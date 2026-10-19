"""The run record must never stamp "ok" over a provider error.

tuto №12 / studi №14 (2026-10-17/18): a health-check shift died
pre-cognition on a budget-class 403 and the run store recorded
``status: ok`` with the raw error body as the response.  Delivery is not
cognition — the turn's stop reason (stamped onto the outbound metadata by
loop._assemble_outbound) drives the record's status, and a model-failed
turn raises CronJobModelError so the job state stamps error too.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nanobot.bus.events import OutboundMessage
from nanobot.cron.bound_runner import run_bound_cron_job
from nanobot.cron.service import CronJobModelError, CronService
from nanobot.cron.types import CronJob, CronPayload


class _FakeTools:
    def get(self, name: str):  # no cron tool registered in the fake
        return None


class _FakeAgent:
    def __init__(self, response: OutboundMessage | None) -> None:
        self.tools = _FakeTools()
        self._response = response

    async def submit_cron_turn(self, msg):
        return self._response


def _job() -> CronJob:
    return CronJob(
        id="job-1",
        name="the-health-check-daily-shift",
        payload=CronPayload(
            message="work the beat",
            session_key="websocket:tuto",
            origin_channel="websocket",
            origin_chat_id="tuto",
        ),
    )


def _read_records(store_path: Path) -> list[dict]:
    runs_dir = store_path.parent / "runs"
    records = []
    for path in sorted(runs_dir.glob("*.json")):
        records.append(json.loads(path.read_text()))
    return records


def _final_record(records: list[dict]) -> dict:
    # queued first, then the terminal write; same run_id
    assert records[-1]["status"] != "queued"
    return records[-1]


async def test_ok_turn_records_ok(tmp_path) -> None:
    cron = CronService(tmp_path / "jobs.json")
    agent = _FakeAgent(
        OutboundMessage(
            channel="websocket",
            chat_id="tuto",
            content="№15 filed clean.",
            metadata={"_stop_reason": "stop"},
        )
    )

    result = await run_bound_cron_job(_job(), agent=agent, cron=cron)

    assert result == "№15 filed clean."
    record = _final_record(_read_records(cron.store_path))
    assert record["status"] == "ok"
    assert record["stop_reason"] == "stop"
    assert record["response"] == "№15 filed clean."


async def test_provider_error_turn_records_error_and_raises(tmp_path) -> None:
    cron = CronService(tmp_path / "jobs.json")
    error_body = (
        "Error: {'message': 'Budget limit exceeded (monthly limit). "
        "Contact your org admin. $255.39 of $250.00 used.', 'code': 403}"
    )
    agent = _FakeAgent(
        OutboundMessage(
            channel="websocket",
            chat_id="tuto",
            content=error_body,
            metadata={"_stop_reason": "error"},
        )
    )

    with pytest.raises(CronJobModelError, match="provider error"):
        await run_bound_cron_job(_job(), agent=agent, cron=cron)

    record = _final_record(_read_records(cron.store_path))
    assert record["status"] == "error"
    assert record["stop_reason"] == "error"
    # The raw error text is preserved for forensics — the record tells the
    # truth about WHAT died, not just that something did.
    assert record["response"] == error_body


async def test_silent_turn_records_ok(tmp_path) -> None:
    # No outbound at all (suppressed response) keeps the historic "ok"
    # semantics — silence is lawful, it is not a failure.
    cron = CronService(tmp_path / "jobs.json")
    agent = _FakeAgent(None)

    result = await run_bound_cron_job(_job(), agent=agent, cron=cron)

    assert result == ""
    record = _final_record(_read_records(cron.store_path))
    assert record["status"] == "ok"
    assert record["stop_reason"] is None


async def test_model_error_marks_job_state_error(tmp_path) -> None:
    # Through the real service path: on_job raising CronJobModelError stamps
    # the job's last_status error without a traceback-level log.
    from nanobot.cron.service import CronService as RealCronService

    async def on_job(job: CronJob):
        raise CronJobModelError("turn ended on a provider error: budget")

    cron = RealCronService(tmp_path / "jobs.json", on_job=on_job)
    job = _job()
    await cron._execute_job(job)

    assert job.state.last_status == "error"
    assert "provider error" in (job.state.last_error or "")
