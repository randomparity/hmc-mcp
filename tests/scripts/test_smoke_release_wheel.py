"""Tests for the fresh-wheel consumer smoke harness."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parents[2] / "scripts" / "smoke_release_wheel.py"
MODULE_SPEC = importlib.util.spec_from_file_location("smoke_release_wheel", MODULE_PATH)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
smoke_release_wheel = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(smoke_release_wheel)


def test_select_wheel_requires_exactly_one_artifact(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exactly one wheel"):
        smoke_release_wheel.select_wheel(tmp_path)

    first = tmp_path / "hmcpctl-1-py3-none-any.whl"
    first.touch()
    assert smoke_release_wheel.select_wheel(tmp_path) == first

    (tmp_path / "hmcpctl-2-py3-none-any.whl").touch()
    with pytest.raises(ValueError, match="exactly one wheel"):
        smoke_release_wheel.select_wheel(tmp_path)


@pytest.mark.parametrize(
    ("platform", "python_path", "command_path"),
    [
        ("linux", "bin/python", "bin/hmcpctl"),
        ("darwin", "bin/python", "bin/hmcpctl"),
        ("win32", "Scripts/python.exe", "Scripts/hmcpctl.exe"),
    ],
)
def test_environment_paths_are_platform_native(
    tmp_path: Path, platform: str, python_path: str, command_path: str
) -> None:
    assert smoke_release_wheel.environment_python(tmp_path, platform) == (
        tmp_path / python_path
    )
    assert smoke_release_wheel.environment_command(tmp_path, "hmcpctl", platform) == (
        tmp_path / command_path
    )
