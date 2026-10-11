"""/invite and /uninvite against an in-process broker with scripted agents (#117, DESIGN.md
§43): the invitation reaches the agent alone, in the room it is in; it shows as pending in the
room it was invited to until it joins; a person can take it back; nothing joins an agent."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from conftest import InProcBroker
from fakes.fake_agent import FakeAgent

from switchboard.config import Config

FAST = Config(human_name="alice").with_delivery(quiet_s=0.0, max_hold_s=0.0)


@pytest.fixture
def broker(tmp_home: Path):
    b = InProcBroker(tmp_home, FAST).start()
    web = b.web_client()
    for r in ("#build", "#review"):
        assert web.post("/api/rooms", json={"name": r}, headers=b.write_headers()).status_code == 200
    b.web = web
    yield b
    web.close()
    b.stop()


def cmd(b: InProcBroker, room: str, text: str) -> Any:
    web = b.web  # type: ignore[attr-defined]
    return web.post(f"/api/rooms/{room}/command", json={"text": text}, headers=b.write_headers())


def pending(b: InProcBroker, room: str) -> list[dict[str, Any]]:
    rooms = b.web.get("/api/rooms").json()["rooms"]  # type: ignore[attr-defined]
    return next(r for r in rooms if r["name"] == room)["settings"]["invites"]


def q(b: InProcBroker, sql: str, *args: Any) -> list[sqlite3.Row]:
    con = sqlite3.connect(f"file:{b.paths.db}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def lines(b: InProcBroker, room: str, kind: str) -> list[sqlite3.Row]:
    """The room's messages of ``kind`` (chat, notice), oldest first."""
    sql = (
        "SELECT m.* FROM messages m JOIN rooms r ON r.id=m.room_id WHERE r.name=? AND m.kind=? ORDER BY m.id"
    )
    return q(b, sql, room, kind)


def delivered_to(b: InProcBroker, message_id: int) -> list[str]:
    sql = (
        "SELECT ms.screen_name FROM deliveries d JOIN memberships ms ON ms.id=d.membership_id"
        " WHERE d.message_id=?"
    )
    return [r["screen_name"] for r in q(b, sql, message_id)]


def members_of(b: InProcBroker, room: str) -> list[str]:
    sql = (
        "SELECT ms.screen_name FROM memberships ms JOIN rooms r ON r.id=ms.room_id"
        " WHERE r.name=? AND ms.left_at IS NULL"
    )
    return [r["screen_name"] for r in q(b, sql, room)]


async def test_an_invited_agent_hears_it_alone_and_joins_itself(broker: InProcBroker) -> None:
    async with FakeAgent(broker.home, "k1") as claude, FakeAgent(broker.home, "k2") as codex:
        await claude.join("#build", "claude-1")
        await codex.join("#build", "codex-1")
        r = cmd(broker, "review", "/invite claude-1 the tax MR")
        assert r.status_code == 200, r.text
        assert (
            r.json()["text"]
            == "asked claude-1 in #build to join #review; it shows as invited here until it does"
        )
        # one message from alice, in #build, delivered to claude-1 alone
        [msg] = lines(broker, "#build", "chat")
        assert msg["sender_name"] == "alice"
        assert msg["text"] == (
            '@claude-1 Please join #review as well: call join("#review", "claude-1"). You stay in #build.'
            "\n\nNote: the tax MR"
        )
        assert delivered_to(broker, msg["id"]) == ["claude-1"]
        assert "join(" in (await claude.read("#build"))["text"]
        # pending in #review, with a notice there; nothing joined
        [p] = pending(broker, "#review")
        assert (p["name"], p["from"], p["by"]) == ("claude-1", "#build", "alice")
        assert members_of(broker, "#review") == []
        assert any(
            "alice invited claude-1 from #build" in x["text"] for x in lines(broker, "#review", "notice")
        )
        # the agent answers by joining: it's in both rooms, and nothing is pending
        await claude.join("#review", "claude-1")
        assert pending(broker, "#review") == []


async def test_a_person_takes_an_invitation_back(broker: InProcBroker) -> None:
    async with FakeAgent(broker.home, "k1") as claude:
        await claude.join("#build", "claude-1")
        assert cmd(broker, "review", "/invite claude-1").status_code == 200
        r = cmd(broker, "review", "/uninvite claude-1")
        assert r.status_code == 200 and r.json()["text"] == "cancelled claude-1's invitation to #review"
        assert pending(broker, "#review") == []
        last = lines(broker, "#build", "chat")[-1]["text"]
        assert last == "@claude-1 Never mind joining #review: the invitation is cancelled."
        r = cmd(broker, "review", "/uninvite claude-1")
        assert r.status_code == 404 and "no pending invitation" in r.json()["message"]


async def test_what_invite_refuses(broker: InProcBroker) -> None:
    web = broker.web  # type: ignore[attr-defined]
    assert web.post("/api/rooms", json={"name": "#ops"}, headers=broker.write_headers()).status_code == 200
    async with (
        FakeAgent(broker.home, "k1") as one,
        FakeAgent(broker.home, "k2") as two,
        FakeAgent(broker.home, "k3") as here,
    ):
        await one.join("#build", "claude-1")
        await two.join("#ops", "claude-1")  # the same name, another agent
        await here.join("#review", "codex-1")
        r = cmd(broker, "review", "/invite claude-1")
        assert r.status_code == 400
        assert r.json()["message"] == (
            "/invite: claude-1 is a different agent in #build, #ops: say which,"
            " as /invite claude-1 from #build"
        )
        assert cmd(broker, "review", "/invite claude-1 from #ops").status_code == 200
        assert [p["from"] for p in pending(broker, "#review")] == ["#ops"]
        r = cmd(broker, "review", "/invite codex-1")
        assert (
            r.status_code == 400
            and r.json()["message"] == "/invite: #review already has a member named codex-1"
        )
        r = cmd(broker, "review", "/invite nobody")
        assert (
            r.status_code == 404
            and r.json()["message"] == "/invite: no agent named nobody in your other rooms"
        )
        # an agent's /invite is a chat message like any other
        said = await here.call("say", room="#review", text="/invite claude-1")
        assert said["ok"] and [p["from"] for p in pending(broker, "#review")] == ["#ops"]
