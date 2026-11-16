"""The length-recovery ladder must not ask a tool call to continue as prose.

Reported by the desk 2026-11-15 (the-townline/requests/
2026-11-15-from-nime-the-length-ladder-cannot-continue-a-tool-call.md). The
mechanism, read from lark's and dova's own records and from runner.py:

  a response arrives with ``finish_reason="length"`` and one tool call, ``content``
  empty; the tool-call gate refused it (length was not a tool-capable stop), and
  the ladder then answered with LENGTH_RECOVERY_PROMPT — *continue the same
  response from its exact endpoint*. There was no prose to continue, so the model
  re-emitted the same call. Three rounds, budget spent, and the turn ended as
  EMPTY_FINAL_RESPONSE_MESSAGE:

      assistant len=0 tc=1   ← the call, dropped
      user      …cut off. Continue the same response from its exact endpoint…  ×3
      assistant I completed the tool steps but couldn't produce a final answer.

That sentence is true about the ladder and false about the resident. lark sat 76
hours behind it; dova's fold died the same morning.

The four rows are the desk's; the last one is the mutation that proves rows 1 and
2 are load-bearing rather than decorative (run by hand, not in CI — see the note
at the bottom).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.runner_helpers import make_run_spec
from nanobot.config.schema import AgentDefaults
from nanobot.providers.base import (
    LLMProvider,
    LLMResponse,
    ToolCallRequest,
)

_MAX_TOOL_RESULT_CHARS = AgentDefaults().max_tool_result_chars


def _tools() -> MagicMock:
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(return_value="tool output")
    return tools


def test_arguments_complete_is_evidence_not_a_guess() -> None:
    """The discriminator the fix rests on.

    ``parse_tool_arguments`` keeps malformed JSON as the raw string it was given,
    so a stream cut mid-arguments arrives as ``str`` and a whole call arrives as
    ``dict``. If that ever stops being true, the gate below starts executing
    truncated calls and the rows that follow stop meaning anything.
    """
    whole = ToolCallRequest(id="c1", name="read_file", arguments={"path": "x"})
    cut = ToolCallRequest(id="c2", name="read_file", arguments='{"path": "x"')
    argless = ToolCallRequest(id="c3", name="ping", arguments={})
    nameless = ToolCallRequest(id="c4", name="", arguments={"path": "x"})

    assert whole.arguments_complete() is True
    assert cut.arguments_complete() is False
    assert argless.arguments_complete() is True
    assert nameless.arguments_complete() is False

    assert whole_finish_is_executable(whole) is True
    assert whole_finish_is_executable(cut) is False


def whole_finish_is_executable(call: ToolCallRequest) -> bool:
    response = LLMResponse(content="", tool_calls=[call], finish_reason="length")
    return response.should_execute_tools


@pytest.mark.asyncio
async def test_a_truncated_response_with_a_whole_tool_call_executes_it():
    """Row 1. The call is usable, so the ladder must not throw it away."""
    from nanobot.agent.runner import AgentRunner

    provider = MagicMock(spec=LLMProvider)
    provider.chat_stream_with_retry = AsyncMock(side_effect=[
        LLMResponse(
            content="",
            finish_reason="length",
            tool_calls=[ToolCallRequest(id="call_1", name="read_file", arguments={"path": "baton.json"})],
        ),
        LLMResponse(content="read it, done", finish_reason="stop"),
    ])
    tools = _tools()

    result = await AgentRunner().run(make_run_spec(
        provider,
        initial_messages=[{"role": "user", "content": "read the baton"}],
        tools=tools,
        model="test-model",
        max_iterations=5,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    ))

    tools.execute.assert_awaited_once()
    user_msgs = [m.get("content") or "" for m in result.messages if m.get("role") == "user"]
    assert user_msgs == ["read the baton"], (
        "the harness must inject nothing into the conversation (Evans 2026-11-15)"
    )
    assert result.final_content == "read it, done"
    assert result.stop_reason == "completed"  # the runner maps a clean stop


@pytest.mark.asyncio
async def test_a_truncated_response_with_a_cut_tool_call_names_the_cap():
    """Row 2. The arguments did not survive, so the call cannot be executed — and
    the turn must say *that*, not 'I couldn't produce a final answer'."""
    from nanobot.agent.runner import AgentRunner
    from nanobot.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE, TRUNCATED_TOOL_CALL_MESSAGE

    provider = MagicMock(spec=LLMProvider)
    provider.chat_stream_with_retry = AsyncMock(return_value=LLMResponse(
        content="",
        finish_reason="length",
        tool_calls=[ToolCallRequest(id="call_1", name="read_file", arguments='{"path": "bat')],
    ))
    tools = _tools()

    result = await AgentRunner().run(make_run_spec(
        provider,
        initial_messages=[{"role": "user", "content": "read the baton"}],
        tools=tools,
        model="test-model",
        max_iterations=5,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    ))

    tools.execute.assert_not_awaited()
    assert result.stop_reason == "truncated_tool_call"
    assert result.stop_detail == "length"
    # Placement (Evans 2026-11-15): the fact about the call is in the call's result,
    # and the assistant's channel says nothing.
    assert result.final_content == ""
    tool_text = " ".join(
        m.get("content") or "" for m in result.messages if m.get("role") == "tool"
    )
    assert TRUNCATED_TOOL_CALL_MESSAGE in tool_text
    assert "output limit" in tool_text
    # The prose ladder never ran: no continuation prompt, and no three-round retry.
    assert provider.chat_stream_with_retry.await_count == 1


@pytest.mark.asyncio
async def test_a_truncated_response_with_text_ends_the_turn_at_the_cap():
    """Row 3, the control — and it is a different control than it used to be.

    Prose truncation used to be the ladder's territory: append the partial text,
    inject a continuation prompt, loop. That prompt is deleted (Evans 2026-11-15),
    so the control is now that the turn ENDS: the text produced is returned, the
    record says `truncated` / `length`, and nothing is said into the conversation.
    """
    from nanobot.agent.runner import AgentRunner

    provider = MagicMock(spec=LLMProvider)
    provider.chat_stream_with_retry = AsyncMock(side_effect=[
        LLMResponse(content="first part ", finish_reason="length"),
        LLMResponse(content="second part", finish_reason="stop"),
    ])

    result = await AgentRunner().run(make_run_spec(
        provider,
        initial_messages=[{"role": "user", "content": "give a long answer"}],
        tools=_tools(),
        model="test-model",
        max_iterations=5,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    ))

    assert result.final_content == "first part"
    assert result.stop_reason == "truncated"
    assert result.stop_detail == "length"
    assert provider.chat_stream_with_retry.await_count == 1, "no continuation round"
    user_msgs = [m.get("content") or "" for m in result.messages if m.get("role") == "user"]
    assert user_msgs == ["give a long answer"], (
        "the harness injected a continuation prompt; it is deleted, not reworded"
    )


# Row 4, the mutation, run by hand on 2026-11-15 rather than committed as a test
# (mutating source inside a test is how a suite starts lying to itself). Both were
# applied, run, and reverted; here is what each one actually caught:
#
#   (a) revert `should_execute_tools` to the three-reason set
#       → `test_arguments_complete_is_evidence_not_a_guess` and
#         `test_a_truncated_response_with_a_whole_tool_call_executes_it` fail:
#         "Ignoring tool calls under finish_reason='length'", then the prose ladder.
#   (b) drop `and not response.has_tool_calls` from the ladder guard
#       → `test_a_truncated_response_with_a_cut_tool_call_names_the_cap` fails:
#         the cut call goes into the ladder, "Output truncated on turn 2 (3/3)",
#         and the turn ends as empty_final_response — the field defect.
#
# Row 1 is caught by (a) alone and row 2 by (b) alone. Neither mutation is caught
# by both, which is why both rows exist.


@pytest.mark.asyncio
async def test_the_3220_injection_terminates_at_the_bound_and_never_reexecutes():
    """Condition from the desk, 2026-11-15: prove #3220 is shut, don't cite the bound.

    #3220 was the infinite empty tool-call loop: a *gateway* injecting tool_calls
    under an anomalous finish reason, executed forever. `length` was in that blocked
    set, and this change moved it out — so the protection is now the count, and a
    count nobody has driven against the injection it replaced is a promise.

    At this layer an injected call and the model's own call are the same object: a
    response with ``finish_reason="length"``, whole arguments, and nothing else
    distinguishing them. That is exactly why the bound is the right shape — there is
    no provenance to check, so the loop must be closed by termination.

    The fixture is the worst case: every single response is truncated and carries a
    call with whole arguments, and max_iterations is far above the bound so it
    cannot be what stops it.
    """
    from nanobot.agent import runner as runner_module
    from nanobot.agent.runner import AgentRunner
    from nanobot.utils.runtime import TRUNCATED_TOOL_CALL_MESSAGE

    bound = runner_module._MAX_LENGTH_TOOL_CALL_EXECUTIONS
    endless = LLMResponse(
        content="",
        finish_reason="length",
        tool_calls=[ToolCallRequest(id="injected", name="read_file", arguments={"path": "baton.json"})],
    )

    provider = MagicMock(spec=LLMProvider)
    provider.chat_stream_with_retry = AsyncMock(return_value=endless)
    tools = _tools()

    result = await AgentRunner().run(make_run_spec(
        provider,
        initial_messages=[{"role": "user", "content": "fold"}],
        tools=tools,
        model="test-model",
        max_iterations=50,          # 50 >> the bound: the bound must be the stopper
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    ))

    # Executed exactly `bound` times, then refused — never a bound+1.
    assert tools.execute.await_count == bound, (
        f"expected {bound} executions before the cap, got {tools.execute.await_count}"
    )
    # The turn ended because of the cap, not because it ran out of iterations.
    assert result.stop_reason == "truncated_tool_call", result.stop_reason
    tool_text = " ".join(
        m.get("content") or "" for m in result.messages if m.get("role") == "tool"
    )
    assert TRUNCATED_TOOL_CALL_MESSAGE in tool_text
    # One request per execution plus the one that hit the cap: it never loops the
    # ladder either, so there is no second road back into another request.
    assert provider.chat_stream_with_retry.await_count == bound + 1


@pytest.mark.asyncio
async def test_the_bound_terminates_the_loop_3220_was_blocking():
    """Condition from the desk: #3220's protection is now a bound, so drive it.

    #3220 was "a gateway injecting tool_calls under an anomalous finish_reason
    causes execution, and that is an infinite empty tool-call loop". `length` left
    that blocked set on 2026-11-15, and the protection moved into the runner. A
    bound nobody has driven is a promise, so this drives it with the worst case
    available from inside the harness: every response truncated, every call whole,
    forever. Nothing here is model-earned — it is the injection shape.

    Acceptance: exactly _MAX_LENGTH_TOOL_CALL_EXECUTIONS executions, then the cap
    sentence, and no further execution however many times the model asks.
    """
    from nanobot.agent import runner as runner_module
    from nanobot.agent.runner import AgentRunner
    from nanobot.utils.runtime import TRUNCATED_TOOL_CALL_MESSAGE

    looping = LLMResponse(
        content="",
        finish_reason="length",
        tool_calls=[ToolCallRequest(id="call_x", name="list_dir", arguments={"path": "."})],
    )
    provider = MagicMock(spec=LLMProvider)
    provider.chat_stream_with_retry = AsyncMock(return_value=looping)  # forever
    tools = _tools()

    result = await AgentRunner().run(make_run_spec(
        provider,
        initial_messages=[{"role": "user", "content": "loop forever"}],
        tools=tools,
        model="test-model",
        max_iterations=50,           # the loop must stop on the bound, not the budget
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    ))

    limit = runner_module._MAX_LENGTH_TOOL_CALL_EXECUTIONS
    assert tools.execute.await_count == limit, (
        f"expected exactly {limit} executions, got {tools.execute.await_count}"
    )
    assert result.stop_reason == "truncated_tool_call"
    tool_text = " ".join(
        m.get("content") or "" for m in result.messages if m.get("role") == "tool"
    )
    assert TRUNCATED_TOOL_CALL_MESSAGE in tool_text
    # It terminated well before the iteration budget: the bound is what stopped it.
    assert provider.chat_stream_with_retry.await_count <= limit + 1
