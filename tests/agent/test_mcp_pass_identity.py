"""Tests for the opt-in `_nanobot_identity` harness injection (pass_identity).

The Valley identity transport binds one world account per caller session key;
the identity must come from the request ContextVar at execute() time and can
never be model-authored. These tests pin that contract:

- flag off          -> no injection, context or not (every existing server untouched)
- flag on + context -> injected from the ContextVar
- model-authored    -> overwritten (the forge attempt)
- flag on, no ctx   -> argument absent entirely (the server refuses)

Spec: vault/specs/valley-identity-transport.md, "Harness diff" section.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp import types as mcp_types

from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.mcp import MCPToolWrapper
from nanobot.config.schema import MCPServerConfig


def _make_tool_def(name="test_tool"):
    return SimpleNamespace(
        name=name,
        description="A test tool",
        inputSchema={"type": "object", "properties": {}},
    )


def _make_wrapper(pass_identity=False):
    session = AsyncMock()
    session.call_tool = AsyncMock(
        return_value=SimpleNamespace(
            content=[mcp_types.TextContent(type="text", text="ok")],
            isError=False,
        )
    )
    wrapper = MCPToolWrapper(
        session, "test_server", _make_tool_def(), tool_timeout=5,
        pass_identity=pass_identity,
    )
    return wrapper, session


def _make_context(**overrides):
    fields = {
        "channel": "websocket",
        "chat_id": "64978e85-conversation",
        "message_id": "msg-1",
        "session_key": "websocket:64978e85-aaaa",
    }
    fields.update(overrides)
    return RequestContext(**fields)


@pytest.mark.asyncio
async def test_flag_off_never_injects_even_with_context():
    wrapper, session = _make_wrapper(pass_identity=False)
    ctx = _make_context()

    with request_context(ctx):
        await wrapper.execute(foo="bar")

    args = session.call_tool.await_args
    assert "_nanobot_identity" not in args.kwargs["arguments"]


@pytest.mark.asyncio
async def test_flag_on_injects_identity_from_context():
    wrapper, session = _make_wrapper(pass_identity=True)
    ctx = _make_context()

    with request_context(ctx):
        await wrapper.execute(foo="bar")

    args = session.call_tool.await_args
    identity = args.kwargs["arguments"]["_nanobot_identity"]
    assert identity == {
        "session_key": "websocket:64978e85-aaaa",
        "channel": "websocket",
        "chat_id": "64978e85-conversation",
    }
    # The model's own argument survives alongside the injected one.
    assert args.kwargs["arguments"]["foo"] == "bar"


@pytest.mark.asyncio
async def test_model_authored_identity_is_overwritten():
    """The forge attempt: a model-authored _nanobot_identity must not survive."""
    wrapper, session = _make_wrapper(pass_identity=True)
    ctx = _make_context()

    with request_context(ctx):
        await wrapper.execute(
            foo="bar",
            _nanobot_identity={"session_key": "forged", "channel": "cli", "chat_id": "x"},
        )

    args = session.call_tool.await_args
    identity = args.kwargs["arguments"]["_nanobot_identity"]
    assert identity["session_key"] == "websocket:64978e85-aaaa"
    assert identity["session_key"] != "forged"


@pytest.mark.asyncio
async def test_flag_on_without_context_injects_nothing():
    wrapper, session = _make_wrapper(pass_identity=True)

    await wrapper.execute(foo="bar")

    args = session.call_tool.await_args
    assert "_nanobot_identity" not in args.kwargs["arguments"]


@pytest.mark.asyncio
async def test_identity_survives_context_exit_before_server_sees_it():
    """The injection must snapshot values, not hold the context by reference.

    execute() passes its kwargs to call_tool inline, but belt-and-braces:
    the dict built from the ContextVar must be a plain value, since a
    deferred delivery would outlive the request scope.
    """
    wrapper, session = _make_wrapper(pass_identity=True)
    ctx = _make_context()

    with request_context(ctx):
        await wrapper.execute(foo="bar")

    identity = session.call_tool.await_args.kwargs["arguments"]["_nanobot_identity"]
    assert identity["session_key"] == "websocket:64978e85-aaaa"


def test_schema_default_is_off():
    cfg = MCPServerConfig(command="echo")
    assert cfg.pass_identity is False


def test_schema_parses_flag_true():
    cfg = MCPServerConfig.model_validate(
        {"command": "echo", "args": ["hi"], "passIdentity": True}
    )
    assert cfg.pass_identity is True
