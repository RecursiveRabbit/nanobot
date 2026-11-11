"""The runtime-context marker names its owner (S10-1, kevu register 2026-11-10).

The marker already carried the exact suffix so a reader could remove the
injected block byte-exactly. It did not say *whose* backlog the block was, so a
raw reader (ingress, QA) saw the recipient's todo list inside the sender's
prose. The owner rides the marker; nothing about the detach changes.
"""

from nanobot.runtime_context import (
    RUNTIME_CONTEXT_HISTORY_META,
    RuntimeContextBlock,
    append_runtime_context,
    public_history_message,
)

OWNER = {"session": "websocket:tuto", "handle": "tuto"}


def _block(text="Current todo items: 1. run the batch"):
    return RuntimeContextBlock(content=text, source="todo_list")


def test_owner_rides_the_suffix_marker():
    content, marker = append_runtime_context("hello", [_block()], owner=OWNER)
    assert content == "hello\n\nCurrent todo items: 1. run the batch"
    assert marker is not None and marker["owner"] == OWNER


def test_owner_rides_the_block_list_marker():
    content, marker = append_runtime_context([{"type": "text", "text": "hello"}], [_block()], owner=OWNER)
    assert isinstance(content, list)
    assert marker is not None and marker["owner"] == OWNER


def test_no_owner_keeps_the_old_shape():
    _, marker = append_runtime_context("hello", [_block()])
    assert marker is not None and "owner" not in marker


def test_detach_still_removes_the_block_byte_exactly_with_an_owner():
    content, marker = append_runtime_context("the sender's words", [_block()], owner=OWNER)
    message = {"role": "user", "content": content, RUNTIME_CONTEXT_HISTORY_META: marker}
    cleaned = public_history_message(message)
    assert cleaned["content"] == "the sender's words"
    assert RUNTIME_CONTEXT_HISTORY_META not in cleaned
