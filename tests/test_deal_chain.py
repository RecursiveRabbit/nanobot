"""The Deal chain (nanobot/agent/deal.py) — acceptance tests.

REDESIGNED 2026-10-15: no new sessions, no minting. The Deal turn is a
flagged turn in the resident's existing session, delivered immediately
after the fold and awaited by the runner. The sender_id is the privacy
flag; ingress strips it.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nanobot.agent import deal


class FakeSessionManager:
    def __init__(self, handles=None):
        self.handles = handles or {}

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


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "deal-welcome.md").write_text("This is your day.")
    return tmp_path


def work_trigger(key="websocket:abc"):
    return SimpleNamespace(
        id="compact-websocket-abc", session_key=key, sender_id="daily-compaction",
    )


def fold_delivery():
    return SimpleNamespace(id="d1", content="The day is closing. Fold it in.")


def compact_delivery():
    return SimpleNamespace(id="d2", content="/compact")


async def test_fold_with_goodnight_opens_the_day(workspace):
    sessions = FakeSessionManager({"websocket:abc": "nime"})
    submit = AsyncMock(return_value=SimpleNamespace(content="EOS"))
    await deal.chain_after_delivery(
        store=None, session_manager=sessions, workspace=workspace,
        trigger=work_trigger(), delivery=fold_delivery(),
        response_text="Folded. Goodnight.", submit_turn=submit,
    )
    submit.assert_awaited_once()
    msg = submit.await_args.args[0]
    assert msg.session_key_override == "websocket:abc"
    assert msg.sender_id == "deal-session"
    assert msg.content == "This is your day."
    assert msg.metadata["_deal_turn"] is True


async def test_handle_resolution_against_the_real_store(tmp_path):
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


async def test_fold_declined_means_no_deal_day(workspace):
    sessions = FakeSessionManager({"websocket:abc": "nime"})
    submit = AsyncMock()
    await deal.chain_after_delivery(
        store=None, session_manager=sessions, workspace=workspace,
        trigger=work_trigger(), delivery=fold_delivery(),
        response_text="Not tonight — mid-incident.", submit_turn=submit,
    )
    submit.assert_not_awaited()


async def test_deal_turn_satisfies_the_coordinator_contract(workspace):
    """Regression (2026-10-15, the silent morning): AutomationTurnCoordinator
    .submit raises ValueError on any turn whose metadata lacks a delivery
    id — the first live chain fired both legs and died at that seam. Pin
    the deal turn against the REAL extractor the coordinator runs
    (local_trigger_delivery_id), not a re-assertion of the dict shape,
    and pin persist_content: the history override replaces msg.content
    wholesale, so without it the resident's day would open on a bare
    marker instead of the welcome."""
    from nanobot.triggers.local_session_turns import (
        LOCAL_TRIGGER_META,
        local_trigger_delivery_id,
    )

    sessions = FakeSessionManager({"websocket:abc": "nime"})
    submit = AsyncMock(return_value=SimpleNamespace(content="EOS"))
    await deal.chain_after_delivery(
        store=None, session_manager=sessions, workspace=workspace,
        trigger=work_trigger(), delivery=fold_delivery(),
        response_text="Goodnight.", submit_turn=submit,
    )
    submit.assert_awaited_once()
    msg = submit.await_args.args[0]
    assert local_trigger_delivery_id(msg.metadata) == "deal-d1"
    trigger = msg.metadata[LOCAL_TRIGGER_META]
    assert trigger["persist_content"] == "This is your day."
    assert msg.metadata["_deal_turn"] is True
    assert msg.metadata["webui_turn_id"].startswith("trigger:deal-nime:")
    assert msg.metadata["_webui_message_source"] == {
        "kind": "local_trigger",
        "label": "deal-nime",
    }


async def test_compact_completion_opens_the_day(workspace):
    sessions = FakeSessionManager({"websocket:abc": "pore"})
    submit = AsyncMock(return_value=SimpleNamespace(content="done"))
    await deal.chain_after_delivery(
        store=None, session_manager=sessions, workspace=workspace,
        trigger=work_trigger(), delivery=compact_delivery(),
        response_text="Compaction complete.", submit_turn=submit,
    )
    submit.assert_awaited_once()
    assert submit.await_args.args[0].session_key_override == "websocket:abc"


async def test_unrostered_sessions_chain(workspace):
    """The roster is retired (Evans 2026-10-21: "All agents get deal
    days... People you know are not more important than people you
    don't"). No roster file exists; a handle nobody planted still gets
    the day — the completed fold is the only qualification."""
    sessions = FakeSessionManager({"websocket:abc": "stranger"})
    submit = AsyncMock(return_value=SimpleNamespace(content="EOS"))
    await deal.chain_after_delivery(
        store=None, session_manager=sessions, workspace=workspace,
        trigger=work_trigger(), delivery=fold_delivery(),
        response_text="Goodnight.", submit_turn=submit,
    )
    submit.assert_awaited_once()


async def test_stale_roster_file_is_ignored(workspace):
    """A leftover cron/deal-roster.txt (the tombstone) must change
    nothing — the file is a memorial, not a gate."""
    (workspace / "cron").mkdir()
    (workspace / "cron" / "deal-roster.txt").write_text("nime\npore\n")
    sessions = FakeSessionManager({"websocket:abc": "stranger"})
    submit = AsyncMock(return_value=SimpleNamespace(content="EOS"))
    await deal.chain_after_delivery(
        store=None, session_manager=sessions, workspace=workspace,
        trigger=work_trigger(), delivery=fold_delivery(),
        response_text="Goodnight.", submit_turn=submit,
    )
    submit.assert_awaited_once()


async def test_handleless_session_chains_with_key_label(workspace):
    """Sessions that never grew a session_handle (tap tests, smoke
    fossils, strangers) still get their day; the label derives from
    the key and only names logs and the trigger id."""
    sessions = FakeSessionManager()  # no metadata at all
    submit = AsyncMock(return_value=SimpleNamespace(content="EOS"))
    await deal.chain_after_delivery(
        store=None, session_manager=sessions, workspace=workspace,
        trigger=work_trigger(), delivery=fold_delivery(),
        response_text="Goodnight.", submit_turn=submit,
    )
    submit.assert_awaited_once()
    msg = submit.await_args.args[0]
    assert msg.session_key_override == "websocket:abc"
    assert msg.metadata["_webui_message_source"] == {
        "kind": "local_trigger",
        "label": "deal-websocket-abc",
    }


async def test_wrong_sender_never_chains(workspace):
    sessions = FakeSessionManager({"websocket:abc": "nime"})
    submit = AsyncMock()
    trigger = SimpleNamespace(
        id="other", session_key="websocket:abc", sender_id="someone-else",
    )
    await deal.chain_after_delivery(
        store=None, session_manager=sessions, workspace=workspace,
        trigger=trigger, delivery=fold_delivery(),
        response_text="Goodnight.", submit_turn=submit,
    )
    submit.assert_not_awaited()


async def test_no_welcome_text_means_no_day(workspace):
    (workspace / "bin" / "deal-welcome.md").unlink()
    sessions = FakeSessionManager({"websocket:abc": "nime"})
    submit = AsyncMock()
    await deal.chain_after_delivery(
        store=None, session_manager=sessions, workspace=workspace,
        trigger=work_trigger(), delivery=fold_delivery(),
        response_text="Goodnight.", submit_turn=submit,
    )
    submit.assert_not_awaited()


async def test_chain_failure_is_logged_and_swallowed(workspace):
    """A Deal hiccup must never break the fold that carried it."""
    sessions = FakeSessionManager({"websocket:abc": "nime"})
    submit = AsyncMock(side_effect=RuntimeError("boom"))
    await deal.chain_after_delivery(
        store=None, session_manager=sessions, workspace=workspace,
        trigger=work_trigger(), delivery=fold_delivery(),
        response_text="Goodnight.", submit_turn=submit,
    )
    submit.assert_awaited_once()  # attempted, failed, swallowed
