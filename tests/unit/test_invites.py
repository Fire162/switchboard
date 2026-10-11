"""Inviting an agent from another room (#117, DESIGN.md §43): which invitations are pending,
what the invitation says, and how /invite and /uninvite parse and who may run them."""

from __future__ import annotations

from typing import Any

import pytest
from test_commands_review import room

from switchboard import invites
from switchboard.broker.commands import Actor, CommandError, check_role, parse_command, required_role


def ev(ts: float, kind: str, pid: Any, name: str = "claude-1", **extra: Any) -> dict[str, Any]:
    return {
        "ts": ts,
        "kind": kind,
        "data": {"participant": pid, "name": name, "from": "#build", "by": "alice", **extra},
    }


def test_an_invitation_is_pending_until_it_is_answered_cancelled_or_old() -> None:
    now = 1000.0
    events = [
        ev(10.0, "invite", 1),
        ev(20.0, "invite", 2, "codex-1"),
        ev(30.0, "invite_cancel", 2, "codex-1"),
    ]
    assert [p["name"] for p in invites.pending(events, now, set())] == ["claude-1"]
    assert invites.pending(events, now, {1}) == []  # it joined
    assert invites.pending(events, 10.0 + invites.TTL_S, set()) == []  # a day without an answer
    [p] = invites.pending(events, now, set())
    assert p == {"participant": 1, "name": "claude-1", "from": "#build", "by": "alice", "at": 10.0}


def test_a_new_invitation_after_a_cancel_counts_and_moves_to_the_end() -> None:
    events = [
        ev(1.0, "invite", 1),
        ev(2.0, "invite", 2, "codex-1"),
        ev(3.0, "invite_cancel", 1),
        ev(4.0, "invite", 1),
    ]
    assert [(p["name"], p["at"]) for p in invites.pending(events, 5.0, set())] == [
        ("codex-1", 2.0),
        ("claude-1", 4.0),
    ]


@pytest.mark.parametrize("pid", [None, "1", 1.0, True])
def test_an_event_without_a_participant_id_counts_for_nothing(pid: Any) -> None:
    assert invites.pending([ev(1.0, "invite", pid)], 2.0, set()) == []


def test_the_invitation_carries_only_the_rooms_names_and_the_note() -> None:
    assert invites.request_text("claude-1", "#review", "#build", "") == (
        '@claude-1 Please join #review as well: call join("#review", "claude-1"). You stay in #build.'
    )
    assert invites.request_text("claude-1", "#review", "#build", "the tax MR").endswith(
        "\n\nNote: the tax MR"
    )
    assert invites.cancel_text("claude-1", "#review") == (
        "@claude-1 Never mind joining #review: the invitation is cancelled."
    )


@pytest.mark.parametrize(
    ("text", "args"),
    [
        ("/invite claude-1", ("claude-1", "", "")),
        ("/invite @Claude-1 from #Build", ("claude-1", "#build", "")),
        ("/invite claude-1 from #build look at the tax MR", ("claude-1", "#build", "look at the tax MR")),
        ("/invite claude-1 look at the tax MR", ("claude-1", "", "look at the tax MR")),
        ("/invite claude-1 fromage is not a room", ("claude-1", "", "fromage is not a room")),
    ],
)
def test_invite_parses_an_agent_an_optional_room_and_a_note(text: str, args: tuple[str, str, str]) -> None:
    cmd = parse_command(text)
    assert (cmd.name, cmd.args) == ("invite", args)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("/invite", "usage: /invite <agent> [from #room] [note]"),
        ("/invite 1abc", "/invite: not a valid screen name"),
        ("/invite claude-1 from", "/invite: from which room? usage: /invite <agent> [from #room] [note]"),
        ("/invite claude-1 from #no!", "/invite: '#no!' isn't a room name, like #build"),
        ("/invite claude-1 " + "n" * 301, "/invite: the note is too long (301 characters; at most 300)"),
        ("/uninvite", "usage: /uninvite <name>"),
    ],
)
def test_invite_refuses_what_it_cant_read(text: str, message: str) -> None:
    with pytest.raises(CommandError) as e:
        parse_command(text)
    assert e.value.message == message


def test_inviting_needs_the_web_and_uninviting_doesnt() -> None:
    """An invitation wakes an agent to bring it into another room: a person's, in the web, like
    raising activity. Taking one back only lowers it."""
    assert required_role(parse_command("/invite claude-1"), room()) == "human"
    assert required_role(parse_command("/uninvite claude-1"), room()) == "human_cli"


def test_the_cli_is_told_to_use_the_web() -> None:
    with pytest.raises(CommandError) as e:
        check_role(parse_command("/invite claude-1"), room(), Actor(role="human_cli", via="cli"))
    assert (e.value.code, e.value.message) == (
        "forbidden",
        "/invite needs your web session: type it in the switchboard web UI",
    )
    check_role(parse_command("/uninvite claude-1"), room(), Actor(role="human_cli", via="cli"))
