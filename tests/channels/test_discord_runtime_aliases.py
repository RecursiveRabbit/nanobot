from __future__ import annotations

import pytest

pytest.importorskip("discord")
from nanobot.channels.discord.runtime import DiscordBotClient


def _client(last: str | None = None) -> DiscordBotClient:
    client = object.__new__(DiscordBotClient)
    client._known_channels = {}
    client._last_inbound_channel_id = last
    return client


def test_numeric_chat_id_is_unchanged() -> None:
    assert _client()._resolve_outbound_chat_id("467239332286300165") == "467239332286300165"


def test_main_uses_last_real_inbound_channel() -> None:
    assert _client("467239332286300165")._resolve_outbound_chat_id("main") == "467239332286300165"


def test_cached_alias_wins_over_last_channel() -> None:
    client = _client("111")
    client._known_channels["main"] = type("Channel", (), {"id": 222})()
    assert client._resolve_outbound_chat_id("main") == "222"


def test_unknown_alias_fails_descriptively() -> None:
    with pytest.raises(RuntimeError, match="no cached route"):
        _client()._resolve_outbound_chat_id("main")
