"""The errors/ door (Evans 2026-10-10): one directory per agent workspace
that receives raw harness error records, auditable at leisure.

"We can send all harness errors there, audit the lot at our leisure."
The writer is shared from day one; individual failure sites adopt it as
they touch it. A failure notice names the file so the record is
pointable: "the raw error is stored at ./errors/error_20261010-172139.txt".
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


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
