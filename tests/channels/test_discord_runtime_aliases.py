from __future__ import annotations

import pytest

pytest.importorskip("discord")
from nanobot.bus.queue import MessageBus
from nanobot.channels.discord.runtime import DiscordBotClient, DiscordChannel, DiscordConfig


def _client(last: str | None = None) -> DiscordBotClient:
    """Build a client the way production wires it: the resolver lives on a
    real channel (the client only delegates). The 2026-11-09 incident
    shipped because the old fixture set ``client._known_channels`` directly
    — an attribute the real constructor never creates — so the suite stayed
    green while production raised AttributeError on every non-numeric
    chat_id."""
    channel = DiscordChannel(DiscordConfig(enabled=True, allow_from=["*"]), MessageBus())
    if last is not None:
        channel._last_inbound_channel_id = last
    client = object.__new__(DiscordBotClient)
    client._channel = channel
    return client


def test_numeric_chat_id_is_unchanged() -> None:
    assert _client()._resolve_outbound_chat_id("467239332286300165") == "467239332286300165"


def test_main_uses_last_real_inbound_channel() -> None:
    assert _client("467239332286300165")._resolve_outbound_chat_id("main") == "467239332286300165"


def test_cached_alias_wins_over_last_channel() -> None:
    client = _client("111")
    client._channel._known_channels["main"] = type("Channel", (), {"id": 222})()
    assert client._resolve_outbound_chat_id("main") == "222"


def test_unknown_alias_fails_descriptively() -> None:
    with pytest.raises(RuntimeError, match="no cached route"):
        _client()._resolve_outbound_chat_id("main")
