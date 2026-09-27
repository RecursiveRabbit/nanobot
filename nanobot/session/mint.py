"""Session minting — the nanobot process as root of session provenance.

Spec: orient/session-minting.md. On process boot, nanobot generates an
RSA-3072 keypair in memory; the private key never touches disk. The public
half is announced to ``~/.nanobot/mint/`` (and from there filed into git by
the session filing service — the transparency log). Only this process can
sign with it: the process marks itself undumpable so same-user children
cannot ptrace the key out of memory. The human is root; that boundary is
deliberate, the same one secure boot draws.

The gateway exposes the mint at ``POST /v1/mint``; ``nanobot commit`` is a
thin client. Verification lives in orient.py (``minted_key``): cert pinned,
envelope signature valid, not a subagent, transcript unaltered since mint,
stamped key spoken in the record — then peel and run.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from loguru import logger

MINT_DIR = Path.home() / ".nanobot" / "mint"
DEFAULT_SESSIONS_ROOT = Path.home() / ".nanobot" / "sessions"

# Metadata keys that mark a session as a fork/subagent session. A fork is
# continuity, not corroboration: same mind, two files, zero additional trust.
# Harness-stamped in the envelope; never taken from the caller.
_SUBAGENT_METADATA_MARKERS = (
    "is_subagent",
    "spawned_by",
    "parent_session",
    "forked_from",
    "session_handle",  # volatile fork metadata; present on forked sessions
)

_identity: "MintIdentity | None" = None


def _boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return "unknown"


def _harden_process() -> None:
    """Mark this process undumpable (Linux): same-user processes — including
    our own children — can no longer ptrace us or read /proc/<pid>/mem.
    Root is unaffected; root was always root."""
    if os.uname().sysname != "Linux":
        return
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        PR_SET_DUMPABLE = 1
        if libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
            logger.warning("mint: prctl(PR_SET_DUMPABLE) failed: {}", ctypes.get_errno())
    except (OSError, AttributeError) as exc:
        logger.warning("mint: could not harden process: {}", exc)


def _repo_revision() -> str:
    try:
        root = Path(__file__).resolve().parents[2]
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                           capture_output=True, timeout=5)
        return r.stdout.decode().strip() if r.returncode == 0 else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def canonical(payload: dict[str, Any]) -> bytes:
    """The signed form. Must match orient.py's reconstruction byte-for-byte:
    the envelope minus ``signature``, sorted keys, compact separators."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


class MintIdentity:
    """A per-boot process identity. Generate once, hold in memory, sign."""

    def __init__(self) -> None:
        self._key = rsa.generate_private_key(
            public_exponent=65537, key_size=3072)
        self.public_pem = self._key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        self.cert_id = hashlib.sha256(self.public_pem.encode()).hexdigest()[:16]
        self.minted_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _harden_process()
        self._announce()

    def _announce(self) -> None:
        """Publish the public half: cert id, PEM, boot id, revision. This is
        the transparency record — filed into git, witnessed by every pull.
        A second announcement claiming this boot is an alarm."""
        try:
            from nanobot import __version__
        except ImportError:
            __version__ = "unknown"
        MINT_DIR.mkdir(parents=True, exist_ok=True)
        announcement = {
            "cert_id": self.cert_id,
            "public_pem": self.public_pem,
            "boot_id": _boot_id(),
            "pid": os.getpid(),
            "minted_utc": self.minted_utc,
            "nanobot_version": __version__,
            "repo_revision": _repo_revision(),
        }
        out = MINT_DIR / f"announce-{self.minted_utc.replace(':', '')}-{self.cert_id}.json"
        out.write_text(json.dumps(announcement, indent=1) + "\n")
        logger.info("mint: process identity {} announced at {}", self.cert_id, out)

    def sign(self, payload: bytes) -> bytes:
        return self._key.sign(payload, padding.PKCS1v15(), hashes.SHA256())


def init() -> MintIdentity:
    """Create (or return) this process's mint identity. Called at boot by
    the gateway and the terminal agent."""
    global _identity
    if _identity is None:
        _identity = MintIdentity()
    return _identity


def identity() -> MintIdentity:
    if _identity is None:
        raise RuntimeError("mint identity not initialized; call mint.init() at boot")
    return _identity


def detect_subagent(session_path: Path) -> bool:
    """Harness-side fork detection, from the session's own metadata record —
    never from the caller, never from model-authored content."""
    try:
        with session_path.open(errors="replace") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict) and rec.get("_type") == "metadata":
                    data = rec.get("data", rec)
                    if isinstance(data, dict) and any(
                            k in data for k in _SUBAGENT_METADATA_MARKERS):
                        return True
    except OSError:
        pass
    # WebUI forks carry no transcript marker; their lineage lives in the
    # session index title ("Fork: ..."). The transcript filename is the
    # base64 of the index key — decode it and look the session up.
    try:
        key = base64.b64decode(session_path.stem.encode()).decode()
        idx = session_path.parent / ".webui_session_index.json"
        for entry in json.loads(idx.read_text()).get("sessions", []):
            if entry.get("key") == key:
                title = entry.get("title") or ""
                if title.startswith("Fork:") or entry.get("forked_from"):
                    return True
    except Exception:
        pass
    return False


def build_envelope(ident: MintIdentity, session_path: Path,
                   keys: dict[str, str], *, subagent: bool) -> dict[str, Any]:
    try:
        from nanobot import __version__
    except ImportError:
        __version__ = "unknown"
    env: dict[str, Any] = {
        "harness": "nanobot",
        "harness_version": __version__,
        "cert_id": ident.cert_id,
        "session": session_path.name,
        "session_sha256": hashlib.sha256(session_path.read_bytes()).hexdigest(),
        "minted_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "subagent": bool(subagent),
        "keys": keys,
    }
    env["signature"] = base64.b64encode(ident.sign(canonical(env))).decode()
    return env


def mint_session(session_path: Path, keys: dict[str, str],
                 outdir: Path, *, sessions_root: Path = DEFAULT_SESSIONS_ROOT
                 ) -> tuple[Path, Path]:
    """Mint a session file as a witness artifact: copy the transcript and
    write the signed envelope beside it in ``outdir`` (orient's sessions/).

    Returns (envelope_path, transcript_copy_path)."""
    session_path = session_path.expanduser().resolve()
    sessions_root = sessions_root.expanduser().resolve()
    if not session_path.is_file():
        raise FileNotFoundError(f"no such session file: {session_path}")
    if session_path.suffix != ".jsonl" or session_path.name.endswith(".checkpoint.json"):
        raise ValueError(f"not a session transcript: {session_path.name}")
    try:
        session_path.relative_to(sessions_root)
    except ValueError:
        raise ValueError(f"refusing to mint outside {sessions_root}: {session_path}")

    ident = identity()
    outdir = outdir.expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    name = f"{session_path.parent.name}-{session_path.stem}"
    transcript_copy = outdir / f"{name}.jsonl"
    shutil.copyfile(session_path, transcript_copy)
    env = build_envelope(ident, transcript_copy, keys,
                         subagent=detect_subagent(session_path))
    envelope_path = outdir / f"{name}.mint.json"
    envelope_path.write_text(json.dumps(env, indent=1) + "\n")
    logger.info("mint: {} minted as witness (cert {})", session_path.name, ident.cert_id)
    return envelope_path, transcript_copy
