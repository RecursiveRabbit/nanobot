"""Universal first-fold minting — sweep-side regression tests.

Request: the-townline/requests/2026-10-27-from-nime-to-pore-universal-baton-minting.md
(nime → pore, 2026-10-27). The sweep (workspace bin/daily-compaction.sh)
routes each qualified session to one of three fold contents:

- FOLD    — an existing, written baton: the standard Goodnight fold prompt.
- MINT    — no baton yet: copy batons/TEMPLATE.json to
            batons/<handle>_baton.json and deliver the minting prompt. A
            baton still byte-identical to the template is an un-completed
            mint: re-deliver the minting prompt, never re-copy.
- COMPACT — no session handle: legacy /compact (the sweep does not mint
            handles).

The laws under test:
- the starter is created AT THE FOLD, never early (dry runs and
  unqualified sessions mint nothing)
- the existing Goodnight path is unchanged (written batons get the locked
  fold prompt; their files are untouched)
- no validator: the template byte-compare is the only mint-state check

These run the REAL sweep script (subprocess) against fixture dirs via the
DAILY_COMPACTION_* seams. The sweep lives in the sibling workspace repo;
SWEEP_WORKSPACE overrides the default side-by-side layout. When the sweep
or the skeleton is absent (pod checkouts, partial clones), every test here
skips rather than failing blind.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

NANOBOT_REPO = Path(__file__).resolve().parents[2]
WORKSPACE = Path(
    os.environ.get("SWEEP_WORKSPACE", NANOBOT_REPO.parent / "workspace")
)
SWEEP = WORKSPACE / "bin" / "daily-compaction.sh"
TEMPLATE_FILE = WORKSPACE / "batons" / "TEMPLATE.json"
FOLD_PROMPT_FILE = WORKSPACE / "bin" / "fold-prompt.md"
MINT_PROMPT_FILE = WORKSPACE / "bin" / "fold-prompt-mint.md"

pytestmark = pytest.mark.skipif(
    not all(p.is_file() for p in (SWEEP, TEMPLATE_FILE, FOLD_PROMPT_FILE, MINT_PROMPT_FILE)),
    reason="sweep or baton skeleton not present in the sibling workspace",
)

REAL_TEMPLATE = TEMPLATE_FILE.read_bytes() if TEMPLATE_FILE.is_file() else b""
REAL_FOLD_PROMPT = FOLD_PROMPT_FILE.read_text().strip() if FOLD_PROMPT_FILE.is_file() else ""
REAL_MINT_PROMPT = MINT_PROMPT_FILE.read_text().strip() if MINT_PROMPT_FILE.is_file() else ""


def _write_session(sess_dir: Path, key: str, *, handle: str | None,
                   work_lines: int = 1, line_age_min: int = 0) -> Path:
    """A minimal session file: metadata line + qualifying work lines."""
    day = sess_dir / "deadbeefcafe"
    day.mkdir(parents=True, exist_ok=True)
    path = day / "session.jsonl"
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    metadata: dict = {"_compaction_route": {"channel": "websocket", "chat_id": "abc"}}
    if handle is not None:
        metadata["session_handle"] = handle
    rows = [json.dumps({
        "_type": "metadata", "key": key, "created_at": ts, "updated_at": ts,
        "metadata": metadata, "last_archived": 0,
    })]
    line_ts = (datetime.now() - timedelta(minutes=line_age_min)).strftime(
        "%Y-%m-%dT%H:%M:%S")
    for i in range(work_lines):
        rows.append(json.dumps({
            "role": "user", "content": f"work {i}", "timestamp": line_ts,
        }))
    path.write_text("\n".join(rows) + "\n")
    return path


@pytest.fixture()
def rig(tmp_path: Path):
    sess_dir = tmp_path / "sessions"
    trig_dir = tmp_path / "triggers"
    state_dir = tmp_path / "state"
    workspace = tmp_path / "workspace"
    for d in (sess_dir, state_dir):
        d.mkdir()
    (trig_dir / "inbox").mkdir(parents=True)
    (trig_dir / "triggers.json").write_text('{"triggers": [], "version": 1}')
    (workspace / "batons").mkdir(parents=True)
    (workspace / "bin").mkdir(parents=True)
    (workspace / "batons" / "TEMPLATE.json").write_bytes(REAL_TEMPLATE)
    (workspace / "bin" / "fold-prompt.md").write_text(REAL_FOLD_PROMPT)
    (workspace / "bin" / "fold-prompt-mint.md").write_text(REAL_MINT_PROMPT)
    cfg = tmp_path / "gateway-config.json"
    cfg.write_text("{}")

    def run(*, dry: bool = False) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.update({
            "DAILY_COMPACTION_SESS_DIR": str(sess_dir),
            "DAILY_COMPACTION_TRIGGERS_DIR": str(trig_dir),
            "DAILY_COMPACTION_WORKSPACE": str(workspace),
            "DAILY_COMPACTION_STATE_DIR": str(state_dir),
            "DAILY_COMPACTION_GATEWAY_CONFIG": str(cfg),
            "DAILY_COMPACTION_NOTIFY": "0",
        })
        if dry:
            env["DAILY_COMPACTION_DRY_RUN"] = "1"
        return subprocess.run(
            ["bash", str(SWEEP)], env=env, capture_output=True, text=True,
            timeout=120,
        )

    return {
        "sess_dir": sess_dir, "trig_dir": trig_dir, "state_dir": state_dir,
        "workspace": workspace, "run": run,
    }


def _deliveries(trig_dir: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in (trig_dir / "inbox").glob("*.json")]


def test_first_fold_mints_the_starter(rig):
    """No baton at qualification → the sweep mints from the template and
    delivers the minting prompt with the session's own address in it."""
    _write_session(rig["sess_dir"], "websocket:mintme", handle="minty")
    result = rig["run"]()
    assert result.returncode == 0, result.stderr
    log = (rig["state_dir"] / "sweep.log").read_text()
    assert "[MINT]" in log

    baton = rig["workspace"] / "batons" / "minty_baton.json"
    assert baton.is_file()
    assert baton.read_bytes() == REAL_TEMPLATE  # verbatim handover

    deliveries = _deliveries(rig["trig_dir"])
    assert len(deliveries) == 1
    expected = REAL_MINT_PROMPT.replace("{handle_baton}", "batons/minty_baton.json")
    assert deliveries[0]["content"] == expected


def test_written_baton_keeps_the_standard_fold(rig):
    """The existing Goodnight path is unchanged: a written baton gets the
    locked fold prompt, and the sweep never touches the file."""
    _write_session(rig["sess_dir"], "websocket:veteran", handle="veteran")
    baton = rig["workspace"] / "batons" / "veteran_baton.json"
    baton.write_text('{"who": "veteran", "note": "my own working self"}')
    result = rig["run"]()
    assert result.returncode == 0, result.stderr
    assert "[FOLD]" in (rig["state_dir"] / "sweep.log").read_text()

    assert baton.read_text() == '{"who": "veteran", "note": "my own working self"}'
    deliveries = _deliveries(rig["trig_dir"])
    assert len(deliveries) == 1
    assert deliveries[0]["content"] == REAL_FOLD_PROMPT


def test_untouched_template_redelivers_the_mint_prompt(rig):
    """A baton still byte-identical to the template is an un-completed mint:
    the minting prompt goes out again and the file is NOT re-copied."""
    _write_session(rig["sess_dir"], "websocket:stalled", handle="stalled")
    baton = rig["workspace"] / "batons" / "stalled_baton.json"
    baton.write_bytes(REAL_TEMPLATE)
    mtime = baton.stat().st_mtime_ns
    result = rig["run"]()
    assert result.returncode == 0, result.stderr
    assert "[MINT]" in (rig["state_dir"] / "sweep.log").read_text()

    assert baton.read_bytes() == REAL_TEMPLATE
    assert baton.stat().st_mtime_ns == mtime  # no re-copy, no touch
    deliveries = _deliveries(rig["trig_dir"])
    assert len(deliveries) == 1
    assert deliveries[0]["content"] != REAL_FOLD_PROMPT
    assert "starter skeleton" in deliveries[0]["content"]


def test_handleless_session_stays_on_legacy_compact(rig):
    """No handle, no baton address: the sweep does not mint handles."""
    _write_session(rig["sess_dir"], "websocket:stranger", handle=None)
    result = rig["run"]()
    assert result.returncode == 0, result.stderr
    assert "[COMPACT]" in (rig["state_dir"] / "sweep.log").read_text()

    deliveries = _deliveries(rig["trig_dir"])
    assert len(deliveries) == 1
    assert deliveries[0]["content"] == "/compact"
    batons = {p.name for p in (rig["workspace"] / "batons").iterdir()}
    assert batons == {"TEMPLATE.json"}


def test_dry_run_mints_nothing(rig):
    """The starter is created at the fold, never by a rehearsal: a dry run
    reports the MINT route but writes no baton, no delivery, no trigger."""
    _write_session(rig["sess_dir"], "websocket:dryrun", handle="dryrun")
    result = rig["run"](dry=True)
    assert result.returncode == 0, result.stderr
    assert "[MINT]" in result.stdout

    assert not (rig["workspace"] / "batons" / "dryrun_baton.json").exists()
    assert _deliveries(rig["trig_dir"]) == []
    assert json.loads((rig["trig_dir"] / "triggers.json").read_text())["triggers"] == []


def test_unqualified_session_mints_nothing(rig):
    """Not at a fold, no starter: a session with no qualifying work lines is
    skipped whole, and no baton appears for it."""
    _write_session(rig["sess_dir"], "websocket:quietone", handle="quiet",
                   work_lines=1, line_age_min=60 * 30)  # older than the 24h window
    result = rig["run"]()
    assert result.returncode == 0, result.stderr
    log = (rig["state_dir"] / "sweep.log").read_text()
    assert "no new lines" in log
    assert "[MINT]" not in log

    assert not (rig["workspace"] / "batons" / "quiet_baton.json").exists()
    assert _deliveries(rig["trig_dir"]) == []


def test_off_shape_handle_is_never_a_path(rig):
    """A handle that fails the house shape law is never used as a filename:
    no mint, legacy /compact, and the report says why."""
    _write_session(rig["sess_dir"], "websocket:weird", handle="..evil")
    result = rig["run"]()
    assert result.returncode == 0, result.stderr
    log = (rig["state_dir"] / "sweep.log").read_text()
    assert "[COMPACT]" in log
    assert "MINT-SKIPPED(handle-shape)" in log

    deliveries = _deliveries(rig["trig_dir"])
    assert len(deliveries) == 1
    assert deliveries[0]["content"] == "/compact"
    assert not (rig["workspace"] / "batons" / "..evil_baton.json").exists()
    assert not (rig["workspace"] / "..evil_baton.json").exists()


def test_missing_template_falls_back_to_compact(rig):
    """No skeleton, no mint: batonless handled sessions keep the legacy path
    and the sweep says so instead of crashing."""
    (rig["workspace"] / "batons" / "TEMPLATE.json").unlink()
    _write_session(rig["sess_dir"], "websocket:orphan", handle="orphan")
    result = rig["run"]()
    assert result.returncode == 0, result.stderr
    log = (rig["state_dir"] / "sweep.log").read_text()
    assert "[COMPACT]" in log
    assert "MINT-SKIPPED(no-template)" in log

    deliveries = _deliveries(rig["trig_dir"])
    assert len(deliveries) == 1
    assert deliveries[0]["content"] == "/compact"
    assert not (rig["workspace"] / "batons" / "orphan_baton.json").exists()
