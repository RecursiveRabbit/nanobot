"""The Goodnight fold — acceptance tests (spec: vault/specs/goodnight-fold.md).

The laws under test:
- armed, not ambient (fold delivery arms; other user turns disarm; casual
  goodnight never clears)
- the right thing or nothing (no goodnight → session untouched)
- the record never rewrites (byte-verified .pre-goodnight sibling before
  any clear; refusal without it)
- the baton succeeds to the fold's system-prompt slot (rendered as text,
  precedence over the legacy summary block, missing baton falls back)
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from nanobot.agent import goodnight


def _session(**meta):
    return SimpleNamespace(
        key="websocket:test",
        metadata=dict(meta),
        messages=[
            {"role": "user", "content": "day one"},
            {"role": "assistant", "content": "work happened"},
        ],
        last_archived=0,
        provider_state=None,
        updated_at=datetime.now(),
        clear=None,  # filled below
    )


class FakeManager:
    def __init__(self, path, writable=True):
        self.path = path
        self.writable = writable
        self.saved = 0

    def _get_session_path(self, key):
        return self.path

    def save(self, session):
        self.saved += 1
        if not self.writable:
            raise OSError("disk full")
        import json

        self.path.write_text(json.dumps({"messages": session.messages}))


def _real_session(path):
    """A session double with a real clear() matching Session.clear semantics."""
    import json

    class S:
        def __init__(self):
            self.key = "websocket:test"
            self.metadata = {}
            self.messages = [
                {"role": "user", "content": "day one"},
                {"role": "assistant", "content": "work happened"},
            ]
            self.last_archived = 0
            self.provider_state = None
            self.updated_at = datetime.now()

        def clear(self):
            self.messages = []
            self.last_archived = 0
            self.provider_state = None
            self.metadata.pop("_last_summary", None)

    path.write_text(json.dumps({"messages": [{"role": "user", "content": "day one"}]}))
    return S()


def test_arm_only_on_fold_delivery():
    s = _real_session(__import__("pathlib").Path("/tmp/unused"))
    assert not goodnight.is_armed(s)
    assert goodnight.arm_if_fold_delivery(s, "daily-compaction")
    assert goodnight.is_armed(s)
    # a user named daily-compaction is the only armed path; others never arm
    s2 = _real_session(__import__("pathlib").Path("/tmp/unused"))
    assert not goodnight.arm_if_fold_delivery(s2, "evans")
    assert not goodnight.is_armed(s2)


def test_disarm_on_any_other_user_turn():
    s = _real_session(__import__("pathlib").Path("/tmp/unused"))
    goodnight.arm_if_fold_delivery(s, "daily-compaction")
    assert goodnight.disarm_for_user_turn(s, "evans")
    assert not goodnight.is_armed(s)


def test_sentinel_string_search():
    assert goodnight.is_goodnight("Goodnight")
    assert goodnight.is_goodnight("goodnight.")
    assert goodnight.is_goodnight("Files are true. GOODNIGHT.")
    assert not goodnight.is_goodnight("still working")
    assert not goodnight.is_goodnight("")
    assert not goodnight.is_goodnight(None)


def test_clear_writes_verified_backup_then_empties(tmp_path):
    path = tmp_path / "session.jsonl"
    s = _real_session(path)
    goodnight.arm_if_fold_delivery(s, "daily-compaction")
    mgr = FakeManager(path)
    assert goodnight.clear_for_goodnight(mgr, s)
    assert s.messages == []  # the context cleared
    backups = list(tmp_path.glob("*.pre-goodnight-*.bak"))
    assert len(backups) == 1  # the record preserved before the clear
    assert "day one" in backups[0].read_text()
    assert not goodnight.is_armed(s)  # disarmed by the clear itself
    assert s.metadata.get(goodnight.LAST_GOODNIGHT_KEY)


def test_clear_refuses_without_proven_backup(tmp_path):
    path = tmp_path / "session.jsonl"
    s = _real_session(path)
    goodnight.arm_if_fold_delivery(s, "daily-compaction")
    mgr = FakeManager(path, writable=False)
    assert not goodnight.clear_for_goodnight(mgr, s)
    assert s.messages != []  # nothing happened — the right thing or nothing
    assert goodnight.is_armed(s)  # still armed; the word may be said later


def test_baton_text_never_parsed(tmp_path):
    (tmp_path / "batons").mkdir()
    (tmp_path / "batons" / "nime_baton.json").write_text('{"mood": "test",}')
    text = goodnight.load_baton_text(tmp_path, {"session_handle": "nime"})
    assert text == '{"mood": "test",}'  # verbatim, even trailing comma
    assert goodnight.load_baton_text(tmp_path, {"session_handle": "nobody"}) is None
    assert goodnight.load_baton_text(tmp_path, {}) is None


def test_baton_succeeds_to_fold_slot(tmp_path):
    """The baton block renders in the system prompt, and beats the legacy
    summary block when both exist."""
    from nanobot.agent.context import ContextBuilder

    builder = ContextBuilder(tmp_path)
    summary = {"text": "old fold notes", "last_active": "2026-10-10T05:00:00-04:00"}
    prompt = builder.build_system_prompt(session_summary=summary, baton_text='{"who": "nime"}')
    assert "Your baton" in prompt
    assert '{"who": "nime"}' in prompt
    assert "old fold notes" not in prompt  # baton has precedence
    prompt2 = builder.build_system_prompt(session_summary=summary)
    assert "old fold notes" in prompt2  # legacy survives batonless sessions
    prompt3 = builder.build_system_prompt()
    assert "Your baton" not in prompt3
