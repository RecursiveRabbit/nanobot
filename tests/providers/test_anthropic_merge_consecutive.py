"""Tests for AnthropicProvider._merge_consecutive.

Evans 2026-10-10: the function no longer merges anything — it validates
Anthropic's strict-alternation contract and raises ``RoleAlternationError``.
Message content is never fused, dropped, rerouted, or synthesized at the
provider layer.
"""

import pytest

from nanobot.providers.anthropic_provider import AnthropicProvider
from nanobot.providers.base import RoleAlternationError


class TestValidSequencesPassThrough:
    def test_basic_alternation(self):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "bye"},
        ]
        result = AnthropicProvider._merge_consecutive([dict(m) for m in msgs])
        assert result == msgs

    def test_empty_messages(self):
        assert AnthropicProvider._merge_consecutive([]) == []

    def test_tool_use_then_tool_result_alternation(self):
        msgs = [
            {"role": "user", "content": "search"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "search", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
            ]},
        ]
        result = AnthropicProvider._merge_consecutive([dict(m) for m in msgs])
        assert result == msgs

    def test_original_messages_not_mutated(self):
        msgs = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
        ]
        snapshot = [dict(m) for m in msgs]
        AnthropicProvider._merge_consecutive(msgs)
        assert msgs == snapshot


class TestRefusesLoudly:
    def test_consecutive_same_role_raises(self):
        """Regression: the old block-extend fused adjacent user turns into
        one, hiding text from the assembled-context view."""
        msgs = [
            {"role": "user", "content": "a"},
            {"role": "user", "content": "b"},
            {"role": "assistant", "content": "reply"},
        ]
        with pytest.raises(RoleAlternationError, match="consecutive 'user'"):
            AnthropicProvider._merge_consecutive(msgs)

    def test_trailing_assistant_raises(self):
        msgs = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"},
        ]
        with pytest.raises(RoleAlternationError, match="ends with an 'assistant'"):
            AnthropicProvider._merge_consecutive(msgs)

    def test_only_assistant_messages_raise(self):
        with pytest.raises(RoleAlternationError):
            AnthropicProvider._merge_consecutive(
                [{"role": "assistant", "content": "lonely"}]
            )

    def test_leading_assistant_raises(self):
        msgs = [
            {"role": "assistant", "content": "a"},
            {"role": "user", "content": "u"},
        ]
        with pytest.raises(RoleAlternationError, match="starts with an 'assistant'"):
            AnthropicProvider._merge_consecutive(msgs)

    def test_retired_checkpoint_marker_line_is_ordinary_text_now(self):
        """The retired imperative marker is no longer special anywhere: as
        plain content it still cannot make two user turns legal."""
        legacy = "Continue the active task from the working-memory checkpoint above."
        msgs = [
            {"role": "user", "content": legacy},
            {"role": "user", "content": "Morning Studi, coffee?"},
        ]
        with pytest.raises(RoleAlternationError):
            AnthropicProvider._merge_consecutive(msgs)
