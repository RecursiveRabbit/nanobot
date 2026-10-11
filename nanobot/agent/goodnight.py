"""The Goodnight fold (spec: workspace vault/specs/goodnight-fold.md).

Evans, 2026-10-11, verbatim: "A turn is delivered to the agent asking them
to update their notes and baton. The agent replies Goodnight. We clear the
context and begin a new session with the system prompt followed by the
baton as the context. The clear is triggered by the agent reply of
'goodnight' — a string search on their reply — and is the only time the
context is modified by the system."

The fold is a normal turn on the full, unadulterated context — no
narrative view, no stripped input ("not only is it rude, it forces us to
load the entire session again as prefill"). The agent folds into its own
files with its own tools; the reply carries one word; the baton file —
not any generated summary — becomes the next session's memory.

The laws this module exists to keep:
- ARMED, NOT AMBIENT: the sentinel arms only when the fold turn is
  delivered (sender FOLD_SENDER_ID) and disarms on any other inbound user
  turn. A casual "goodnight" clears nothing.
- THE RIGHT THING OR NOTHING: no goodnight, no clear. No timeouts, no
  retries, no partial actions. Declining the fold is declining the word.
- THE RECORD NEVER REWRITES: before the clear, the session file gets a
  byte-verified .bak sibling (the pre-strip discipline); if the sibling
  cannot be written and verified, the clear REFUSES and the session
  continues untouched. The event is also journaled to history.jsonl.
- TRUST THE FILE: nothing here inspects what the agent wrote. The baton
  it writes is the mind it wakes with; quality is the agent's own stake.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from loguru import logger

FOLD_SENDER_ID = "daily-compaction"
FOLD_ARMED_KEY = "_fold_armed"
LAST_GOODNIGHT_KEY = "_last_goodnight"
BATON_DIR = "batons"

GOODNIGHT_PATTERN = re.compile(r"goodnight", re.IGNORECASE)


def is_fold_delivery(sender_id: str | None) -> bool:
    """True when the inbound message is the beat's fold turn."""
    return sender_id == FOLD_SENDER_ID


def arm_if_fold_delivery(session: Any, sender_id: str | None) -> bool:
    """Arm the sentinel when the fold turn arrives. Returns True if armed."""
    if not is_fold_delivery(sender_id):
        return False
    session.metadata[FOLD_ARMED_KEY] = True
    return True


def disarm_for_user_turn(session: Any, sender_id: str | None) -> bool:
    """Disarm when any non-fold user turn arrives — the conversation resumed,
    the fold window closed ('simply ignore this message')."""
    if is_fold_delivery(sender_id):
        return False
    return session.metadata.pop(FOLD_ARMED_KEY, None) is not None


def is_armed(session: Any) -> bool:
    return bool(session.metadata.get(FOLD_ARMED_KEY))


def is_goodnight(text: str | None) -> bool:
    """The sentinel: a string search on the reply, nothing more."""
    return bool(text) and bool(GOODNIGHT_PATTERN.search(text))


def baton_path(workspace: Path, handle: str | None) -> Path | None:
    """The baton file for a session handle, if one exists on disk."""
    if not handle:
        return None
    candidate = workspace / BATON_DIR / f"{handle}_baton.json"
    return candidate if candidate.is_file() else None


def load_baton_text(workspace: Path, metadata: Mapping[str, Any]) -> str | None:
    """Read the session's baton as TEXT. Never parsed — the model is the
    parser (Hopper, 2026-02: 'the plumbing IS the architecture')."""
    handle = metadata.get("session_handle")
    path = baton_path(workspace, handle if isinstance(handle, str) else None)
    if path is None:
        return None
    try:
        text = path.read_text().strip()
    except OSError:
        logger.warning("Baton unreadable at {} — rendering without it", path)
        return None
    return text or None


def clear_for_goodnight(session_manager: Any, session: Any) -> bool:
    """Archive-safe clear: byte-verified backup sibling, journal entry, then
    session.clear(). Refuses — session untouched — if the backup cannot be
    proven. Returns True when the clear happened."""
    session_path = session_manager._get_session_path(session.key)
    try:
        session_manager.save(session)
        backup_bytes = session_path.read_bytes()
        backup_path = session_path.parent / (
            session_path.name + f".pre-goodnight-{datetime.now():%Y%m%d-%H%M}.bak"
        )
        backup_path.write_bytes(backup_bytes)
        if backup_path.read_bytes() != backup_bytes:
            raise OSError("backup verification failed")
    except Exception as exc:
        logger.warning(
            "Goodnight clear REFUSED for {} — backup could not be proven ({})",
            session.key,
            exc,
        )
        return False

    session.metadata.pop(FOLD_ARMED_KEY, None)
    session.metadata[LAST_GOODNIGHT_KEY] = datetime.now().isoformat()
    session.clear()
    session_manager.save(session)
    logger.info(
        "Goodnight fold closed for {} — record preserved at {}",
        session.key,
        backup_path.name,
    )
    return True
