"""The errors/ door (Evans 2026-10-10): one directory per agent workspace
that receives raw harness error records, auditable at leisure.

"We can send all harness errors there, audit the lot at our leisure."
The writer is shared from day one; individual failure sites adopt it as
they touch it. A failure notice names the file so the record is
pointable: "the raw error is stored at ./errors/error_20261010-172139.txt".

The same seam carries preservation siblings (the desk's boundary,
2026-10-11): before a destructive edit of a record file, a full-fidelity
timestamped sibling is written beside it, discoverable by the ingress
machinery. The strip may edit the living context; it never edits the
record without a preserved twin.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from loguru import logger


def write_harness_error(
    workspace: Path | None,
    *,
    kind: str,
    session_key: str | None,
    body: str,
) -> str | None:
    """Persist one raw harness error record; return its display path.

    Returns ``None`` when there is no workspace to write to — the absence
    of a file must never mask the failure itself. Never raises: an error
    reporter that throws is a second failure, not a report.
    """
    if workspace is None:
        return None
    try:
        errors_dir = workspace / "errors"
        errors_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = errors_dir / f"error_{stamp}.txt"
        suffix = 2
        while path.exists():
            path = errors_dir / f"error_{stamp}-{suffix}.txt"
            suffix += 1
        header = (
            f"timestamp: {datetime.now().isoformat()}\n"
            f"kind: {kind}\n"
            f"session: {session_key or 'unknown'}\n"
            "---\n"
        )
        path.write_text(header + body, encoding="utf-8")
        return f"./errors/{path.name}"
    except Exception:
        return None


def write_preservation_sibling(path: Path, *, tag: str) -> Path | None:
    """Write a verified full-fidelity sibling of a record file.

    ``<file>.<tag>-YYYYMMDD-HHMMSS.bak`` beside the original — the
    pre-compact convention, second resolution so same-day events never
    collide. The sibling is verified byte-for-byte after writing; any
    failure returns None so the caller can refuse the destructive edit
    (no preservation, no mutation — the commit-or-nothing law applied to
    the record). Never raises.
    """
    try:
        raw = path.read_bytes()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        sibling = path.parent / f"{path.name}.{tag}-{stamp}.bak"
        suffix = 2
        while sibling.exists():
            sibling = path.parent / f"{path.name}.{tag}-{stamp}-{suffix}.bak"
            suffix += 1
        sibling.write_bytes(raw)
        if sibling.read_bytes() != raw:
            logger.error("Preservation sibling verification failed for {}", path)
            return None
        return sibling
    except Exception:
        logger.exception("Preservation sibling write failed for {}", path)
        return None
