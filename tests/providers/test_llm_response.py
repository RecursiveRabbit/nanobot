"""Regression tests for ``LLMResponse.should_execute_tools`` (#3220).

The agent used to execute tool calls whenever ``has_tool_calls`` was true, regardless
of ``finish_reason``. Non-compliant API gateways that inject empty / bogus tool calls
under ``refusal`` / ``content_filter`` / ``error`` pushed the agent into a tight loop
until ``max_iterations`` fired. ``should_execute_tools`` is the single guard that
every tool-execution site now funnels through.
"""

from __future__ import annotations

import pytest

from nanobot.providers.base import LLMResponse, ToolCallRequest


def _response(finish_reason: str, *, with_tool_call: bool = True) -> LLMResponse:
    tool_calls = (
        [ToolCallRequest(id="call_1", name="list_dir", arguments={"path": "."})]
        if with_tool_call
        else []
    )
    return LLMResponse(content=None, tool_calls=tool_calls, finish_reason=finish_reason)


class TestShouldExecuteTools:
    def test_no_tool_calls_never_executes(self) -> None:
        # No tool calls present -> guard must reject regardless of finish_reason.
        for reason in ("tool_calls", "stop", "length", "error", "refusal", "content_filter"):
            resp = _response(reason, with_tool_call=False)
            assert resp.should_execute_tools is False, f"rejected for finish_reason={reason!r}"

    def test_tool_calls_with_tool_calls_reason_executes(self) -> None:
        # The canonical case: provider explicitly signals tool intent.
        resp = _response("tool_calls")
        assert resp.has_tool_calls is True
        assert resp.should_execute_tools is True

    def test_tool_calls_with_stop_reason_executes(self) -> None:
        # Some compliant providers emit "stop" together with tool_calls; the
        # guard must accept this to avoid breaking real tool-calling flows.
        # See openai_compat_provider.py:~633,678 where ("tool_calls", "stop")
        # are both treated as terminal tool-call states.
        resp = _response("stop")
        assert resp.should_execute_tools is True

    def test_legacy_function_call_reason_executes(self) -> None:
        # Older OpenAI-compatible streaming APIs can still use the singular
        # function_call finish reason while carrying a tool-call-shaped payload.
        resp = _response("function_call")
        assert resp.should_execute_tools is True

    @pytest.mark.parametrize(
        "anomalous_reason",
        ["refusal", "content_filter", "error", ""],
    )
    def test_tool_calls_under_anomalous_reason_blocked(self, anomalous_reason: str) -> None:
        # This is the #3220 bug: gateways injecting tool_calls under any of these
        # finish_reasons must not cause execution. Blocking here is what prevents
        # the infinite empty tool-call loop.
        #
        # `length` left this set on 2026-11-15 — see the row below, and
        # runner._MAX_LENGTH_TOOL_CALL_EXECUTIONS for the bound that carries
        # #3220's purpose for it. The other three are unchanged: they describe a
        # provider that refused or failed, where a tool call is not the model's
        # own next step.
        resp = _response(anomalous_reason)
        assert resp.has_tool_calls is True
        assert resp.should_execute_tools is False

    def test_a_truncated_response_with_a_whole_call_executes_and_a_cut_one_does_not(self) -> None:
        """`length` is decided by whether the arguments arrived, not by the reason.

        2026-11-15 (the desk's filing): lark sat 76 hours and dova's fold died
        because a truncated response carrying a usable tool call was dropped, and
        the length ladder then asked the model to continue prose it never began.
        The call's own shape is the evidence — ``parse_tool_arguments`` keeps
        malformed JSON as the raw string it received — so object shape means the
        arguments were not cut, and string shape means they were.

        The loop this could reintroduce (#3220) is bounded in the runner, not
        here: a turn executes at most _MAX_LENGTH_TOOL_CALL_EXECUTIONS truncated
        calls before ending with a sentence that names the cap.
        """
        whole = _response("length")
        assert whole.has_tool_calls is True
        assert whole.should_execute_tools is True

        cut = LLMResponse(
            content=None,
            tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments='{"path": ".')],
            finish_reason="length",
        )
        assert cut.has_tool_calls is True
        assert cut.should_execute_tools is False
