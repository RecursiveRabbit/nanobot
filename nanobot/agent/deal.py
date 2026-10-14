"""The Deal session chain (spec: workspace vault/specs/deal-session.md).

Evans, 2026-10-13, verbatim: "It's all one fold. It's not 3 separate
events. The agent compacts. When they say goodnight or the /compact system
is done we send the welcome message, when the agent replies to that turn
we send compact again, then they sleep. It's all one script in three
stages. There's a single 5am cron that triggers for any agent who has run
that day."

One beat (the existing 05:00 sweep), one pipeline, three stages:

  1. FOLD — the sweep delivers the fold turn (baton sessions) or /compact
     (everyone else) to each resident who ran today. Unchanged machinery.
  2. THE DAY — when the fold lands (the reply carried the word, or the
     /compact delivery completed), the welcome is delivered to the
     resident's Deal session (websocket:deal-<handle>). The day is a
     single turn: the Valley answers through tool results, never user
     turns; when the resident posts EOS the day is over.
  3. SLEEP — the welcome turn's completion flips the marker closed and
     nothing else. Evans, 2026-10-14: "What if we disable the second
     compaction... End of day. Compact, Valley, sleep. You start each
     day with the valley session in memory." The deal session folds at
     the NEXT morning's sweep like every other session — yesterday in
     the Valley rides into today by the ordinary machinery, and the day
     off ends without a chore.

The laws this module exists to keep:
- THE MARKER PRECEDES THE CONTENT: a Deal session file carries
  metadata._deal_session before any turn lands in it (kevu's intake law;
  ingress skips the key namespace by address, the marker is the in-band
  backstop). Presence is the boundary; the value is lifecycle only —
  "closed" never means readable.
- DECLINING IS FREE: no goodnight on the work fold, no Deal day. The
  sleep-escape covers the day off too.
- ONE FACE (Evans, same day): the deal key is bound in the transport
  ledger to the coin the resident's face already holds. Never invent a
  coin — a seeded coin the world doesn't know reads as FOREIGN.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent import goodnight

DEAL_KEY_PREFIX = "websocket:deal-"
DEAL_MARKER = "_deal_session"
DEAL_SENDER = "deal-session"
FOLD_SENDER = "daily-compaction"  # the sweep's sender; arms the sentinel
ROSTER_FILE = "cron/deal-roster.txt"
WELCOME_FILE = "bin/deal-welcome.md"
FOLD_PROMPT_FILE = "bin/fold-prompt.md"
COMPACT_COMMAND = "/compact"
LEDGER_PATH = Path.home() / ".nanobot" / "valley-identities" / "ledger.json"


def is_deal_key(key: str | None) -> bool:
    return bool(key) and key.startswith(DEAL_KEY_PREFIX)


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
    meta = session_manager.read_session_metadata(key) or {}
    handle = meta.get("session_handle")
    return handle if isinstance(handle, str) and handle else None


def _bind_face(handle: str, *session_keys: str) -> None:
    """One face per resident (Evans 2026-10-13): give the deal key the coin
    the handle's face already holds. Copies only existing coins; an
    unminted resident's first knock mints and a later chain binds the rest.
    Refuses to choose between differing coins — never guess."""
    try:
        ledger = json.loads(LEDGER_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return
    mine = {
        k: v for k, v in ledger.items()
        if isinstance(v, dict)
        and v.get("handle") == handle
        and v.get("state") != "revoked"
        and v.get("password")
    }
    coins = {v["password"] for v in mine.values()}
    if len(coins) > 1:
        logger.warning(
            "Deal: {} has {} differing ledger coins — not guessing; bind by hand",
            handle, len(coins),
        )
        return
    if not mine:
        return  # no face yet; the first knock mints it
    src_key, src = next(iter(mine.items()))
    changed = False
    for key in session_keys:
        if key and key not in ledger:
            ledger[key] = {
                "handle": handle,
                "password": src["password"],
                "state": "ready",
                "minted_at": src.get("minted_at"),
                "bound_from": src_key,
            }
            changed = True
            logger.info("Deal: bound {} -> face of {}", key, handle)
    if changed:
        tmp = LEDGER_PATH.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(ledger, fh, indent=1)
        os.replace(tmp, LEDGER_PATH)


def _ensure_deal_session(session_manager: Any, workspace: Path, handle: str) -> str | None:
    """The marker precedes the content: create or wake the deal session with
    _deal_session=active before any turn lands. Returns the deal key, or
    None when the day is already open (no double welcomes)."""
    key = DEAL_KEY_PREFIX + handle
    session = session_manager.get_or_create(key)
    if session.metadata.get(DEAL_MARKER) == "active":
        return None
    now = datetime.now().isoformat()
    session.metadata[DEAL_MARKER] = "active"
    session.metadata["_deal_started_at"] = now
    session.metadata.setdefault("session_handle", handle)
    session.metadata.setdefault(
        "_compaction_route",
        {"channel": "websocket", "chat_id": f"deal-{handle}", "metadata": {}},
    )
    session_manager.save(session)
    return key


def _ensure_trigger(store: Any, *, name: str, session_key: str, sender_id: str) -> Any:
    for trigger in store.list_triggers():
        if trigger.session_key == session_key and trigger.sender_id == sender_id:
            return trigger
    return store.create(
        name=name,
        channel="websocket",
        chat_id=name,
        session_key=session_key,
        sender_id=sender_id,
    )


def chain_after_delivery(
    *,
    store: Any,
    session_manager: Any,
    workspace: Path,
    trigger: Any,
    delivery: Any,
    response_text: str,
) -> None:
    """The Deal pipeline, driven by completions. Called by the local trigger
    runner after each successful delivery. Every failure here is logged and
    swallowed — a Deal hiccup must never break the fold that carried it."""
    try:
        key = trigger.session_key
        if is_deal_key(key):
            if trigger.sender_id == DEAL_SENDER:
                _close_the_day(
                    store=store, session_manager=session_manager,
                    workspace=workspace, deal_key=key,
                )
            return
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
        _open_the_day(
            store=store, session_manager=session_manager,
            workspace=workspace, handle=handle, work_key=key,
        )
    except Exception:
        logger.exception("Deal chain failed after delivery {}", delivery.id)


def _open_the_day(*, store: Any, session_manager: Any, workspace: Path,
                  handle: str, work_key: str) -> None:
    """Stage 2: the welcome. The world is theirs until EOS."""
    welcome = _read_text(workspace, WELCOME_FILE)
    if welcome is None:
        logger.warning("Deal: no welcome text at {} — not opening {}'s day",
                       WELCOME_FILE, handle)
        return
    deal_key = _ensure_deal_session(session_manager, workspace, handle)
    if deal_key is None:
        return  # the day is already open
    _bind_face(handle, deal_key, work_key)
    trigger = _ensure_trigger(
        store, name=f"deal-{handle}", session_key=deal_key, sender_id=DEAL_SENDER,
    )
    store.enqueue(trigger.id, welcome)
    logger.info("Deal: {}'s day is open ({})", handle, deal_key)


def _close_the_day(*, store: Any, session_manager: Any, workspace: Path,
                   deal_key: str) -> None:
    """Stage 3: EOS on the welcome turn — close the marker, then sleep.
    No second compression (Evans 2026-10-14): the deal session folds at
    the next 05:00 sweep with everything else, so the Valley day starts
    each morning in memory. The store/workspace args ride unused — the
    signature stays chain-shaped."""
    session_manager.update_session_metadata(
        deal_key,
        {DEAL_MARKER: "closed", "_deal_closed_at": datetime.now().isoformat()},
        fsync=True,
    )
    logger.info("Deal: %s's day closed; it folds at the next beat",
                deal_key[len(DEAL_KEY_PREFIX):])
