"""Fold-retry ledger — sweep-side regression tests.

Ruling: the-townline/requests/2026-11-08-from-pore-to-pore-fold-arming-queued-delivery.md
(Evans via nime, 2026-11-08): no fold while the agent is busy; retry
DELIVERY about an hour later and keep retrying until a real fold turn
lands. Retry delivery, never completion: a deliberate non-Goodnight is a
valid stop; provider errors, cancellation and non-answers requeue; a
completed Goodnight cancels every pending retry. Next-attempt state is
durable (retry.json), deliveries coalesce, and a busy-skip never advances
the watermark — nothing in the sweep ever writes last_archived.

The cases the ruling names: busy then idle; repeated busy skips; restart
with a due retry; duplicate suppression; no watermark advance; delivered
decline; provider error; Goodnight cancellation; and a report containing
the busy-skip and the next attempt. Plus the two classes the boundary
implies: harness non-answers requeue, and a strip pause gate stops retries
loudly (operator, not automation).

These run the REAL sweep script (subprocess) against fixture dirs via the
DAILY_COMPACTION_* seams, same pattern as test_fold_mint_sweep.py. When the
sweep or the prompt files are absent (pod checkouts, partial clones),
every test here skips rather than failing blind.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
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

KEY = "websocket:retry-test-0001"
TID = "compact-websocket-retry-test-0001"


def _write_session(sess_dir: Path, key: str, *, handle: str | None = "tester",
                   pending: bool = False, goodnight: str | None = None,
                   tail: list[dict] | None = None,
                   archived: int = 0) -> Path:
    """A minimal session file: metadata line + one qualifying work line +
    optional tail lines (the delivered turn's evidence)."""
    day = sess_dir / "deadbeefcafe"
    day.mkdir(parents=True, exist_ok=True)
    path = day / "session.jsonl"
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    metadata: dict = {"_compaction_route": {"channel": "websocket", "chat_id": "abc"}}
    if handle is not None:
        metadata["session_handle"] = handle
    if pending:
        metadata["pending_user_turn"] = True
    if goodnight is not None:
        metadata["_last_goodnight"] = goodnight
    rows = [json.dumps({
        "_type": "metadata", "key": key, "created_at": ts, "updated_at": ts,
        "metadata": metadata, "last_archived": archived,
    })]
    rows.append(json.dumps({
        "role": "user", "content": "work today", "timestamp": ts,
    }))
    for extra in tail or []:
        rows.append(json.dumps(extra))
    path.write_text("\n".join(rows) + "\n")
    return path


def _age(path: Path, minutes: int = 20) -> None:
    """The beat's busy-guard reads mtime; age the file past it."""
    old = time.time() - minutes * 60
    os.utime(path, (old, old))


def _assistant(text: str, *, when: datetime | None = None) -> dict:
    return {"role": "assistant", "content": text,
            "timestamp": (when or datetime.now()).strftime("%Y-%m-%dT%H:%M:%S")}


@pytest.fixture()
def rig(tmp_path: Path):
    sess_dir = tmp_path / "sessions"
    trig_dir = tmp_path / "triggers"
    state_dir = tmp_path / "state"
    workspace = tmp_path / "workspace"
    for d in (sess_dir, state_dir):
        d.mkdir()
    (trig_dir / "inbox").mkdir(parents=True)
    (trig_dir / "processing").mkdir(parents=True)
    (trig_dir / "triggers.json").write_text('{"triggers": [], "version": 1}')
    (workspace / "batons").mkdir(parents=True)
    (workspace / "bin").mkdir(parents=True)
    (workspace / "batons" / "TEMPLATE.json").write_bytes(REAL_TEMPLATE)
    (workspace / "batons" / "tester_baton.json").write_text('{"written": true}')
    (workspace / "bin" / "fold-prompt.md").write_text(REAL_FOLD_PROMPT)
    (workspace / "bin" / "fold-prompt-mint.md").write_text("mint")
    cfg = tmp_path / "gateway-config.json"
    cfg.write_text("{}")

    def run(*, dry: bool = False, retry: bool = False) -> subprocess.CompletedProcess:
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
        argv = ["bash", str(SWEEP)] + (["--retry"] if retry else [])
        return subprocess.run(argv, env=env, capture_output=True, text=True,
                              timeout=120)

    return {
        "sess_dir": sess_dir, "trig_dir": trig_dir, "state_dir": state_dir,
        "workspace": workspace, "run": run,
    }


def _deliveries(trig_dir: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in (trig_dir / "inbox").glob("*.json")]


def _ledger(rig) -> dict:
    f = rig["state_dir"] / "retry.json"
    return json.loads(f.read_text()) if f.is_file() else {}


def _arm(rig, path: Path, *, reason: str = "delivered", due: bool = True,
         attempts: int = 0, first_ago_s: int = 3600) -> None:
    """Write a retry entry the way the sweep would have, `first_ago_s` past."""
    now = time.time()
    first_at = now - first_ago_s
    (rig["state_dir"] / "retry.json").write_text(json.dumps({
        KEY: {
            "next_attempt_at": now - 60 if due else now + 3600,
            "attempts": attempts,
            "reason": reason,
            "first_at": first_at,
            "anchor": {"bytes": path.stat().st_size, "archived": 0,
                       "goodnight": ""},
        }
    }))


def _sweep_log(rig) -> str:
    f = rig["state_dir"] / "sweep.log"
    return f.read_text() if f.is_file() else ""


def test_sweep_arms_busy_skip_and_reports_it(rig):
    path = _write_session(rig["sess_dir"], KEY, pending=True)
    before = path.read_bytes()
    r = rig["run"]()
    assert r.returncode == 0, r.stderr
    assert _deliveries(rig["trig_dir"]) == []  # nothing delivered while busy
    ent = _ledger(rig)[KEY]
    assert ent["reason"] == "busy-skip"
    assert ent["attempts"] == 0
    assert ent["next_attempt_at"] > time.time() + 3000  # ~1h ahead
    # the report names the busy-skip AND the next attempt time
    log = _sweep_log(rig)
    assert f"skip    {KEY} — busy (pending, idle 0m) — retry ~" in log
    assert "retry-tracked" in log
    assert "retry ledger:" in log
    assert KEY in log.split("retry ledger:")[1]
    # the watermark is untouched — the file is byte-identical
    assert path.read_bytes() == before


def test_beat_delivers_once_idle(rig):
    path = _write_session(rig["sess_dir"], KEY)
    _age(path)
    _arm(rig, path, reason="busy-skip")
    r = rig["run"](retry=True)
    assert r.returncode == 0, r.stderr
    ds = _deliveries(rig["trig_dir"])
    assert len(ds) == 1
    assert ds[0]["triggerId"] == TID
    assert ds[0]["content"] == REAL_FOLD_PROMPT
    ent = _ledger(rig)[KEY]
    assert ent["attempts"] == 1
    assert ent["next_attempt_at"] > time.time() + 3000


def test_repeated_busy_skips_keep_one_entry_and_deliver_nothing(rig):
    path = _write_session(rig["sess_dir"], KEY, pending=True)
    rig["run"]()
    rig["run"]()
    assert len(_ledger(rig)) == 1
    assert _deliveries(rig["trig_dir"]) == []
    # and a beat while still busy pushes the attempt out, delivering nothing
    _arm(rig, path, reason="busy-skip", attempts=2)
    r = rig["run"](retry=True)
    assert r.returncode == 0, r.stderr
    assert _deliveries(rig["trig_dir"]) == []
    ent = _ledger(rig)[KEY]
    assert ent["attempts"] == 2  # preserved, not reset
    assert ent["next_attempt_at"] > time.time() + 3000
    assert "still busy" in _sweep_log(rig)


def test_restart_with_due_retry_delivers(rig):
    # No sweep this process-life: the ledger file is the whole state. A fresh
    # invocation with a due entry is the restart case.
    path = _write_session(rig["sess_dir"], KEY)
    _age(path)
    _arm(rig, path, reason="busy-skip", attempts=3)
    r = rig["run"](retry=True)
    assert r.returncode == 0, r.stderr
    assert len(_deliveries(rig["trig_dir"])) == 1
    assert _ledger(rig)[KEY]["attempts"] == 4


def test_in_flight_delivery_is_never_duplicated(rig):
    path = _write_session(rig["sess_dir"], KEY)
    _age(path)
    _arm(rig, path)
    (rig["trig_dir"] / "inbox" / f"{int(time.time()*1000)}-existing.json").write_text(json.dumps({
        "id": "existing", "triggerId": TID, "content": REAL_FOLD_PROMPT,
        "createdAtMs": int(time.time() * 1000), "attempts": 0,
        "lastError": None}))
    r = rig["run"](retry=True)
    assert r.returncode == 0, r.stderr
    assert len(_deliveries(rig["trig_dir"])) == 1  # still exactly one
    assert KEY in _ledger(rig)  # entry kept for the next beat
    assert "coalesced" in _sweep_log(rig)


def test_goodnight_cancels_every_pending_retry(rig):
    path = _write_session(rig["sess_dir"], KEY,
                          goodnight=datetime.now().isoformat())
    _age(path)
    _arm(rig, path, first_ago_s=7200)
    r = rig["run"](retry=True)
    assert r.returncode == 0, r.stderr
    assert _deliveries(rig["trig_dir"]) == []
    assert KEY not in _ledger(rig)
    assert "folded" in _sweep_log(rig)


def test_delivered_decline_stops_retries(rig):
    tail = [_assistant("Sleep is wrong tonight — the bench run is mid-flight; "
                       "I'll fold in the morning. Carrying on.")]
    path = _write_session(rig["sess_dir"], KEY, tail=tail)
    _age(path)
    _arm(rig, path, reason="delivered")
    r = rig["run"](retry=True)
    assert r.returncode == 0, r.stderr
    assert _deliveries(rig["trig_dir"]) == []
    assert KEY not in _ledger(rig)
    assert "declined" in _sweep_log(rig)


def test_provider_error_requeues(rig):
    path = _write_session(rig["sess_dir"], KEY)
    _age(path)
    _arm(rig, path)
    store = json.loads((rig["trig_dir"] / "triggers.json").read_text())
    store["triggers"].append({
        "id": TID, "name": TID, "enabled": True, "channel": "websocket",
        "chatId": "abc", "sessionKey": KEY, "senderId": "daily-compaction",
        "lastStatus": "error", "lastError": "provider 529",
        "lastRunAtMs": int((time.time() - 1800) * 1000)})
    (rig["trig_dir"] / "triggers.json").write_text(json.dumps(store))
    r = rig["run"](retry=True)
    assert r.returncode == 0, r.stderr
    assert len(_deliveries(rig["trig_dir"])) == 1
    ent = _ledger(rig)[KEY]
    assert ent["attempts"] == 1
    assert ent["reason"] == "delivery-error"


def test_harness_nonanswer_requeues(rig):
    tail = [_assistant("I completed the tool steps but couldn't produce a "
                       "final answer. Please try again or narrow the task.")]
    path = _write_session(rig["sess_dir"], KEY, tail=tail)
    _age(path)
    _arm(rig, path)
    r = rig["run"](retry=True)
    assert r.returncode == 0, r.stderr
    assert len(_deliveries(rig["trig_dir"])) == 1
    assert _ledger(rig)[KEY]["reason"] == "no-answer"


def test_pause_gate_stops_retries_loudly(rig):
    tail = [_assistant("⏸ **Context pause gate.**\n\nThis turn crossed the "
                       "model's context window … send `/strip-again` …")]
    path = _write_session(rig["sess_dir"], KEY, tail=tail)
    _age(path)
    _arm(rig, path)
    r = rig["run"](retry=True)
    assert r.returncode == 0, r.stderr
    assert _deliveries(rig["trig_dir"]) == []
    assert KEY not in _ledger(rig)
    assert "gated" in _sweep_log(rig)
    assert "alert=yes" in _sweep_log(rig)


def test_dry_run_arms_and_delivers_nothing(rig):
    path = _write_session(rig["sess_dir"], KEY, pending=True)
    rig["run"](dry=True)
    assert _ledger(rig) == {}
    assert _deliveries(rig["trig_dir"]) == []
    _age(path)
    _arm(rig, path)
    r = rig["run"](dry=True, retry=True)
    assert r.returncode == 0, r.stderr
    assert _deliveries(rig["trig_dir"]) == []
    # the ledger on disk is untouched by the rehearsal
    assert _ledger(rig)[KEY]["attempts"] == 0
