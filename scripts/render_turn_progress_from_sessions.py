"""Render the four moments of the turn progress message from real recorded turns.

Reads nanobot's own session event streams (the same `tool_events` the harness
emits live) and prints the message text Discord would show at each moment.
Nothing here invents a command or a timestamp: every command line and every
elapsed figure comes from a recorded turn.
"""

from __future__ import annotations

import glob
import json
import sys
from dataclasses import dataclass

sys.path.insert(0, "/home/evans/agent-hunt/nanobot")

from nanobot.channels.discord.activity import (  # noqa: E402
    Command,
    TurnPhase,
    commands_from_tool_events,
    render_message,
)

SESSIONS = "/home/evans/Coding_Projects/session-files/nanobot-webui/*.jsonl"


@dataclass
class Turn:
    session: str
    turn_id: str
    start_ms: int
    events: list[dict]
    interrupted_at_ms: int | None

    def commands_before(self, at_ms: int) -> list[Command]:
        window = [e for e in self.events if (e.get("created_at_ms") or 0) <= at_ms]
        return commands_from_tool_events(window, turn_started_ms=self.start_ms)

    @property
    def commands(self) -> list[Command]:
        return commands_from_tool_events(self.events, turn_started_ms=self.start_ms)


def load_turns() -> list[Turn]:
    turns: list[Turn] = []
    for path in sorted(glob.glob(SESSIONS)):
        rows = [json.loads(line) for line in open(path) if line.strip()]
        open_turn: str | None = None
        buckets: dict[str, list[dict]] = {}
        order: list[str] = []
        interrupt: dict[str, int] = {}
        for row in rows:
            event = row.get("event")
            if event == "user":
                turn_id = row.get("turn_id")
                if open_turn and turn_id and turn_id != open_turn:
                    interrupt.setdefault(open_turn, row.get("created_at_ms") or 0)
                open_turn = turn_id or open_turn
                if turn_id:
                    buckets.setdefault(turn_id, [])
                    if turn_id not in order:
                        order.append(turn_id)
            elif event == "turn_end":
                open_turn = None
            turn_id = row.get("turn_id")
            if turn_id and event not in {"user", "turn_end"}:
                if turn_id not in buckets:
                    buckets[turn_id] = []
                    order.append(turn_id)
                buckets[turn_id].append(row)
        for turn_id in order:
            events = [e for e in buckets.get(turn_id, []) if e.get("created_at_ms")]
            if not events:
                continue
            start = min(e["created_at_ms"] for e in events)
            turns.append(
                Turn(
                    session=path.rsplit("/", 1)[-1],
                    turn_id=turn_id,
                    start_ms=start,
                    events=sorted(events, key=lambda e: e["created_at_ms"]),
                    interrupted_at_ms=interrupt.get(turn_id),
                )
            )
    return turns


def show(title: str, message: str) -> None:
    print("=" * 78)
    print(title)
    print("=" * 78)
    print(message)
    print(f"[content length: {len(message)} characters]")
    print()


def main() -> None:
    turns = load_turns()

    completed = [t for t in turns if t.interrupted_at_ms is None and len(t.commands) >= 4]
    completed.sort(key=lambda t: (len(t.commands), t.start_ms), reverse=True)
    interrupted = [t for t in turns if t.interrupted_at_ms and t.commands]
    interrupted.sort(key=lambda t: (len(t.commands), t.start_ms), reverse=True)

    if not completed:
        print("no completed turn with >=4 commands found", file=sys.stderr)
        return
    turn = completed[0]
    print(
        f"REAL TURN: session {turn.session}\n"
        f"  turn_id {turn.turn_id}\n"
        f"  {len(turn.commands)} recorded commands, "
        f"{(turn.events[-1]['created_at_ms'] - turn.start_ms) / 1000:.1f}s wall\n"
    )

    first = turn.commands[0]
    show(
        "t = 0 — first command issued",
        render_message(
            TurnPhase.RUNNING,
            [first],
            elapsed_ms=0,
            current_command=first.text,
        ),
    )

    at_10s = turn.start_ms + 10_000
    up_to_10s = turn.commands_before(at_10s)
    current = up_to_10s[-1].text if up_to_10s else first.text
    show(
        "t = 10s — commands issued so far",
        render_message(
            TurnPhase.RUNNING,
            up_to_10s,
            elapsed_ms=min(10_000, turn.events[-1]["created_at_ms"] - turn.start_ms),
            current_command=current,
        ),
    )

    total_ms = turn.events[-1]["created_at_ms"] - turn.start_ms
    show(
        f"t = done — {total_ms / 1000:.1f}s after the turn began",
        render_message(TurnPhase.DONE, turn.commands, elapsed_ms=total_ms),
    )

    if interrupted:
        iturn = interrupted[0]
        requested = iturn.interrupted_at_ms - iturn.start_ms
        # The stop is taken at the first tool boundary after the request.
        after = [
            e["created_at_ms"]
            for e in iturn.events
            if e.get("created_at_ms") and e["created_at_ms"] >= iturn.interrupted_at_ms
        ]
        stopped = (min(after) if after else iturn.events[-1]["created_at_ms"]) - iturn.start_ms
        cmds = iturn.commands_before(iturn.interrupted_at_ms + stopped)
        print(
            f"REAL INTERRUPTED TURN: session {iturn.session}\n"
            f"  turn_id {iturn.turn_id}\n"
            f"  user message at {requested / 1000:.1f}s, "
            f"boundary at {stopped / 1000:.1f}s\n"
        )
        show(
            f"t = draining — {requested / 1000:.1f}s, request in, call still running",
            render_message(
                TurnPhase.DRAINING,
                cmds,
                elapsed_ms=requested,
                current_command=cmds[-1].text if cmds else None,
            ),
        )
        show(
            f"t = interrupted — {stopped / 1000:.1f}s, boundary crossed",
            render_message(
                TurnPhase.INTERRUPTED,
                cmds,
                elapsed_ms=stopped,
                interrupt_requested_ms=requested,
            ),
        )
    else:
        print("no interrupted turn found in the recorded sessions", file=sys.stderr)


if __name__ == "__main__":
    main()
