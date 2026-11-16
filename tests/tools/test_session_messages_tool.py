import asyncio
import json
from pathlib import Path
from typing import Callable
from unittest.mock import patch

import pytest

from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.session_messages import (
    SessionMessageStatusTool,
    ListSessionsTool,
    SendSessionMessageTool,
    SessionMessageError,
)
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import ToolsConfig
from nanobot.session.manager import SessionManager
from nanobot.session.session_handles import SessionHandle, SessionHandleResolver
from nanobot.session.session_messages import (
    SESSION_MESSAGE_METADATA_KEY,
    session_message_envelope,
)


def _persist(manager: SessionManager, *keys: str) -> None:
    for key in keys:
        manager.save(manager.get_or_create(key))


def _handle(manager: SessionManager, key: str) -> SessionHandle:
    handle = SessionHandleResolver(manager).handle_for_session(key)
    assert handle is not None
    return handle


class _Timer:
    def __init__(self, callback: Callable[[], None]) -> None:
        self.callback = callback
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        if not self.cancelled:
            self.callback()


class _Scheduler:
    def __init__(self) -> None:
        self.calls: list[tuple[float, _Timer]] = []

    def __call__(self, delay: float, callback: Callable[[], None]) -> _Timer:
        timer = _Timer(callback)
        self.calls.append((delay, timer))
        return timer


def test_config_and_tool_schema_keep_only_the_basic_reply_contract(
    tmp_path: Path,
) -> None:
    tool = SendSessionMessageTool(
        sessions=SessionManager(tmp_path),
        bus=MessageBus(),
    )

    assert ToolsConfig.model_fields["max_session_messages_per_minute"].default == 6
    assert tool.parameters["required"] == ["to", "content", "expect_reply"]
    timeout = tool.parameters["properties"]["reply_timeout_seconds"]
    assert (timeout["minimum"], timeout["maximum"]) == (5, 60)


@pytest.mark.asyncio
async def test_list_sessions_includes_all_persisted_channels_except_current(
    tmp_path: Path,
) -> None:
    sessions = SessionManager(tmp_path)
    _persist(sessions, "websocket:current", "telegram:other", "slack:team")
    tool = ListSessionsTool(sessions)

    with request_context(RequestContext(
        channel="websocket",
        chat_id="current",
        session_key="websocket:current",
    )):
        result = json.loads(await tool.execute())

    assert set(result) == {
        f"@{_handle(sessions, 'telegram:other').name}",
        f"@{_handle(sessions, 'slack:team').name}",
    }


@pytest.mark.asyncio
async def test_send_publishes_user_input_to_the_existing_target(
    tmp_path: Path,
) -> None:
    sessions = SessionManager(tmp_path)
    _persist(sessions, "websocket:source", "telegram:target")
    bus = MessageBus()
    tool = SendSessionMessageTool(sessions=sessions, bus=bus)
    target = _handle(sessions, "telegram:target")

    sent_to = await tool.enqueue(
        source_session_key="websocket:source",
        target_handle=f"@{target.name}",
        content="Please review this.",
        expect_reply=False,
    )
    inbound = await bus.consume_inbound()
    envelope = session_message_envelope(inbound.metadata)

    assert sent_to.target_name == target.name
    assert sent_to.message_id
    assert inbound.channel == "system"
    assert inbound.chat_id == "telegram:target"
    assert inbound.session_key_override == "telegram:target"
    assert inbound.is_user_input
    assert inbound.content == "Please review this."
    assert envelope is not None
    assert envelope["source_session_key"] == "websocket:source"
    assert envelope["target_session_key"] == "telegram:target"
    assert inbound.metadata == {SESSION_MESSAGE_METADATA_KEY: envelope}


@pytest.mark.asyncio
async def test_send_fails_when_target_does_not_exist(tmp_path: Path) -> None:
    sessions = SessionManager(tmp_path)
    _persist(sessions, "websocket:source")
    bus = MessageBus()
    tool = SendSessionMessageTool(sessions=sessions, bus=bus)

    with pytest.raises(SessionMessageError, match="was not found"):
        await tool.enqueue(
            source_session_key="websocket:source",
            target_handle="@zzzz",
            content="Hello",
            expect_reply=False,
        )

    assert bus.inbound.empty()


@pytest.mark.asyncio
async def test_rate_limit_is_per_source_session_and_uses_a_rolling_minute(
    tmp_path: Path,
) -> None:
    sessions = SessionManager(tmp_path)
    _persist(sessions, "websocket:a", "websocket:b", "websocket:target")
    now = 0.0
    tool = SendSessionMessageTool(
        sessions=sessions,
        bus=MessageBus(),
        max_messages_per_minute=1,
        clock=lambda: now,
    )
    target = _handle(sessions, "websocket:target").name

    await tool.enqueue(
        source_session_key="websocket:a",
        target_handle=target,
        content="A1",
        expect_reply=False,
    )
    await tool.enqueue(
        source_session_key="websocket:b",
        target_handle=target,
        content="B1",
        expect_reply=False,
    )
    with pytest.raises(SessionMessageError, match="rate limit"):
        await tool.enqueue(
            source_session_key="websocket:a",
            target_handle=target,
            content="A2",
            expect_reply=False,
        )

    now = 61.0
    await tool.enqueue(
        source_session_key="websocket:a",
        target_handle=target,
        content="A3",
        expect_reply=False,
    )


@pytest.mark.asyncio
async def test_rate_limit_releases_expired_source_state_and_keeps_recent_sources(
    tmp_path: Path,
) -> None:
    sessions = SessionManager(tmp_path)
    _persist(
        sessions,
        "websocket:a",
        "websocket:b",
        "websocket:c",
        "websocket:target",
    )
    now = 0.0
    tool = SendSessionMessageTool(
        sessions=sessions,
        bus=MessageBus(),
        max_messages_per_minute=2,
        clock=lambda: now,
    )
    target = _handle(sessions, "websocket:target").name

    for source in ("websocket:a", "websocket:b"):
        await tool.enqueue(
            source_session_key=source,
            target_handle=target,
            content="initial",
            expect_reply=False,
        )
    now = 30.0
    await tool.enqueue(
        source_session_key="websocket:a",
        target_handle=target,
        content="recent",
        expect_reply=False,
    )

    now = 61.0
    await tool.enqueue(
        source_session_key="websocket:c",
        target_handle=target,
        content="trigger cleanup",
        expect_reply=False,
    )

    assert set(tool._sent_at) == {"websocket:a", "websocket:c"}
    await tool.enqueue(
        source_session_key="websocket:a",
        target_handle=target,
        content="within rolling window",
        expect_reply=False,
    )
    with pytest.raises(SessionMessageError, match="rate limit"):
        await tool.enqueue(
            source_session_key="websocket:a",
            target_handle=target,
            content="over limit",
            expect_reply=False,
        )


@pytest.mark.asyncio
async def test_reply_timeout_injects_a_user_input_back_into_the_source(
    tmp_path: Path,
) -> None:
    sessions = SessionManager(tmp_path)
    _persist(sessions, "websocket:source", "websocket:target")
    bus = MessageBus()
    scheduler = _Scheduler()
    tool = SendSessionMessageTool(
        sessions=sessions,
        bus=bus,
        schedule_later=scheduler,
    )
    target = _handle(sessions, "websocket:target")

    await tool.enqueue(
        source_session_key="websocket:source",
        target_handle=target.name,
        content="Question",
        expect_reply=True,
        reply_timeout_seconds=5,
    )
    await bus.consume_inbound()
    delay, timer = scheduler.calls[0]

    assert delay == 5
    timer.fire()
    await asyncio.sleep(0)
    timeout = await bus.consume_inbound()
    assert timeout.chat_id == "websocket:source"
    assert timeout.is_user_input
    assert timeout.content == f"No reply from @{target.name} after 5 seconds."


@pytest.mark.asyncio
async def test_reply_timeout_observes_background_delivery_failure(
    tmp_path: Path,
) -> None:
    sessions = SessionManager(tmp_path)
    _persist(sessions, "websocket:source", "websocket:target")
    bus = MessageBus()
    scheduler = _Scheduler()
    tool = SendSessionMessageTool(
        sessions=sessions,
        bus=bus,
        schedule_later=scheduler,
    )
    target = _handle(sessions, "websocket:target")

    await tool.enqueue(
        source_session_key="websocket:source",
        target_handle=target.name,
        content="Question",
        expect_reply=True,
        reply_timeout_seconds=5,
    )
    await bus.consume_inbound()

    async def fail_publish(_message) -> None:
        raise RuntimeError("queue unavailable")

    bus.publish_inbound = fail_publish
    with patch("nanobot.agent.tools.session_messages.logger") as logger:
        scheduler.calls[0][1].fire()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert tool._expiry_tasks == set()
    logger.exception.assert_called_once_with(
        "Session reply timeout delivery failed",
    )


@pytest.mark.asyncio
async def test_reverse_message_cancels_the_pending_reply_timeout(
    tmp_path: Path,
) -> None:
    sessions = SessionManager(tmp_path)
    _persist(sessions, "websocket:source", "websocket:target")
    bus = MessageBus()
    scheduler = _Scheduler()
    tool = SendSessionMessageTool(
        sessions=sessions,
        bus=bus,
        schedule_later=scheduler,
    )
    source = _handle(sessions, "websocket:source")
    target = _handle(sessions, "websocket:target")

    await tool.enqueue(
        source_session_key=source.session_key,
        target_handle=target.name,
        content="Question",
        expect_reply=True,
        reply_timeout_seconds=5,
    )
    await tool.enqueue(
        source_session_key=target.session_key,
        target_handle=source.name,
        content="Answer",
        expect_reply=False,
    )

    assert scheduler.calls[0][1].cancelled


@pytest.mark.asyncio
async def test_reply_follows_a_recycled_handle(tmp_path: Path) -> None:
    sessions = SessionManager(tmp_path)
    _persist(sessions, "websocket:source", "websocket:target")
    bus = MessageBus()
    tool = SendSessionMessageTool(sessions=sessions, bus=bus)
    source = _handle(sessions, "websocket:source")
    target = _handle(sessions, "websocket:target")

    await tool.enqueue(
        source_session_key=source.session_key,
        target_handle=target.name,
        content="Question",
        expect_reply=False,
    )
    received = await bus.consume_inbound()
    assert sessions.delete_session(source.session_key)
    _persist(sessions, "websocket:replacement")
    replacement = _handle(sessions, "websocket:replacement")
    assert replacement.name == source.name

    with request_context(RequestContext(
        channel="system",
        chat_id=target.session_key,
        session_key=target.session_key,
        metadata=received.metadata,
    )):
        result = await tool.execute(
            to=f"@{source.name}",
            content="Answer",
            expect_reply=False,
        )

    assert result.startswith(f"Queued for {source.name} — message ")
    assert "arrival has not been observed" in result
    reply = await bus.consume_inbound()
    assert reply.chat_id == replacement.session_key


# ---------------------------------------------------------------------------
# The receipt (desk filing 2026-11-15, defect 2).
#
# "Sent to @dova" claimed arrival and could not prove it. A message queued at 18:40
# left no run record, nothing in the target's session and nothing in the trigger
# store; it would arrive whenever it arrived, into a context it was not written
# for. The same class had already fired — a fold prompt delivered nine hours late
# at 14:10, which is why a mid-afternoon fold was reported as a reassuring green.
#
# A queued message is a landmine when the sender cannot see it. The fix is a
# receipt, and the receipt is answered from the target's own record: the one
# artifact the sender cannot forge.
# ---------------------------------------------------------------------------


def _rig(tmp_path: Path):
    sessions = SessionManager(tmp_path)
    _persist(sessions, "websocket:source", "telegram:target")
    bus = MessageBus()
    send = SendSessionMessageTool(sessions=sessions, bus=bus)
    status = SessionMessageStatusTool(sessions)
    return sessions, bus, send, status


async def _send(send, target: SessionHandle) -> str:
    with request_context(RequestContext(
        channel="websocket",
        chat_id="source",
        session_key="websocket:source",
    )):
        return await send.execute(
            to=f"@{target.name}",
            content="hello",
            expect_reply=False,
        )


def _message_id(result: str) -> str:
    return result.split("message ")[1].split(".")[0].strip()


@pytest.mark.asyncio
async def test_a_send_reports_queued_and_never_claims_arrival(tmp_path: Path) -> None:
    sessions, bus, send, _status = _rig(tmp_path)
    target = _handle(sessions, "telegram:target")

    result = await _send(send, target)

    assert result.startswith(f"Queued for {target.name} — message ")
    assert "Sent" not in result
    assert "arrival has not been observed" in result
    # and the message really is in the queue
    assert (await bus.consume_inbound()) is not None


@pytest.mark.asyncio
async def test_status_says_pending_with_its_age_before_the_target_has_it(tmp_path: Path) -> None:
    sessions, _bus, send, status = _rig(tmp_path)
    target = _handle(sessions, "telegram:target")
    message_id = _message_id(await _send(send, target))

    answer = await status.execute(message_id=message_id)

    assert answer.startswith("Pending:")
    assert message_id in answer
    assert f"@{target.name}" in answer
    assert "arrival has not been observed" in answer
    assert "Queued " in answer and "s ago" in answer


@pytest.mark.asyncio
async def test_status_says_delivered_from_the_targets_own_record(tmp_path: Path) -> None:
    """Arrival is arrival: the id is in that session's messages, not in a promise."""
    sessions, bus, send, status = _rig(tmp_path)
    target = _handle(sessions, "telegram:target")
    message_id = _message_id(await _send(send, target))

    inbound = await bus.consume_inbound()
    session = sessions.get_or_create("telegram:target")
    session.add_message("user", inbound.content, metadata=inbound.metadata)
    sessions.save(session)

    answer = await status.execute(message_id=message_id)

    assert answer.startswith("Delivered:")
    assert message_id in answer
    assert f"@{target.name}'s record" in answer


@pytest.mark.asyncio
async def test_status_for_an_unknown_id_says_so_rather_than_guessing(tmp_path: Path) -> None:
    _sessions, _bus, _send_tool, status = _rig(tmp_path)
    answer = await status.execute(message_id="no-such-message")
    assert answer.startswith("No receipt for message no-such-message")
    assert "aged out" in answer


def test_the_receipt_is_a_file_not_a_memory(tmp_path: Path) -> None:
    """A receipt that dies with the process is not a receipt: the sender may ask
    from a different turn, hours later."""
    import json as _json

    from nanobot.agent.tools import session_messages as module

    sessions = SessionManager(tmp_path)

    def _receipts_at(_tmp: Path) -> Path:
        return _tmp / "receipts.json"

    receipts = _receipts_at(tmp_path)
    with patch.object(module, "_receipts_path", return_value=receipts):
        _persist(sessions, "websocket:source", "telegram:target")
        tool = SendSessionMessageTool(sessions=sessions, bus=MessageBus())
        target = _handle(sessions, "telegram:target")
        receipt = asyncio.run(tool.enqueue(
            source_session_key="websocket:source",
            target_handle=f"@{target.name}",
            content="hello",
            expect_reply=False,
        ))
        data = _json.loads(receipts.read_text())
        assert receipt.message_id in data
        assert data[receipt.message_id]["target"] == target.name
        assert data[receipt.message_id]["target_session_key"] == "telegram:target"
