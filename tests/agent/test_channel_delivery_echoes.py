"""Regression tests for the 2026-10-19 channel-delivery ghost.

A `_channel_delivery` echo persisted mid-turn (bare assistant, no
tool_calls) followed by the runner's own assistant+tool_calls message is a
permanent alternation violation: the adjacent-assistant merge may never
fuse a tool_calls-bearing message, so the pair killed every turn of nime's
main session until fettle excised the ghost by hand. The governor now
absorbs such echoes into the following assistant message; these tests pin
the behavior against the REAL consumer contract — the provider's
`_enforce_role_alternation` validation (the 944a786e standard: no fakes at
the seam).
"""

from __future__ import annotations

import pytest

from nanobot.agent.context_governance import ContextGovernor
from nanobot.providers.base import LLMProvider


def _ghost(text: str) -> dict:
    return {"role": "assistant", "content": text, "_channel_delivery": True}


def _dispatch_shape(tape: list[dict]) -> list[dict]:
    """The real consumer contract: absorb, append the current user turn,
    construct alternation, then pass the provider's own validation.

    Every real dispatch ends with the incoming user message (a bare tape
    ends with an assistant reply, refused as prefill), and the governor
    merges adjacent users — so the current turn is appended BEFORE
    construction, exactly as the runner does.
    """
    governed = ContextGovernor.construct_role_alternation(
        ContextGovernor.absorb_channel_delivery_echoes(tape)
        + [{"role": "user", "content": "(current turn)"}]
    )
    LLMProvider._enforce_role_alternation(governed)
    return governed


def test_echo_before_tool_calling_assistant_is_absorbed():
    """The exact 10-19 ghost shape: echo at N, assistant+tool_calls at N+1."""
    tape = [
        {"role": "user", "content": "draw the lobby"},
        _ghost("MOSS-Image, first render — delivered with image attached."),
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "message", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "delivered"},
        {"role": "assistant", "content": "Here is the render."},
    ]
    governed = _dispatch_shape(tape)

    # The delivery text survives as a visible prefix on the tool-calling
    # message; the tape's own words are untouched.
    tool_msg = next(m for m in governed if m.get("tool_calls"))
    assert "channel delivery" in str(tool_msg["content"])
    assert "MOSS-Image, first render" in str(tool_msg["content"])
    assert not any(m.get("_channel_delivery") for m in governed)


def test_echo_before_user_message_keeps_its_content():
    """Legal alternation: absorb passes echo+user through; the construction
    then folds the echo into the PRIOR assistant reply (its normal merge),
    so the delivery text reaches the model either way."""
    tape = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        _ghost("a delivery the user may be replying to"),
        {"role": "user", "content": "about that message..."},
    ]
    governed = _dispatch_shape(tape)
    assert "a delivery the user may be replying to" in str(
        [m["content"] for m in governed]
    )


def test_consecutive_echoes_absorb_in_order():
    tape = [
        {"role": "user", "content": "go"},
        _ghost("first delivery"),
        _ghost("second delivery"),
        {
            "role": "assistant",
            "content": "done",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "exec", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        {"role": "assistant", "content": "wrapped up"},
    ]
    governed = _dispatch_shape(tape)
    tool_msg = next(m for m in governed if m.get("tool_calls"))
    content = str(tool_msg["content"])
    assert content.index("first delivery") < content.index("second delivery")


def test_end_of_tape_echo_is_kept_and_harmless():
    """A trailing echo stays in the model-facing copy; the incoming user
    turn completes the alternation on the next request."""
    tape = [
        {"role": "user", "content": "evening"},
        _ghost("delivery at close of day"),
    ]
    governed = ContextGovernor.absorb_channel_delivery_echoes(tape)
    assert governed[-1].get("_channel_delivery") is True
    # And once the next user turn arrives, the full shape validates:
    _dispatch_shape(tape)


def test_no_echoes_is_identity():
    tape = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]
    assert ContextGovernor.absorb_channel_delivery_echoes(tape) == tape
