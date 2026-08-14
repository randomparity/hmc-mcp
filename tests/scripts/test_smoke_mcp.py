"""Direct tests for the MCP stdio smoke script."""

import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


def test_main_lists_the_live_tool_registry(capsys: pytest.CaptureFixture[str]) -> None:
    runpy.run_path(str(ROOT / "scripts" / "smoke_mcp.py"), run_name="__main__")

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("Connected. ")
    assert lines[0].endswith(" tools exposed:")
    assert "  - hmc_systems" in lines
    assert "  - hmc_lpars" in lines
    assert len(lines) > 100
