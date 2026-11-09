#!/usr/bin/env python3
"""Generate a v1 activation-registry image from current session state.

Evans 2026-11-08: "Just take an image of current state" — every existing
handle-bearing session is registered ACTIVE with all surfaces; the Munu
incident session is registered INERT (deliberate adoption via Tuto/nime,
never a silent conversion or deletion).

The Manager workflow (dova/soro's design) owns writes to the live registry;
this tool only EMITS the image for review and approval. The harness never
writes the registry, and this script is run by an operator, not an agent
turn doing incidental work.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from nanobot.identity.activation import SURFACES, ActivationStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Workspace whose sessions are scanned (default: gateway default).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write the image here (default: stdout).",
    )
    args = parser.parse_args()

    from nanobot.session.manager import SessionManager

    manager = SessionManager(args.workspace or Path.home() / ".nanobot" / "workspace")
    entries: list[dict[str, object]] = []
    # Lolu's 2026-11-08 boundary pass: these are never registered, even if a
    # future snapshot run finds their session files. Exclusion is deliberate,
    # not a cue to reactivate them later by inertia.
    excluded_handles = {"hise", "tiyu"}
    inert_handles = {
        "munu": "accidental acceptance session; deliberate adoption via Tuto (sponsor: nime) — inert until onboarding completes",
        "joli": "adjudicated by lolu boundary pass 2026-11-08 — retired ROSTER #3 (desk, 2026-10-14); quiet since 2026-09-30; reactivation only via explicit adoption (§10.1)",
        "peti": "adjudicated by lolu boundary pass 2026-11-08 — retired ROSTER #3 (desk, 2026-10-14); accidental deal resident (deal-kevu) exhibited as transient per Evans ruling 2026-10-15; history preserved in place; reactivation only via explicit adoption (§10.1)",
        "gane": "adjudicated by lolu boundary pass 2026-11-08 — dream consolidation attempt (single run, 2026-09-17); attempts do not pass the gate implicitly (§2); futures per §10.1",
        "havi": "adjudicated by lolu boundary pass 2026-11-08 — dream consolidation attempt (single run, 2026-09-17); attempts do not pass the gate implicitly (§2); futures per §10.1",
        "jeno": "adjudicated by lolu boundary pass 2026-11-08 — dream consolidation attempt (single run, 2026-09-16); attempts do not pass the gate implicitly (§2); futures per §10.1",
        "besa": "adjudicated by lolu boundary pass 2026-11-08 — dream consolidation attempt (single run, 2026-09-16); accidental deal resident (deal-studi) exhibited as transient per Evans ruling 2026-10-15; futures per §10.1",
    }
    active_purposes = {
        "royi": "adjudicated by lolu boundary pass 2026-11-08 — active resident (Valley face ready 10-21; author of 2026-11-08-munu-incident.md; live work triggers bound to key; Evans correspondence 11-02)",
    }

    for row in manager.list_sessions():
        key = row.get("key")
        if not isinstance(key, str) or not key:
            continue
        metadata = manager.read_session_metadata(key) or {}
        meta = metadata.get("metadata") if isinstance(metadata, dict) else None
        handle = meta.get("session_handle") if isinstance(meta, dict) else None
        if not isinstance(handle, str) or not handle.strip():
            continue  # no handle: transport addressing only, not an agent identity
        handle = handle.strip()
        if handle in excluded_handles:
            continue
        if handle in inert_handles:
            entries.append({
                "handle": handle,
                "session_key": key,
                "state": "inert",
                "approved_by": "Evans",
                "purpose": inert_handles[handle],
                "activated_at": "",
                "surfaces": [],
            })
            continue
        entries.append({
            "handle": handle,
            "session_key": key,
            "state": "active",
            "approved_by": "Evans",
            "purpose": active_purposes.get(handle, "v1 image: pre-existing resident at gate introduction"),
            "activated_at": "2026-11-08",
            "surfaces": list(SURFACES),
        })

    image = ActivationStore.build_image(entries)
    payload = json.dumps(image, indent=2, ensure_ascii=False) + "\n"
    if args.out is not None:
        args.out.write_text(payload, encoding="utf-8")
        print(f"wrote {len(entries)} entries to {args.out}")
    else:
        print(payload)


if __name__ == "__main__":
    main()
