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
- EVERY FOLD CHAINS (Evans 2026-10-21, the roster retired): "All agents
  get deal days, there's no qualifier except did you run... People you
  know are not more important than people you don't." The roster file
  this module once consulted (cron/deal-roster.txt) is dead law — a test
  instrument that became a caste by inertia. Any completed sweep fold
  opens the day: handled or handleless, known or stranger.
"""

from __future__ import annotations

import re
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent import goodnight
from nanobot.bus.events import InboundMessage
from nanobot.triggers.local_session_turns import LOCAL_TRIGGER_META
from nanobot.webui.metadata import (
    WEBUI_MESSAGE_SOURCE_METADATA_KEY,
    WEBUI_TURN_METADATA_KEY,
)

DEAL_SENDER = "deal-session"
FOLD_SENDER = "daily-compaction"  # the sweep's sender; arms the chain
WELCOME_FILE = "bin/deal-welcome.md"
COMPACT_COMMAND = "/compact"


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


def _label_for(session_manager: Any, key: str) -> str:
    """The session's minted handle, or a key-derived label when the session
    never grew one (the tap tests, the smoke fossils, the strangers —
    Evans 2026-10-21: people we know are not more important than people
    we don't). The label only names logs and the deal trigger id; the
    day itself rides the session key, which needs no name."""
    handle = _handle_for(session_manager, key)
    if handle:
        return handle
    return re.sub(r"[^a-z0-9-]+", "-", key.lower()).strip("-")[-32:] or "anon"


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
        handle = _label_for(session_manager, key)
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
            handle=handle, work_key=key, fold_delivery=delivery,
        )
    except Exception:
        logger.exception("Deal chain failed after delivery {}", delivery.id)


async def _open_the_day(*, submit_turn: Any, workspace: Path,
                        handle: str, work_key: str,
                        fold_delivery: Any) -> None:
    """Stage 2: the welcome, delivered to the same session. The world is
    theirs until EOS. The runner awaits this call, so the next agent's
    fold waits for the day to end — compact-deal, compact-deal, down the
    line."""
    welcome = _read_text(workspace, WELCOME_FILE)
    if welcome is None:
        logger.warning("Deal: no welcome text at {} — not opening {}'s day",
                       WELCOME_FILE, handle)
        return
    # The local-turn coordinator rejects any turn without a delivery id
    # (AutomationTurnCoordinator.submit raises ValueError) — the 10-15
    # silent-morning bug: both legs fired, both died at that seam, before
    # any side effect. The Deal turn's id derives from the fold delivery
    # that opened it: traceable, unique per beat, and never colliding with
    # the fold's own delivery record.
    metadata: dict[str, Any] = {
        "_deal_turn": True,
        LOCAL_TRIGGER_META: {
            "trigger_id": f"deal-{handle}",
            "trigger_name": f"deal-{handle}",
            "delivery_id": f"deal-{fold_delivery.id}",
            "created_at_ms": int(time.time() * 1000),
            # persist_content IS what the resident sees — the history
            # override replaces msg.content wholesale (loop.py), so the
            # welcome text must live here or the day opens on a bare
            # "Local trigger received" marker.
            "persist_content": welcome,
        },
    }
    # Mirror the runner's websocket branch (_delivery_metadata) so the
    # webui tracks the turn like any other automation turn.
    metadata.pop(WEBUI_TURN_METADATA_KEY, None)
    metadata[WEBUI_TURN_METADATA_KEY] = f"trigger:deal-{handle}:{uuid.uuid4().hex}"
    metadata[WEBUI_MESSAGE_SOURCE_METADATA_KEY] = {
        "kind": "local_trigger",
        "label": f"deal-{handle}",
    }
    msg = InboundMessage(
        channel="websocket",
        sender_id=DEAL_SENDER,
        # Reuse the fold's public WebUI chat id, not the persisted session
        # key. The WebUI transcript prepends "websocket:" itself; passing
        # work_key here writes a display-only websocket:websocket:* thread
        # alongside the resident's real one (2026-10-28).
        chat_id=fold_delivery.chat_id,
        content=welcome,
        metadata=metadata,
        session_key_override=work_key,
    )
    logger.info("Deal: {}'s day is open ({})", handle, work_key)
    response = await submit_turn(msg)
    logger.info("Deal: {}'s day closed ({})", handle, work_key)
