import json
from pathlib import Path
from unittest.mock import patch

from scripts import activation_snapshot


class FakeManager:
    def __init__(self, _workspace):
        self.rows = [
            {"key": "websocket:hise", "metadata": {"session_handle": "hise"}},
            {"key": "websocket:tiyu", "metadata": {"session_handle": "tiyu"}},
            {"key": "websocket:joli", "metadata": {"session_handle": "joli"}},
            {"key": "websocket:royi", "metadata": {"session_handle": "royi"}},
            {"key": "websocket:munu", "metadata": {"session_handle": "munu"}},
        ]

    def list_sessions(self):
        return self.rows

    def read_session_metadata(self, key):
        for row in self.rows:
            if row["key"] == key:
                return {"metadata": row["metadata"]}
        raise KeyError(key)


def test_boundary_pass_exclusions_and_inert_states(tmp_path):
    out = tmp_path / "image.json"
    with patch("nanobot.session.manager.SessionManager", FakeManager):
        import sys
        sys.argv = ["activation_snapshot.py", "--out", str(out)]
        activation_snapshot.main()

    agents = json.loads(out.read_text())["agents"]
    by_handle = {agent["handle"]: agent for agent in agents}
    assert "hise" not in by_handle
    assert "tiyu" not in by_handle
    assert by_handle["joli"]["state"] == "inert"
    assert by_handle["joli"]["surfaces"] == []
    assert by_handle["munu"]["state"] == "inert"
    assert by_handle["munu"]["surfaces"] == []
    assert by_handle["royi"]["state"] == "active"
    assert set(by_handle["royi"]["surfaces"]) == set(activation_snapshot.SURFACES)
    purpose = by_handle["munu"]["purpose"]
    assert by_handle["munu"]["approved_by"] == "Evans"
    assert "via Tuto" not in purpose
    assert (
        "deliberate adoption approved by Evans (named human; assignment: "
        "requests/2026-11-08-from-nime-to-tuto-munu-onboarding.md)" in purpose
    )
    assert "agent guide/delegate: Tuto" in purpose
    assert "sponsor: nime" in purpose
