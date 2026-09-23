"""Load and render agent system prompt templates (Jinja2) under nanobot/templates/.

Agent prompts live in ``templates/agent/`` (pass names like ``agent/identity.md``).
Shared copy lives under ``agent/_snippets/`` and is included via
``{% include 'agent/_snippets/....md' %}``.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader
from loguru import logger

from nanobot.utils import strings

_TEMPLATES_ROOT = Path(__file__).resolve().parent.parent / "templates"


@lru_cache
def _environment() -> Environment:
    # Plain-text prompts: do not HTML-escape variable values.
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_ROOT)),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_template(name: str, *, strip: bool = False, **kwargs: Any) -> str:
    """Render ``name`` (e.g. ``agent/identity.md``, ``agent/platform_policy.md``) under ``templates/``.

    Use ``strip=True`` for single-line user-facing strings when the file ends
    with a trailing newline you do not want preserved.
    """
    override = strings.get_override(f"template:{name}")
    if override is not None:
        try:
            text = _environment().from_string(override).render(**kwargs)
            return text.rstrip() if strip else text
        except Exception:
            logger.warning(
                "Strings override for template {} failed to render; using bundled default",
                name,
            )
    text = _environment().get_template(name).render(**kwargs)
    return text.rstrip() if strip else text


def bundled_template_defaults() -> dict[str, str]:
    """Raw bundled text of every agent template, for the operator catalog."""
    return {
        str(path.relative_to(_TEMPLATES_ROOT)): path.read_text(encoding="utf-8")
        for path in sorted(_TEMPLATES_ROOT.rglob("*.md"))
        if path.is_file()
    }
