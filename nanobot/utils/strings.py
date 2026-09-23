"""Operator-owned strings layer.

Every string the harness can inject into model context resolves through here.
Defaults ship with the code; overrides live in ``agents.defaults.strings`` in
the operator's config file and are edited through the webui settings page.
The model is never told whether a string is default or overridden — the lens
belongs to the operator, invisibly.

Keys:
  template:<name>        bundled Jinja templates under templates/
  literal:<identifier>   hardcoded literals (markers, preambles, wrappers)
  tool:<name>:description        tool definition text
  tool:<name>:param:<param>      tool parameter description text
"""

from __future__ import annotations

import threading
from typing import Any, Callable

_lock = threading.Lock()
_overrides: dict[str, str] = {}
_tool_definitions_provider: Callable[[], list[dict[str, Any]]] | None = None


def set_tool_definitions_provider(provider: Callable[[], list[dict[str, Any]]] | None) -> None:
    """Point the catalog at the live tool registry (pristine definitions)."""
    global _tool_definitions_provider
    with _lock:
        _tool_definitions_provider = provider


def tool_definitions() -> list[dict[str, Any]]:
    with _lock:
        provider = _tool_definitions_provider
    return provider() if provider is not None else []

# Catalog metadata for literals and other non-template strings.
# key -> (default, group, advanced)
_LITERALS: dict[str, tuple[str, str, bool]] = {}


def register_literal(key: str, default: str, *, group: str, advanced: bool = False) -> str:
    """Register an injectable literal with its default; returns the default."""
    _LITERALS[key] = (default, group, advanced)
    return default


def set_overrides(mapping: dict[str, str] | None) -> None:
    """Replace the active overrides (operator config, settings save)."""
    with _lock:
        _overrides.clear()
        if mapping:
            _overrides.update({str(k): str(v) for k, v in mapping.items()})


def get_override(key: str) -> str | None:
    with _lock:
        return _overrides.get(key)


def overrides_snapshot() -> dict[str, str]:
    with _lock:
        return dict(_overrides)


def text(key: str, default: str) -> str:
    """Resolve a string: operator override if set, else the default."""
    return get_override(key) or default


def globals_tool_definitions() -> list[dict[str, Any]]:
    """Tool definitions from the registered live provider, if any."""
    return tool_definitions()


def catalog_entry(
    key: str,
    default: str,
    *,
    group: str,
    advanced: bool,
) -> dict[str, Any]:
    override = get_override(key)
    return {
        "key": key,
        "group": group,
        "advanced": advanced,
        "default": default,
        "override": override,
        "effective": override if override is not None else default,
        "overridden": override is not None,
    }


def catalog(
    *,
    template_defaults: dict[str, str],
    tool_definitions: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Full injectable-strings catalog for the operator settings page."""
    if tool_definitions is None:
        tool_definitions = globals_tool_definitions()
    entries: list[dict[str, Any]] = []
    for name, default in sorted(template_defaults.items()):
        entries.append(catalog_entry(
            f"template:{name}",
            default,
            group=_TEMPLATE_GROUPS.get(name, "System prompt"),
            advanced=_TEMPLATE_ADVANCED.get(name, False),
        ))
    for key, (default, group, advanced) in sorted(_LITERALS.items()):
        entries.append(catalog_entry(key, default, group=group, advanced=advanced))
    for definition in tool_definitions:
        fn = definition.get("function", definition)
        name = fn.get("name")
        if not name:
            continue
        entries.append(catalog_entry(
            f"tool:{name}:description",
            fn.get("description") or "",
            group="Tools",
            advanced=True,
        ))
        params = (fn.get("parameters") or {}).get("properties") or {}
        for param, spec in sorted(params.items()):
            if isinstance(spec, dict) and spec.get("description"):
                entries.append(catalog_entry(
                    f"tool:{name}:param:{param}",
                    spec["description"],
                    group="Tools",
                    advanced=True,
                ))
    return entries


_TEMPLATE_GROUPS = {
    "agent/identity.md": "System prompt",
    "agent/platform_policy.md": "System prompt",
    "agent/tool_contract.md": "System prompt",
    "agent/_snippets/untrusted_content.md": "System prompt",
    "agent/skills_section.md": "System prompt",
    "agent/consolidator_archive.md": "Compaction",
    "agent/cron_reminder.md": "Scheduled work",
    "agent/max_iterations_message.md": "Turns",
    "agent/goal_runtime.md": "Goals & subagents",
    "agent/evaluator.md": "Goals & subagents",
    "agent/subagent_announce.md": "Goals & subagents",
    "agent/subagent_system.md": "Goals & subagents",
    "agent/dream.md": "Memory pipeline",
}

_TEMPLATE_ADVANCED = {
    "agent/goal_runtime.md": True,
    "agent/evaluator.md": True,
    "agent/subagent_announce.md": True,
    "agent/subagent_system.md": True,
    "agent/dream.md": True,
}
