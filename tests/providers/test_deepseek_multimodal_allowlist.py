"""The DeepSeek multimodal allowlist — a capability decided by a name.

A model absent from `_DEEPSEEK_MULTIMODAL_MODELS` has its content forced to a
string, so an image reaches the model as a filename and a size and nothing
reports the drop. 2026-11-14: `deepseek-flash` became V4.1-Flash with native
multimodal support while the set still listed only the retired
`deepseek-v4-flash-vision-exp`, and a resident with a camera spent a night
measuring green pixels with arithmetic because she could not be handed one.

These rows pin the claim in both directions: the live names are in, and an
unknown name is out, so adding a model is a deliberate act with a test beside
it rather than a hope that the next name also supports images.
"""

from __future__ import annotations

from nanobot.providers.openai_compat_provider import _DEEPSEEK_MULTIMODAL_MODELS


def test_the_live_flash_names_accept_images() -> None:
    # deepseek-flash is V4.1-Flash (multimodal); the two older names route to it.
    for name in ("deepseek-flash", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp"):
        assert name in _DEEPSEEK_MULTIMODAL_MODELS, (
            f"{name} is served by a multimodal model but is not in the allowlist, "
            "so its images are silently flattened to text"
        )


def test_an_unknown_name_is_not_assumed_multimodal() -> None:
    assert "deepseek-flash-text-only-does-not-exist" not in _DEEPSEEK_MULTIMODAL_MODELS
