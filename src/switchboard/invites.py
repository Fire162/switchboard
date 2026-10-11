"""Inviting an agent from another room (#117, DESIGN.md §43): pure policy, no I/O.

A person's ``/invite`` asks an agent that is in another of their rooms to join this one too:
one message from that person, posted in a room the agent is already in and delivered to that
agent alone. The agent joins itself, with its own ``join()``; nothing joins it behind its back.
Until it does, the invitation is pending, read from the room's ``invite`` events. It ends when
the agent joins, when a person cancels it (an ``invite_cancel`` event), or after ``TTL_S``.
"""

from __future__ import annotations

from typing import Any

TTL_S = 24 * 3600.0  # an invitation nobody answered for a day is dropped
MAX_NOTE = 300


def pending(events: list[dict[str, Any]], now: float, members: set[int]) -> list[dict[str, Any]]:
    """The room's open invitations, oldest first. ``events``: its ``invite`` and
    ``invite_cancel`` events, oldest first, as ``{ts, kind, data}``; ``members``: the
    participant ids in the room now. A newer invitation for the same agent replaces the older."""
    last: dict[int, dict[str, Any]] = {}
    for e in events:
        d = e.get("data") or {}
        pid = d.get("participant")
        if type(pid) is not int:
            continue
        if e.get("kind") == "invite":
            last.pop(pid, None)  # re-inserted: an invitation sent again moves to the end
            last[pid] = {"ts": e.get("ts", 0.0), **d}
        elif e.get("kind") == "invite_cancel":
            last.pop(pid, None)
    return [
        {"participant": pid, "name": v.get("name"), "from": v.get("from"), "by": v.get("by"), "at": v["ts"]}
        for pid, v in last.items()
        if pid not in members and now - v["ts"] < TTL_S
    ]


def request_text(name: str, room: str, source: str, note: str) -> str:
    """The invitation: switchboard's words, the room's name and the person's note, nothing from
    either room's conversation (#117: no text crosses rooms)."""
    text = f'@{name} Please join {room} as well: call join("{room}", "{name}"). You stay in {source}.'
    return text + (f"\n\nNote: {note}" if note else "")


def cancel_text(name: str, room: str) -> str:
    return f"@{name} Never mind joining {room}: the invitation is cancelled."
