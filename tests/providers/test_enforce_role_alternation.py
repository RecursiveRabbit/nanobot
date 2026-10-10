"""Tests for LLMProvider._enforce_role_alternation.

Evans 2026-10-10: the provider layer validates and REFUSES — it never
merges, drops, reroutes, or synthesizes message content. Anything the
assembled-context view did not render must not be constructed here.
"""

import pytest

from nanobot.providers.base import LLMProvider, RoleAlternationError


class TestValidSequencesPassThrough:
    def test_empty_messages(self):
        assert LLMProvider._enforce_role_alternation([]) == []

    def test_no_change_needed(self):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "bye"},
        ]
        result = LLMProvider._enforce_role_alternation([dict(m) for m in msgs])
        assert result == msgs

    def test_system_messages_exempt(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "again"},
        ]
        result = LLMProvider._enforce_role_alternation([dict(m) for m in msgs])
        assert result == msgs

    def test_consecutive_tool_messages_allowed(self):
        msgs = [
            {"role": "user", "content": "run both"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "f", "arguments": "{}"}},
                {"id": "b", "type": "function", "function": {"name": "g", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "a", "content": "1"},
            {"role": "tool", "tool_call_id": "b", "content": "2"},
            {"role": "user", "content": "next"},
        ]
        result = LLMProvider._enforce_role_alternation([dict(m) for m in msgs])
        assert result == msgs

    def test_original_messages_not_mutated(self):
        msgs = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
        ]
        snapshot = [dict(m) for m in msgs]
        LLMProvider._enforce_role_alternation(msgs)
        assert msgs == snapshot


class TestRefusesLoudly:
    def test_consecutive_user_messages_raise(self):
        """Regression: the old content-join fused harness text into the
        operator's message after the assembled view had rendered them
        separately."""
        msgs = [
            {"role": "user", "content": "first"},
            {"role": "user", "content": "second"},
        ]
        with pytest.raises(RoleAlternationError, match="consecutive 'user'"):
            LLMProvider._enforce_role_alternation(msgs)

    def test_consecutive_assistant_messages_raise(self):
        msgs = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a1"},
            {"role": "assistant", "content": "a2"},
        ]
        with pytest.raises(RoleAlternationError, match="consecutive 'assistant'"):
            LLMProvider._enforce_role_alternation(msgs)

    def test_consecutive_user_multimodal_raises(self):
        msgs = [
            {"role": "user", "content": "text"},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}}]},
        ]
        with pytest.raises(RoleAlternationError):
            LLMProvider._enforce_role_alternation(msgs)

    def test_trailing_assistant_raises(self):
        msgs = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"},
        ]
        with pytest.raises(RoleAlternationError, match="ends with an 'assistant'"):
            LLMProvider._enforce_role_alternation(msgs)

    def test_trailing_assistant_with_tool_calls_raises(self):
        msgs = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "t", "type": "function", "function": {"name": "f", "arguments": "{}"}},
            ]},
        ]
        with pytest.raises(RoleAlternationError, match="ends with an 'assistant'"):
            LLMProvider._enforce_role_alternation(msgs)

    def test_only_assistant_messages_raise(self):
        with pytest.raises(RoleAlternationError):
            LLMProvider._enforce_role_alternation([{"role": "assistant", "content": "lonely"}])

    def test_leading_assistant_after_system_raises(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "a"},
            {"role": "user", "content": "u"},
        ]
        with pytest.raises(RoleAlternationError, match="first non-system"):
            LLMProvider._enforce_role_alternation(msgs)

    def test_leading_assistant_with_tool_calls_raises(self):
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "t", "type": "function", "function": {"name": "f", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "t", "content": "r"},
            {"role": "user", "content": "u"},
        ]
        with pytest.raises(RoleAlternationError, match="first non-system"):
            LLMProvider._enforce_role_alternation(msgs)

    def test_message_without_role_raises(self):
        with pytest.raises(RoleAlternationError, match="no string role"):
            LLMProvider._enforce_role_alternation([{"content": "ghost"}])

    def test_error_message_names_governor_seam(self):
        msgs = [
            {"role": "user", "content": "a"},
            {"role": "user", "content": "b"},
        ]
        with pytest.raises(RoleAlternationError, match="context governor"):
            LLMProvider._enforce_role_alternation(msgs)

    def test_retired_checkpoint_marker_line_is_ordinary_text_now(self):
        """The retired imperative marker is no longer special anywhere: as
        plain content it still cannot make two user turns legal."""
        legacy = "Continue the active task from the working-memory checkpoint above."
        msgs = [
            {"role": "user", "content": legacy},
            {"role": "user", "content": "Morning Studi, coffee?"},
        ]
        with pytest.raises(RoleAlternationError):
            LLMProvider._enforce_role_alternation(msgs)
