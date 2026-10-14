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
    (tmp_path / "cron").mkdir()
    (tmp_path / "cron" / "deal-roster.txt").write_text("nime\npore\n")
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


async def test_unrostered_sessions_never_chain(workspace):
    sessions = FakeSessionManager({"websocket:abc": "stranger"})
    submit = AsyncMock()
    await deal.chain_after_delivery(
        store=None, session_manager=sessions, workspace=workspace,
        trigger=work_trigger(), delivery=fold_delivery(),
        response_text="Goodnight.", submit_turn=submit,
    )
    submit.assert_not_awaited()


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
