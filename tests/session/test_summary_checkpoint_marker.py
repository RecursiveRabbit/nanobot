"""Tests for the retired checkpoint marker and its replay suture.

The marker message machinery was excised on Evans' 2026-10-10 ruling: the
harness never places synthetic text into user turns. These tests pin the
suture: checkpoints persisted BEFORE the excision still carry a hidden
marker line, and that line must never reach replay or the summarizer.
They also pin that no new marker is ever written.
"""

from nanobot.session.history_visibility import HIDDEN_HISTORY_META
from nanobot.session.manager import Session
from nanobot.session.summary import (
    is_retired_summary_checkpoint,
)

LEGACY_IMPERATIVE_MARKER = (
    "Continue the active task from the working-memory checkpoint above."
)
BRIEF_DECLARATIVE_MARKER = "Working-memory checkpoint above."


class TestRetiredPredicate:
    def _message(self, content, *, hidden=True):
        msg = {"role": "user", "content": content}
        if hidden:
            msg[HIDDEN_HISTORY_META] = True
        return msg

    def test_legacy_imperative_marker_matches(self):
        assert is_retired_summary_checkpoint(self._message(LEGACY_IMPERATIVE_MARKER))

    def test_declarative_marker_matches(self):
        """The short-lived 82416802 default may exist on disk; it stays retired."""
        assert is_retired_summary_checkpoint(self._message(BRIEF_DECLARATIVE_MARKER))

    def test_hidden_meta_required(self):
        assert not is_retired_summary_checkpoint(
            self._message(LEGACY_IMPERATIVE_MARKER, hidden=False)
        )

    def test_unrelated_content_does_not_match(self):
        assert not is_retired_summary_checkpoint(self._message("hello there"))
        assert not is_retired_summary_checkpoint(self._message(""))
        assert not is_retired_summary_checkpoint(None)
        assert not is_retired_summary_checkpoint({"role": "user", "content": None})

    def test_ordinary_text_is_never_a_checkpoint(self):
        """An operator typing the old sentence by hand is conversation, not
        a checkpoint — but no code path treats it specially either way."""
        assert not is_retired_summary_checkpoint(
            {"role": "user", "content": LEGACY_IMPERATIVE_MARKER}
        )


class TestNoNewMarkerIsWritten:
    def test_commit_summary_checkpoint_writes_no_marker_message(self):
        session = Session("cli:test")
        session.add_message("user", "old question")
        session.add_message("assistant", "old answer")
        boundary = len(session.messages)
        session.add_message("user", "new question")

        session.commit_summary_checkpoint(
            "settled notes", insert_at=boundary, last_active=None
        )

        assert session.last_archived == boundary
        assert len(session.messages) == 3  # nothing inserted
        assert session.metadata["_last_summary"]["text"] == "settled notes"
        replay = session.get_history()
        assert [m["content"] for m in replay] == ["new question"]

    def test_replay_skips_retired_marker_lines(self):
        """A session persisted before the excision: the hidden marker sits
        at the archive boundary and must not replay as a user turn."""
        session = Session("cli:legacy")
        session.add_message("user", "old question")
        session.add_message("assistant", "old answer")
        session.messages.append({
            "role": "user",
            "content": LEGACY_IMPERATIVE_MARKER,
            HIDDEN_HISTORY_META: True,
            "timestamp": "2026-10-09T00:00:00",
        })
        session.last_archived = 2
        session.add_message("user", "new question")

        replay = session.get_history()
        assert [m["content"] for m in replay] == ["new question"]
