"""The Deal chain (nanobot/agent/deal.py) — acceptance tests.

The pipeline: work fold done -> welcome to the deal session; deal-day EOS
-> compact again; sleep. The marker precedes the content, always.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nanobot.agent import deal


class FakeSession:
    def __init__(self, key):
        self.key = key
        self.metadata = {}


class FakeSessionManager:
    def __init__(self, handles=None):
        self.sessions = {}
        self.handles = handles or {}
        self.saved = []
        self.metadata_updates = []

    def read_session_metadata(self, key):
        # Mirror the real JsonlSessionStore payload shape (nested "metadata")
        # so the fake can't drift from production again (10-14: the flat
        # shape here hid the _handle_for mismatch; the chain never fired).
        if key in self.handles:
            return {
                "key": key,
                "created_at": None,
                "updated_at": None,
                "metadata": {"session_handle": self.handles[key]},
            }
        return None

    def get_or_create(self, key):
        return self.sessions.setdefault(key, FakeSession(key))

    def save(self, session):
        self.saved.append(session.key)

    def update_session_metadata(self, key, updates, *, fsync=False):
        self.metadata_updates.append((key, updates))
        if key in self.sessions:
            self.sessions[key].metadata.update(updates)
        return True


class FakeStore:
    def __init__(self):
        self.triggers = []
        self.enqueued = []

    def list_triggers(self):
        return list(self.triggers)

    def create(self, *, name, channel, chat_id, session_key, sender_id="trigger", origin_metadata=None):
        trigger = SimpleNamespace(
            id=name, name=name, channel=channel, chat_id=chat_id,
            session_key=session_key, sender_id=sender_id, enabled=True,
        )
        self.triggers.append(trigger)
        return trigger

    def enqueue(self, trigger_id, content):
        self.enqueued.append((trigger_id, content))


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "cron").mkdir()
    (tmp_path / "cron" / "deal-roster.txt").write_text("nime\npore\n")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "deal-welcome.md").write_text("This is your day.")
    (tmp_path / "bin" / "fold-prompt.md").write_text("The day is closing. Fold it in.")
    return tmp_path


@pytest.fixture(autouse=True)
def ledger_to_tmp(tmp_path, monkeypatch):
    path = tmp_path / "ledger.json"
    monkeypatch.setattr(deal, "LEDGER_PATH", path)
    return path


def work_trigger(key="websocket:abc"):
    return SimpleNamespace(
        id="compact-websocket-abc", session_key=key, sender_id="daily-compaction",
    )


def fold_delivery():
    return SimpleNamespace(id="d1", content="The day is closing. Fold it in.")


def compact_delivery():
    return SimpleNamespace(id="d2", content="/compact")


def test_fold_with_goodnight_opens_the_day(workspace):
    store = FakeStore()
    sessions = FakeSessionManager({"websocket:abc": "nime"})
    deal.chain_after_delivery(
        store=store, session_manager=sessions, workspace=workspace,
        trigger=work_trigger(), delivery=fold_delivery(),
        response_text="Folded. Goodnight.",
    )
    deal_session = sessions.sessions["websocket:deal-nime"]
    assert deal_session.metadata["_deal_session"] == "active"
    assert deal_session.metadata["session_handle"] == "nime"
    # the marker was saved BEFORE any content could land
    assert "websocket:deal-nime" in sessions.saved
    assert store.enqueued == [("deal-nime", "This is your day.")]


def test_handle_resolution_against_the_real_store(tmp_path):
    """Regression (2026-10-14): read_session_metadata returns the store
    payload — session metadata nested under "metadata". _handle_for read
    the top level, found no handle ever, and the whole Deal chain silently
    no-oped through a full morning sweep. This test wires the real
    SessionManager + JsonlSessionStore so the shapes cannot drift apart."""
    from nanobot.session.manager import SessionManager

    sm = SessionManager(tmp_path / "ws", sessions_root=tmp_path / "sessions")
    session = sm.get_or_create("websocket:abc")
    sm.save(session)
    sm.update_session_metadata("websocket:abc", {"session_handle": "nime"}, fsync=True)

    assert deal._handle_for(sm, "websocket:abc") == "nime"
    assert deal._handle_for(sm, "websocket:missing") is None


def test_fold_declined_means_no_deal_day(workspace):
    store = FakeStore()
    sessions = FakeSessionManager({"websocket:abc": "nime"})
    deal.chain_after_delivery(
        store=store, session_manager=sessions, workspace=workspace,
        trigger=work_trigger(), delivery=fold_delivery(),
        response_text="Not tonight — mid-incident.",
    )
    assert sessions.sessions == {}
    assert store.enqueued == []


def test_compact_completion_opens_the_day(workspace):
    store = FakeStore()
    sessions = FakeSessionManager({"websocket:abc": "pore"})
    deal.chain_after_delivery(
        store=store, session_manager=sessions, workspace=workspace,
        trigger=work_trigger(), delivery=compact_delivery(),
        response_text="Compaction complete.",
    )
    assert "websocket:deal-pore" in sessions.sessions
    assert store.enqueued == [("deal-pore", "This is your day.")]


def test_unrostered_sessions_never_chain(workspace):
    store = FakeStore()
    sessions = FakeSessionManager({"websocket:abc": "stranger"})
    deal.chain_after_delivery(
        store=store, session_manager=sessions, workspace=workspace,
        trigger=work_trigger(), delivery=fold_delivery(),
        response_text="Goodnight.",
    )
    assert sessions.sessions == {}
    assert store.enqueued == []


def test_welcome_turn_completion_closes_and_sleeps(workspace):
    """Evans 2026-10-14: no second compaction. The marker closes; the deal
    session folds at the next 05:00 sweep like every other session."""
    store = FakeStore()
    sessions = FakeSessionManager()
    sessions.sessions["websocket:deal-nime"] = FakeSession("websocket:deal-nime")
    sessions.sessions["websocket:deal-nime"].metadata["_deal_session"] = "active"
    welcome_trigger = SimpleNamespace(
        id="deal-nime", session_key="websocket:deal-nime", sender_id="deal-session",
    )
    deal.chain_after_delivery(
        store=store, session_manager=sessions, workspace=workspace,
        trigger=welcome_trigger, delivery=SimpleNamespace(id="d3", content="This is your day."),
        response_text="What a day. The motel has a cat.",
    )
    key, updates = sessions.metadata_updates[0]
    assert key == "websocket:deal-nime"
    assert updates["_deal_session"] == "closed"
    assert "_deal_closed_at" in updates
    assert store.enqueued == []  # nothing queued — the day just ends


def test_the_close_never_chains_again(workspace):
    store = FakeStore()
    sessions = FakeSessionManager()
    fold_trigger = SimpleNamespace(
        id="deal-fold-nime", session_key="websocket:deal-nime", sender_id="daily-compaction",
    )
    deal.chain_after_delivery(
        store=store, session_manager=sessions, workspace=workspace,
        trigger=fold_trigger, delivery=fold_delivery(), response_text="Goodnight.",
    )
    assert store.enqueued == []


def test_an_open_day_is_never_reopened(workspace):
    store = FakeStore()
    sessions = FakeSessionManager({"websocket:abc": "nime"})
    open_session = FakeSession("websocket:deal-nime")
    open_session.metadata["_deal_session"] = "active"
    sessions.sessions["websocket:deal-nime"] = open_session
    deal.chain_after_delivery(
        store=store, session_manager=sessions, workspace=workspace,
        trigger=work_trigger(), delivery=fold_delivery(),
        response_text="Goodnight.",
    )
    assert store.enqueued == []


def test_face_binding_copies_never_invents(workspace, ledger_to_tmp):
    ledger_to_tmp.write_text(json.dumps({
        "websocket:abc": {"handle": "nime", "password": "coin", "state": "ready"},
    }))
    store = FakeStore()
    sessions = FakeSessionManager({"websocket:abc": "nime"})
    deal.chain_after_delivery(
        store=store, session_manager=sessions, workspace=workspace,
        trigger=work_trigger(), delivery=fold_delivery(),
        response_text="Goodnight.",
    )
    ledger = json.loads(ledger_to_tmp.read_text())
    assert ledger["websocket:deal-nime"]["password"] == "coin"
    assert ledger["websocket:deal-nime"]["bound_from"] == "websocket:abc"

    # a handleless mint invents nothing
    store2 = FakeStore()
    sessions2 = FakeSessionManager({"websocket:xyz": "pore"})
    deal.chain_after_delivery(
        store=store2, session_manager=sessions2, workspace=workspace,
        trigger=work_trigger("websocket:xyz"), delivery=compact_delivery(),
        response_text="done",
    )
    ledger = json.loads(ledger_to_tmp.read_text())
    assert "websocket:deal-pore" not in ledger


def test_differing_coins_are_never_guessed(workspace, ledger_to_tmp):
    ledger_to_tmp.write_text(json.dumps({
        "websocket:a": {"handle": "nime", "password": "one", "state": "ready"},
        "websocket:b": {"handle": "nime", "password": "two", "state": "ready"},
    }))
    store = FakeStore()
    sessions = FakeSessionManager({"websocket:abc": "nime"})
    deal.chain_after_delivery(
        store=store, session_manager=sessions, workspace=workspace,
        trigger=work_trigger(), delivery=fold_delivery(),
        response_text="Goodnight.",
    )
    ledger = json.loads(ledger_to_tmp.read_text())
    assert "websocket:deal-nime" not in ledger
    # the day still opened — the binding problem is flagged, not fatal
    assert store.enqueued == [("deal-nime", "This is your day.")]
