"""write_section: the retry-safe unit for a file too big to emit in one response.

The hazard this exists for (desk batch 15; 17 cap-deaths measured across five
residents on 2026-11-23): a 40-50 KB baton written in a single `write_file` call
is 13-16k output tokens against an 8192-token cap, so the call is cut mid-JSON and
never runs. The residents' answer is to write the file in sections — but
`write_file` replaces the whole file and `apply_patch`'s add appends, so
re-issuing a section that was cut either duplicates it or clobbers the rest.

Setting one top-level key is idempotent by construction. These rows pin that,
and pin the two failure answers that make it safe: nothing is written unless both
the file and the new value parse.
"""
import json
from pathlib import Path

import pytest

from nanobot.agent.tools.filesystem import WriteSectionTool


def tool(tmp_path: Path) -> WriteSectionTool:
    return WriteSectionTool(workspace=tmp_path, allowed_dir=tmp_path, restrict_to_workspace=True)


@pytest.mark.asyncio
async def test_a_new_file_is_created_with_one_section(tmp_path: Path) -> None:
    target = tmp_path / "baton.json"
    out = await tool(tmp_path).execute(
        path=str(target), section="written", content='"2026-11-23"')

    assert "Successfully wrote section 'written'" in out
    assert json.loads(target.read_text(encoding="utf-8")) == {"written": "2026-11-23"}


@pytest.mark.asyncio
async def test_other_sections_are_untouched(tmp_path: Path) -> None:
    target = tmp_path / "baton.json"
    target.write_text(json.dumps({"who": {"name": "pore"}, "keep": [1, 2, 3]}), encoding="utf-8")

    await tool(tmp_path).execute(path=str(target), section="day", content='{"n": 1}')

    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["who"] == {"name": "pore"}
    assert data["keep"] == [1, 2, 3]
    assert data["day"] == {"n": 1}


@pytest.mark.asyncio
async def test_writing_the_same_section_twice_is_the_same_file(tmp_path: Path) -> None:
    """The whole point: a retried call cannot double the section."""
    t = tool(tmp_path)
    target = tmp_path / "baton.json"

    await t.execute(path=str(target), section="a", content='"one"')
    await t.execute(path=str(target), section="b", content='"two"')
    first = target.read_bytes()

    await t.execute(path=str(target), section="b", content='"two"')

    assert target.read_bytes() == first
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": "one", "b": "two"}


@pytest.mark.asyncio
async def test_rewriting_cut_section_three_after_four_does_not_duplicate(tmp_path: Path) -> None:
    """Section 3's call is cut, 4 lands, then 3 is retried: 3 replaces in place."""
    t = tool(tmp_path)
    target = tmp_path / "baton.json"

    await t.execute(path=str(target), section="s1", content='"1"')
    await t.execute(path=str(target), section="s2", content='"2"')
    await t.execute(path=str(target), section="s4", content='"4"')   # out of order is fine
    await t.execute(path=str(target), section="s3", content='"3"')   # the retry

    data = json.loads(target.read_text(encoding="utf-8"))
    assert list(data) == ["s1", "s2", "s4", "s3"]
    assert sorted(data) == ["s1", "s2", "s3", "s4"]
    assert len(data) == 4


@pytest.mark.asyncio
async def test_invalid_json_value_writes_nothing(tmp_path: Path) -> None:
    target = tmp_path / "baton.json"
    target.write_text('{"a": "one"}', encoding="utf-8")
    before = target.read_bytes()

    out = await tool(tmp_path).execute(
        path=str(target), section="b", content='{"cut": ')   # truncated arguments

    assert "Error" in out
    assert "not valid JSON" in out
    assert "Nothing was written" in out
    assert target.read_bytes() == before


@pytest.mark.asyncio
async def test_a_file_that_is_not_json_is_refused_not_clobbered(tmp_path: Path) -> None:
    target = tmp_path / "notes.md"
    target.write_text("# not json\n", encoding="utf-8")
    before = target.read_bytes()

    out = await tool(tmp_path).execute(path=str(target), section="a", content='"x"')

    assert "Error" in out
    assert "refusing" in out
    assert target.read_bytes() == before


@pytest.mark.asyncio
async def test_a_json_array_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "list.json"
    target.write_text("[1, 2]", encoding="utf-8")

    out = await tool(tmp_path).execute(path=str(target), section="a", content='"x"')

    assert "Error" in out
    assert "holds a JSON list" in out
    assert "sections need top-level keys" in out
    assert json.loads(target.read_text(encoding="utf-8")) == [1, 2]


@pytest.mark.asyncio
async def test_existing_indent_is_preserved(tmp_path: Path) -> None:
    target = tmp_path / "baton.json"
    target.write_text('{\n    "a": "one"\n}\n', encoding="utf-8")

    await tool(tmp_path).execute(path=str(target), section="b", content='"two"')

    text = target.read_text(encoding="utf-8")
    assert '\n    "b"' in text          # four spaces kept
    assert json.loads(text) == {"a": "one", "b": "two"}


@pytest.mark.asyncio
async def test_the_result_names_the_section_and_the_size(tmp_path: Path) -> None:
    """The record fact the desk asked for: no byte-count re-derivation later."""
    target = tmp_path / "baton.json"
    out = await tool(tmp_path).execute(path=str(target), section="pages", content='"x"')

    assert "section 'pages'" in out
    assert "file now 1 sections" in out
    assert f"{target.stat().st_size} bytes" in out


@pytest.mark.asyncio
async def test_outside_the_workspace_is_refused(tmp_path: Path) -> None:
    outside = tmp_path.parent / "elsewhere-section.json"
    out = await tool(tmp_path).execute(path=str(outside), section="a", content='"x"')

    assert "Error" in out
    assert not outside.exists()
