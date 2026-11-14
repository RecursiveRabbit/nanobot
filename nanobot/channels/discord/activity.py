"""Turn progress message: one edited Discord message that shows what a turn is doing.

The message has two channels with different jobs, and that split is the whole
design:

* the **label** is always visible, one line, and carries every volatile fact —
  phase, current command, call count, elapsed time;
* the **block** is a spoiler holding an append-only list of commands.

A Discord spoiler re-folds on every edit, so a reader cannot see inside it while
the turn is running. Therefore:

    All volatile state lives in the label; the block is append-only and
    structurally stable, so a re-fold hides nothing that moved.

The block lists **commands only** — never tool results. Outcomes belong in the
agent's answer, where they can be argued with. Because the block never saw a
result, a summary sentence cannot report one, and every clause the label asserts
can be checked by a reader against a line one row down. A clause with nothing to
check it against is dropped, *including when the model is right and the block is
merely sparse*: a true sentence a reader cannot verify is still not allowed.

The scroll notice is **derived, never incremented**:

    "N earlier commands not shown" is total - shown. An accumulator is wrong by
    exactly the amount truncated, silently.

Escaping is the renderer's job, not the model's. A command containing ``||`` or a
backtick fence must not be able to close or restructure the spoiler it sits in;
the visible text must still read as the command that ran. Both are handled by
inserting U+200B (zero width space), which is invisible to the reader and does
not depend on Discord's escape semantics — unlike a backslash, which would be
rendered literally inside a code fence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Sequence

#: Discord's hard content limit for a message edit.
DISCORD_CONTENT_LIMIT = 2000

#: A single command line is cut here, marked with an ellipsis, never silently.
COMMAND_MAX = 120

#: At most this many lines are shown, whichever binds first with the character
#: budget. "The last dozen" is the readable window; the budget is the hard one.
MAX_LINES = 12

#: The minimum interval between edits. The label may never display a precision
#: finer than this, or a long turn becomes hundreds of edits saying "still going".
EDIT_CADENCE_MS = 2000

ZWSP = "\u200b"

_PHASE_LABEL = {
    "running": ("▸", "Working"),
    "draining": ("◼", "Stopping after this call"),
    "interrupted": ("■", "Interrupted by user message"),
    "done": ("✓", "Done"),
    "failed": ("✗", "Failed"),
}


class TurnPhase(str, Enum):
    RUNNING = "running"
    DRAINING = "draining"
    INTERRUPTED = "interrupted"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class Command:
    """One command line, recorded when it was *issued* rather than when it returned.

    Writing at issue time is what makes a twelve-minute call visible for twelve
    minutes. The consequence, stated so nobody has to infer it: every line above
    the last is known to have returned; the last may still be running, and the
    label says whether it is.
    """

    text: str
    elapsed_ms: int = 0


def neutralise(text: str) -> str:
    """Make *text* unable to change the shape of the container it sits in.

    Two tokens can escape a Discord spoiler or code fence: the spoiler delimiter
    ``||`` and a run of three or more backticks. Both are split with a zero width
    space, which renders invisibly — the reader still sees exactly the command
    that ran, and a copy-paste loses nothing that was visible.
    """
    if not text:
        return text
    # Every run of two or more pipes is split, so no ``||`` survives — including
    # the seam between two adjacent replacements, which a naive replace creates.
    text = re.sub(r"\|{2,}", lambda m: ZWSP.join(m.group(0)), text)
    # Every backtick in a run of three or more is split, so no terminable fence
    # survives anywhere in the run — not just at its third character.
    text = re.sub(r"`{3,}", lambda m: ZWSP.join(m.group(0)), text)
    return text


def truncate_command(text: str, limit: int = COMMAND_MAX) -> str:
    """Cut a command at *limit* characters, marked, never silently."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rstrip() + "…"


def format_elapsed(elapsed_ms: int, cadence_ms: int = EDIT_CADENCE_MS) -> str:
    """Render elapsed time no finer than the edit cadence.

    Under a minute: whole seconds floored to the cadence. Past it: whole minutes.
    A label that ticks every second while the message can only be edited every
    two is a label describing a state the reader never sees.
    """
    ms = max(0, int(elapsed_ms))
    if cadence_ms > 0:
        ms -= ms % cadence_ms
    total_s = ms // 1000
    if total_s < 60:
        return f"{total_s}s"
    minutes, seconds = divmod(total_s, 60)
    if minutes < 60:
        return f"{minutes}m{seconds:02d}s" if seconds else f"{minutes}m"
    return f"{minutes // 60}h{minutes % 60:02d}m"


def format_clock(elapsed_ms: int) -> str:
    """mm:ss since the turn started — the clock the block's marker uses."""
    total_s = max(0, int(elapsed_ms)) // 1000
    return f"{total_s // 60:02d}:{total_s % 60:02d}"


def interruption_marker(requested_ms: int, stopped_ms: int) -> str:
    """The line written at the tool boundary when a turn is stopped.

    Its *position* in the block is the boundary — the line above it therefore has
    positive evidence that it returned. Its *text* says when the stop was asked
    for, so a reader can see how long the drain took, which is the difference
    between "it wouldn't stop" and "it finished the call it was in".
    """
    return (
        "— Turn interrupted by user message "
        f"(requested {format_clock(requested_ms)}, stopped {format_clock(stopped_ms)}) —"
    )


def label(
    phase: TurnPhase | str,
    *,
    calls: int,
    elapsed_ms: int,
    current_command: str | None = None,
    summary: str | None = None,
) -> str:
    """The always-visible line. Everything volatile goes here.

    ``draining`` is deliberately a separate phase from ``interrupted``: a stop is
    only taken at a tool boundary, so between the user's message and the call
    returning the turn has not been interrupted yet. Writing "interrupted" at
    arrival would hold a claim about the future for as long as that call runs.
    """
    key = phase.value if isinstance(phase, TurnPhase) else str(phase)
    icon, word = _PHASE_LABEL.get(key, ("·", "Working"))
    text = f"**{summary}**" if summary else f"**{word}**"
    noun = "call" if calls == 1 else "calls"
    detail = f"{calls} {noun} · {format_elapsed(elapsed_ms)}"
    if key == "running" and current_command:
        rendered = neutralise(truncate_command(current_command))
        # Inline-code wrapping is a container too: a backtick in the command would
        # close it early and spill the rest of the line into markdown. When the
        # command contains one, it is shown unwrapped rather than wrapped and broken.
        shown_cmd = f"`{rendered}`" if "`" not in rendered else rendered
        detail = f"{shown_cmd} · {detail}"
    return f"{icon} {text} — {detail}"


#: Words that anchor nothing. A clause is only "traceable" if it shares a token
#: that actually refers to something the block shows.
_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "from", "into", "that", "this", "these",
        "those", "are", "was", "were", "has", "have", "had", "not", "but",
        "its", "then", "than", "over", "under", "also", "after", "before",
        "ran", "run", "did", "does", "done", "been", "being", "all", "any",
    }
)


def _tokens(text: str) -> set[str]:
    """Lowercased word tokens used to anchor a label clause to a command.

    Only letters, digits and underscore hold a token together; every other
    character separates. So ``test/services/hub`` yields three anchors rather
    than one unquotable slug, and a clause that mentions ``hub`` can be checked
    by a reader against a path they can see.
    """
    out: set[str] = set()
    word: list[str] = []
    for ch in text.lower():
        if ch.isalnum() or ch == "_":
            word.append(ch)
        else:
            if len(word) >= 3:
                token = "".join(word)
                if token not in _STOPWORDS:
                    out.add(token)
            word = []
    if len(word) >= 3:
        token = "".join(word)
        if token not in _STOPWORDS:
            out.add(token)
    return out


def filter_clauses(
    clauses: Iterable[str], commands: Sequence[Command]
) -> tuple[list[str], list[str]]:
    """Keep only clauses a reader can trace to a command line.

    Returns ``(kept, dropped)``. The test is deliberately crude — a shared token
    of three characters or more — because the guarantee wanted here is only the
    one that can be stated: a clause with no anchor at all must not ship. A
    heuristic can admit a wrong clause that reuses a token; it cannot admit a
    clause the block gives a reader no way to check.
    """
    anchors: set[str] = set()
    for command in commands:
        anchors |= _tokens(command.text)
    kept: list[str] = []
    dropped: list[str] = []
    for clause in clauses:
        sentence = clause.strip()
        if not sentence:
            continue
        if _tokens(sentence) & anchors:
            kept.append(sentence)
        else:
            dropped.append(sentence)
    return kept, dropped


def _fence(inner: str) -> str:
    return "```\n" + inner + "\n```"


def render_block(
    commands: Sequence[Command],
    *,
    budget: int,
    limit: int = COMMAND_MAX,
    trailer: str | None = None,
) -> tuple[str, int]:
    """Render the spoiler block within *budget* characters.

    Returns ``(block, shown)``. The window always keeps the **most recent**
    commands in chronological order; the dropped count is computed from
    ``len(commands) - shown``, never carried along, so it stays right no matter
    how the budget moves.
    """
    total = len(commands)
    rendered: list[str] = []
    used = 0

    # Reserve room for the scroll notice before filling, so a count that only
    # appears once the window is full cannot push the message over the limit.
    reserve = len(f"… {total} earlier commands not shown") + 1

    for command in reversed(commands):
        if len(rendered) >= MAX_LINES:
            break
        line = neutralise(truncate_command(command.text, limit))
        cost = len(line) + 1
        if used + cost + reserve > budget and rendered:
            break
        if used + cost > budget + reserve:
            break
        rendered.append(line)
        used += cost

    rendered.reverse()
    shown = len(rendered)
    dropped = total - shown
    body: list[str] = []
    if dropped > 0:
        body.append(f"… {dropped} earlier commands not shown")
    body.extend(rendered)
    if trailer:
        body.append(trailer)
    return f"||{_fence(chr(10).join(body))}||", shown


def render_message(
    phase: TurnPhase | str,
    commands: Sequence[Command],
    *,
    elapsed_ms: int,
    current_command: str | None = None,
    summary: str | None = None,
    interrupt_requested_ms: int | None = None,
    limit: int = DISCORD_CONTENT_LIMIT,
) -> str:
    """The whole message as Discord will show it.

    On an interrupted turn the marker is appended at the boundary and carries the
    request time, so the log's own chronology survives: the line's *position* is
    the boundary, its *text* says when the stop was asked for.
    """
    key = phase.value if isinstance(phase, TurnPhase) else str(phase)
    if summary:
        kept, _dropped = filter_clauses([summary], commands)
        summary = " ".join(kept) or None

    head = label(
        key,
        calls=len(commands),
        elapsed_ms=elapsed_ms,
        current_command=current_command if key == "running" else None,
        summary=summary,
    )
    # Room for the head, a blank line, the spoiler markers, and the fence.
    block_budget = limit - len(head) - 2 - len("||||") - len("```\n\n```")
    marker = None
    if key == "interrupted" and interrupt_requested_ms is not None:
        marker = interruption_marker(interrupt_requested_ms, elapsed_ms)
        block_budget -= len(marker) + 1
    block, _shown = render_block(
        commands, budget=max(0, block_budget), limit=COMMAND_MAX, trailer=marker
    )
    return f"{head}\n\n{block}"


def commands_from_tool_events(
    events: Iterable[dict[str, Any]],
    *,
    turn_started_ms: int | None = None,
) -> list[Command]:
    """Build the command list from the harness's tool-event stream.

    Accepts either bare tool events (``{"phase": "start", ...}``) or persisted
    session rows that wrap them (``{"created_at_ms": ..., "tool_events": [...]}``),
    which is the shape ``session/*.jsonl`` actually stores.

    Only ``start`` events are read, and only the command argument is kept.
    Results are never captured here — the block does not show them, so neither
    this module nor anything downstream of it holds one.
    """
    out: list[Command] = []
    for row in events:
        if not isinstance(row, dict):
            continue
        nested = row.get("tool_events")
        if isinstance(nested, list):
            at = row.get("created_at_ms")
            for event in nested:
                out.extend(_commands_from_one(event, at, turn_started_ms))
        else:
            out.extend(_commands_from_one(row, row.get("at_ms"), turn_started_ms))
    return out


def _commands_from_one(
    event: dict[str, Any], at_ms: int | None, started_ms: int | None = None
) -> list[Command]:
    if event.get("phase") != "start":
        return []
    args = event.get("arguments") or {}
    text = ""
    if isinstance(args, dict):
        for key in ("command", "cmd", "script"):
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                text = value
                break
        if not text:
            text = _summarise_arguments(event.get("name") or "", args)
    if not text:
        return []
    at = event.get("at_ms", at_ms)
    elapsed = int(at - started_ms) if (at is not None and started_ms is not None) else 0
    return [Command(text=text, elapsed_ms=elapsed)]


def _summarise_arguments(name: str, args: dict[str, Any]) -> str:
    """A non-shell tool call, rendered as name(key=value, …)."""
    parts: list[str] = []
    for key, value in args.items():
        if isinstance(value, (str, int, float, bool)):
            rendered = truncate_command(str(value), 48)
            parts.append(f"{key}={rendered!r}")
    return f"{name}({', '.join(parts)})" if name else ""
