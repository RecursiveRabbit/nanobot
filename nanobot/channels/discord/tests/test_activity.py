"""Rows for the turn progress message."""

from __future__ import annotations

import re

import pytest

from nanobot.channels.discord.activity import (
    COMMAND_MAX,
    DISCORD_CONTENT_LIMIT,
    ZWSP,
    Command,
    TurnPhase,
    commands_from_tool_events,
    filter_clauses,
    format_elapsed,
    interruption_marker,
    label,
    neutralise,
    render_block,
    render_message,
    truncate_command,
)


def _visible(text: str) -> str:
    """What a reader sees: the rendering with the invisible escapes removed."""
    return text.replace(ZWSP, "")


# --- the body cannot change the shape of its container ---------------------


def test_command_with_spoiler_and_fence_stays_inside_one_spoiler():
    """A command containing both ``||`` and a fence renders inside ONE spoiler,
    and the visible text still reads as the command that ran."""
    nasty = "echo '||not a spoiler||' && printf '```'; grep -n x || true"
    message = render_message(
        TurnPhase.RUNNING, [Command(nasty)], elapsed_ms=0, current_command=nasty
    )

    # Exactly two spoiler delimiters: the ones the renderer wrote.
    assert message.count("||") == 2, message

    # No terminable fence in the *content* (the renderer's own two fences are the
    # container, not the content).
    inner = message.split("||")[1]
    assert inner.startswith("```\n") and inner.endswith("\n```"), inner
    assert "```" not in inner[4:-4]

    # And the reader still sees the command that ran, verbatim — once in the
    # label (readable without opening the spoiler) and once in the block.
    assert _visible(message).count(nasty) == 2
    assert _visible(inner).count(nasty) == 1


def test_neutralise_preserves_visible_characters():
    for raw in ("a || b", "```x```", "||||", "````````", "plain command"):
        out = neutralise(raw)
        assert _visible(out) == raw
        assert not re.search(r"`{3}", out)
        assert "||" not in out


def test_newline_in_command_cannot_add_a_log_line():
    message = render_message(
        TurnPhase.RUNNING, [Command("echo one\necho two")], elapsed_ms=0
    )
    body = message.split("```")[1]
    assert "echo one echo two" in body
    assert body.count("\n") <= 3  # blank padding + one command line + fence close


# --- the scroll count is derived, never incremented ------------------------


def test_scrolled_count_is_total_minus_shown():
    commands = [Command(f"cmd-{i:03d} " + "x" * 20) for i in range(40)]
    block, shown = render_block(commands, budget=400)

    assert 0 < shown < len(commands)
    dropped = len(commands) - shown
    assert f"… {dropped} earlier commands not shown" in block

    # The window is the most recent commands, in chronological order.
    body = [ln for ln in block.split("\n") if ln.startswith("cmd-")]
    assert [ln.split()[0] for ln in body] == [
        f"cmd-{i:03d}" for i in range(len(commands) - shown, len(commands))
    ]


def test_count_tracks_the_budget_rather_than_a_running_total():
    """Two renders of the same list at different budgets agree with themselves."""
    commands = [Command(f"cmd-{i:03d} " + "y" * 20) for i in range(30)]
    for budget in (300, 600, 2000):
        block, shown = render_block(commands, budget=budget)
        assert shown <= len(commands)
        if shown < len(commands):
            assert f"… {len(commands) - shown} earlier commands not shown" in block


# --- the label may only name what the block shows -------------------------


def test_untraceable_label_clause_is_dropped():
    commands = [Command("git diff --stat d045a06 b3361de"), Command("rspec test/services/hub")]
    kept, dropped = filter_clauses(
        [
            "compared the branch head against main",
            "worked diligently and carefully",
            "ran the hub test suite",
        ],
        commands,
    )
    assert "compared the branch head against main" in dropped
    assert "worked diligently and carefully" in dropped
    assert kept == ["ran the hub test suite"]


def test_summary_with_no_anchor_falls_back_to_the_mechanical_label():
    message = render_message(
        TurnPhase.RUNNING,
        [Command("grep -n OPERATORS config.rb")],
        elapsed_ms=10_000,
        current_command="grep -n OPERATORS config.rb",
        summary="worked diligently",
    )
    assert "worked diligently" not in message
    assert "**Working**" in message
    assert "`grep -n OPERATORS config.rb`" in message


def test_traceable_summary_replaces_the_word_but_not_the_facts():
    message = render_message(
        TurnPhase.RUNNING,
        [Command("rspec test/services/hub")],
        elapsed_ms=10_000,
        summary="ran the hub test suite",
    )
    assert "**ran the hub test suite**" in message
    assert "1 call" in message and "10s" in message


# --- the four moments -----------------------------------------------------


def test_t0_first_command_just_issued():
    message = render_message(
        TurnPhase.RUNNING,
        [Command("git log --oneline -3")],
        elapsed_ms=0,
        current_command="git log --oneline -3",
    )
    assert message.startswith(
        "▸ **Working** — `git log --oneline -3` · 1 call · 0s"
    )
    assert message.count("```") == 2


def test_t10s_five_commands():
    commands = [
        Command("git log --oneline -3"),
        Command("git show b3361de:app/services/hub/config.rb"),
        Command("git grep -n reload_identities b3361de -- app config lib test"),
        Command("grep -n OPERATORS app/services/hub/config.rb"),
        Command("rspec test/services/hub"),
    ]
    message = render_message(
        TurnPhase.RUNNING,
        commands,
        elapsed_ms=10_000,
        current_command="rspec test/services/hub",
    )
    assert "▸ **Working** — `rspec test/services/hub` · 5 calls · 10s" in message
    assert "git grep -n reload_identities" in message


def test_draining_does_not_claim_interruption():
    """Between the user's message and the call returning, the turn is draining:
    'interrupted' would be a claim about the future."""
    draining = label(TurnPhase.DRAINING, calls=8, elapsed_ms=23_000)
    assert "Stopping after this call" in draining
    assert "interrupted" not in draining.lower()

    interrupted = label(TurnPhase.INTERRUPTED, calls=9, elapsed_ms=26_000)
    assert "Interrupted by user message" in interrupted


def test_interrupted_marker_carries_the_request_time_at_the_boundary():
    """The marker's *position* is the boundary; its *text* carries the request time,
    so the log's chronology survives an interruption."""
    marker = interruption_marker(23_000, 26_000)
    assert marker == (
        "— Turn interrupted by user message (requested 00:23, stopped 00:26) —"
    )


def test_interrupted_turn_writes_the_marker_at_the_boundary():
    commands = [Command(f"cmd-{i}") for i in range(9)]
    message = render_message(
        TurnPhase.INTERRUPTED,
        commands,
        elapsed_ms=26_000,
        interrupt_requested_ms=23_000,
    )
    assert "■ **Interrupted by user message** — 9 calls · 26s" in message
    body = message.split("```")[1].strip().split("\n")
    assert body[-1].startswith("— Turn interrupted by user message")
    assert body[-2] == "cmd-8"


def test_done_label_and_separate_answer():
    message = render_message(
        TurnPhase.DONE, [Command(f"cmd-{i}") for i in range(13)], elapsed_ms=134_000
    )
    assert message.startswith("✓ **Done** — 13 calls · 2m14s")


# --- mechanics the four constraints force --------------------------------


def test_message_never_exceeds_discord_limit():
    commands = [Command("z" * 200) for _ in range(200)]
    for phase in TurnPhase:
        message = render_message(phase, commands, elapsed_ms=3_600_000)
        assert len(message) <= DISCORD_CONTENT_LIMIT, (phase, len(message))


def test_command_is_cut_marked_and_never_silently():
    long = "x" * (COMMAND_MAX + 50)
    cut = truncate_command(long)
    assert cut.endswith("…")
    assert len(cut) <= COMMAND_MAX


def test_elapsed_is_never_finer_than_the_edit_cadence():
    assert format_elapsed(0) == "0s"
    assert format_elapsed(1_999) == "0s"
    assert format_elapsed(9_400) == "8s"
    assert format_elapsed(59_000) == "58s"
    assert format_elapsed(60_000) == "1m"
    assert format_elapsed(134_000) == "2m14s"
    assert format_elapsed(134_001) == "2m14s"  # floored to the cadence
    assert format_elapsed(3_600_000) == "1h00m"


def test_pluralisation():
    assert "1 call ·" in label(TurnPhase.RUNNING, calls=1, elapsed_ms=0)
    assert "2 calls ·" in label(TurnPhase.RUNNING, calls=2, elapsed_ms=0)


# --- reading the harness's own event stream -------------------------------


def test_commands_come_from_start_events_and_never_carry_results():
    events = [
        {
            "phase": "start",
            "name": "exec",
            "arguments": {"command": "git show b3361de:config.rb"},
            "result": None,
        },
        {
            "phase": "end",
            "name": "exec",
            "arguments": {"command": "git show b3361de:config.rb"},
            "result": "SECRET RESULT BODY",
        },
        {
            "phase": "start",
            "name": "read_session",
            "arguments": {"session_key": "websocket:abc"},
            "result": None,
        },
    ]
    commands = commands_from_tool_events(events, turn_started_ms=1_000)

    assert [c.text for c in commands] == [
        "git show b3361de:config.rb",
        "read_session(session_key='websocket:abc')",
    ]
    message = render_message(TurnPhase.RUNNING, commands, elapsed_ms=5_000)
    assert "SECRET RESULT BODY" not in message


@pytest.mark.parametrize("phase", list(TurnPhase))
def test_every_phase_renders_a_label_and_a_block(phase):
    message = render_message(phase, [Command("ls")], elapsed_ms=2_000)
    head, _, block = message.partition("\n\n")
    assert head and block.startswith("||```")
