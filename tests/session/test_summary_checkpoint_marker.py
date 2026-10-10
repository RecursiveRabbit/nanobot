"""Tests for the summary-checkpoint marker text and its recognition set."""

from nanobot.session.history_visibility import HIDDEN_HISTORY_META
from nanobot.session.summary import (
    SUMMARY_CONTINUATION_TEXT,
    is_summary_checkpoint,
    is_summary_checkpoint_content,
    summary_continuation_text,
)

LEGACY_IMPERATIVE_MARKER = (
    "Continue the active task from the working-memory checkpoint above."
)


class TestSummaryContinuationText:
    def test_default_marker_is_declarative(self):
        """The marker line must never assign a task to the agent.

        Evans' rule: the compaction prompt is the only task-assigning string
        the harness may inject. The checkpoint marker describes state; it
        does not command.
        """
        marker = SUMMARY_CONTINUATION_TEXT
        lowered = marker.lower()
        for imperative in ("continue", "resume", "proceed", "keep going", "now "):
            assert not lowered.startswith(imperative), (
                f"marker line must be declarative, got: {marker!r}"
            )

    def test_current_text_matches(self):
        assert is_summary_checkpoint_content(SUMMARY_CONTINUATION_TEXT)
        assert is_summary_checkpoint_content(summary_continuation_text())

    def test_legacy_imperative_text_still_matches(self):
        """Checkpoints persisted under the retired text must not be orphaned."""
        assert is_summary_checkpoint_content(LEGACY_IMPERATIVE_MARKER)

    def test_unrelated_content_does_not_match(self):
        assert not is_summary_checkpoint_content("hello there")
        assert not is_summary_checkpoint_content("")
        assert not is_summary_checkpoint_content(None)
        assert not is_summary_checkpoint_content(["Working-memory checkpoint above."])


class TestIsSummaryCheckpoint:
    def _message(self, content, *, hidden=True):
        msg = {"role": "user", "content": content}
        if hidden:
            msg[HIDDEN_HISTORY_META] = True
        return msg

    def test_hidden_legacy_marker_is_checkpoint(self):
        assert is_summary_checkpoint(
            self._message(LEGACY_IMPERATIVE_MARKER)
        )

    def test_hidden_current_marker_is_checkpoint(self):
        assert is_summary_checkpoint(self._message(SUMMARY_CONTINUATION_TEXT))

    def test_marker_without_hidden_meta_is_not_checkpoint(self):
        assert not is_summary_checkpoint(
            self._message(SUMMARY_CONTINUATION_TEXT, hidden=False)
        )
