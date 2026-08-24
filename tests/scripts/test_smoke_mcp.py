"""Direct tests for the MCP stdio smoke script."""

import runpy
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).parents[2]


def _main() -> Callable[[list[str] | None], None]:
    namespace = runpy.run_path(str(ROOT / "scripts" / "smoke_mcp.py"))
    return cast(Callable[[list[str] | None], None], namespace["main"])


def test_main_reports_only_the_tool_count_by_default(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _main()([])

    output = capsys.readouterr().out
    lines = output.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("Connected. ")
    assert lines[0].endswith(" tools exposed.")
    assert "  - " not in output


def test_main_lists_the_live_tool_registry_when_verbose(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _main()(["--verbose"])

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("Connected. ")
    assert lines[0].endswith(" tools exposed:")
    assert "  - hmc_list_systems" in lines
    assert "  - hmc_list_lpars" in lines
    assert len(lines) > 100
