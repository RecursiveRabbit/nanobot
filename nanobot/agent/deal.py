"""The Deal session chain (spec: workspace vault/specs/deal-session.md).

REDESIGNED 2026-10-15 — the websocket:deal-* session-key design grenaded
on first live run (2026-10-14): the key prefix was a valid webui chat_id,
the anon websocket channel treated Deal welcomes as new sessions, and
fourteen accidental residents were minted. Evans' ruling: no new
websockets, no new sessions, no minting. The Deal turn is a flagged turn
in the resident's existing session, stripped by ingress before upload.

One beat (the existing 05:00 sweep), paired turns, sequential per agent:

  1. FOLD — the sweep delivers the fold turn (baton sessions) or /compact
     (everyone else) to each resident who ran today. Unchanged machinery.
  2. THE DAY — when the fold lands (the reply carried the word, or the
     /compact delivery completed), the chain immediately delivers the
     welcome to the SAME session with sender_id="deal-session", then
     awaits the day. The runner is sequential: compact → Deal day →
     next agent, down the roster. The day is a single turn: unlimited
     tool calls, the Valley answers through tool results, and EOS ends it.
  3. SLEEP — the turn's completion is the close. No second compression,
     no marker flipping. The session folds at the next 05:00 sweep like
     every other session, and the Valley day rides into memory by the
     ordinary machinery.

The laws this module exists to keep:
- NO MINTING: no new sessions, no new websocket keys, no ledger binding,
  no coin copying. The Deal turn rides the work session's existing face.
- THE FLAG IS THE SENDER: the welcome carries sender_id="deal-session".
  Ingress strips any turn whose inbound message carries it, replacing
  the whole turn with [DEAL SESSION STRIPPED]. The original stays in the
  session file; only the uploaded copy is redacted.
- DECLINING IS FREE: no goodnight on the work fold, no Deal day.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent import goodnight
from nanobot.bus.events import InboundMessage

DEAL_SENDER = "deal-session"
FOLD_SENDER = "daily-compaction"  # the sweep's sender; arms the chain
ROSTER_FILE = "cron/deal-roster.txt"
WELCOME_FILE = "bin/deal-welcome.md"
COMPACT_COMMAND = "/compact"


def read_roster(workspace: Path) -> set[str]:
    """Who gets a Deal day. Evans' list; one handle per line."""
    try:
        return {
            line.strip()
            for line in (workspace / ROSTER_FILE).read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
    except OSError:
        return set()


def _read_text(workspace: Path, rel: str) -> str | None:
    try:
        text = (workspace / rel).read_text().strip()
    except OSError:
        return None
    return text or None


def _handle_for(session_manager: Any, key: str) -> str | None:
    # read_session_metadata returns the store payload
    # ({"key", "created_at", "updated_at", "metadata": {...}}) — the session's
    # own metadata lives one level down (session_handles.py reads it the same
    # way). Reading the top level here silently found no handle, and the
    # chain swallowed every fold: no logs, no Deal days (2026-10-14).
    payload = session_manager.read_session_metadata(key) or {}
    meta = payload.get("metadata")
    if not isinstance(meta, dict):
        return None
    handle = meta.get("session_handle")
    return handle if isinstance(handle, str) and handle else None


async def chain_after_delivery(
    *,
    store: Any,
    session_manager: Any,
    workspace: Path,
    trigger: Any,
    delivery: Any,
    response_text: str,
    submit_turn: Any,
) -> None:
    """The Deal pipeline, driven by completions. Called by the local trigger
    runner after each successful delivery. Every failure here is logged and
    swallowed — a Deal hiccup must never break the fold that carried it."""
    try:
        key = trigger.session_key
        if trigger.sender_id != FOLD_SENDER:
            return
        handle = _handle_for(session_manager, key)
        if not handle or handle not in read_roster(workspace):
            return
        folded = (
            delivery.content.strip() == COMPACT_COMMAND
            or goodnight.is_goodnight(response_text)
        )
        if not folded:
            logger.info(
                "Deal: {} declined the fold — no deal day today (the sleep-escape)",
                handle,
            )
            return
        await _open_the_day(
            submit_turn=submit_turn, workspace=workspace,
            handle=handle, work_key=key,
        )
    except Exception:
        logger.exception("Deal chain failed after delivery {}", delivery.id)


async def _open_the_day(*, submit_turn: Any, workspace: Path,
                        handle: str, work_key: str) -> None:
    """Stage 2: the welcome, delivered to the same session. The world is
    theirs until EOS. The runner awaits this call, so the next agent's
    fold waits for the day to end — compact-deal, compact-deal, down the
    roster."""
    welcome = _read_text(workspace, WELCOME_FILE)
    if welcome is None:
        logger.warning("Deal: no welcome text at {} — not opening {}'s day",
                       WELCOME_FILE, handle)
        return
    msg = InboundMessage(
        channel="websocket",
        sender_id=DEAL_SENDER,
        chat_id=work_key,
        content=welcome,
        metadata={"_deal_turn": True},
        session_key_override=work_key,
    )
    logger.info("Deal: {}'s day is open ({})", handle, work_key)
    response = await submit_turn(msg)
    logger.info("Deal: {}'s day closed ({})", handle, work_key)
