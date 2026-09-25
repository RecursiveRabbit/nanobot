"""Operator strings layer: override resolution, template rendering, tool schemas."""

from __future__ import annotations

import pytest

from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.base import Tool, ToolResult
from nanobot.utils import strings
from nanobot.utils.prompt_templates import render_template


@pytest.fixture(autouse=True)
def _clean_overrides():
    strings.set_overrides(None)
    yield
    strings.set_overrides(None)


class _DummyTool(Tool):
    name = "dummy"
    description = "Original dummy description."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Original path param."}
        },
    }

    def execute(self, **kwargs):
        return ToolResult(output="ok")


def test_override_replaces_template_render() -> None:
    default = render_template("agent/platform_policy.md", system="Linux")
    assert "POSIX" in default

    strings.set_overrides({"template:agent/platform_policy.md": "CUSTOM POLICY {{ system }}"})

    assert render_template("agent/platform_policy.md", system="Linux") == "CUSTOM POLICY Linux"
    # strip applies to overrides too
    strings.set_overrides({"template:agent/platform_policy.md": "PADDED\n"})
    assert render_template("agent/platform_policy.md", system="Linux", strip=True) == "PADDED"


def test_malformed_override_falls_back_to_bundled_default() -> None:
    default = render_template("agent/platform_policy.md", system="Linux")
    strings.set_overrides({"template:agent/platform_policy.md": "{{ unterminated"})
    assert render_template("agent/platform_policy.md", system="Linux") == default


def test_literal_resolution() -> None:
    assert strings.text("literal:missing", "dflt") == "dflt"
    strings.set_overrides({"literal:missing": "mine"})
    assert strings.text("literal:missing", "dflt") == "mine"


def test_tool_definition_overrides_apply_without_touching_cache() -> None:
    registry = ToolRegistry()
    registry.register(_DummyTool())

    pristine = registry.get_definitions(apply_overrides=False)
    assert pristine[0]["function"]["description"] == "Original dummy description."

    strings.set_overrides({
        "tool:dummy:description": "Operator description.",
        "tool:dummy:param:path": "Operator param.",
    })
    applied = registry.get_definitions()
    assert applied[0]["function"]["description"] == "Operator description."
    assert applied[0]["function"]["parameters"]["properties"]["path"]["description"] == "Operator param."

    # Cache stays pristine; clearing overrides restores default behavior.
    strings.set_overrides(None)
    assert registry.get_definitions()[0]["function"]["description"] == "Original dummy description."


def test_catalog_groups_and_marks_overrides() -> None:
    registry = ToolRegistry()
    registry.register(_DummyTool())
    strings.set_tool_definitions_provider(lambda: registry.get_definitions(apply_overrides=False))
    try:
        strings.set_overrides({"literal:runtime_context_tag": "[ctx]"})
        entries = strings.catalog(
            template_defaults={"agent/identity.md": "id", "agent/subagent_system.md": "sub"}
        )
        by_key = {e["key"]: e for e in entries}
        assert by_key["template:agent/subagent_system.md"]["group"] == "Goals & subagents"
        assert by_key["template:agent/subagent_system.md"]["advanced"] is True
        assert by_key["tool:dummy:description"]["advanced"] is True
        assert by_key["tool:dummy:param:path"]["default"] == "Original path param."
        lit = by_key["literal:runtime_context_tag"]
        assert lit["overridden"] is True
        assert lit["effective"] == "[ctx]"
    finally:
        strings.set_tool_definitions_provider(None)


def test_config_load_applies_strings_overrides(tmp_path, monkeypatch) -> None:
    import json

    from nanobot.config.loader import load_config

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "agents": {"defaults": {"strings": {"literal:runtime_context_end": "[/ctx]"}}}
    }))
    monkeypatch.setenv("NANOBOT_CONFIG", str(config_path))
    load_config(config_path)
    assert strings.get_override("literal:runtime_context_end") == "[/ctx]"
