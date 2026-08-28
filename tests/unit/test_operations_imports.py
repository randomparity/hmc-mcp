"""Import-order regressions for operation-module dependency boundaries."""

from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "modules",
    [
        ("hmc_mcp.operations.lpar", "hmc_mcp.operations.vnic"),
        ("hmc_mcp.operations.vnic", "hmc_mcp.operations.lpar"),
        ("hmc_mcp.ssh.lpar", "hmc_mcp.ssh.profiles"),
        ("hmc_mcp.ssh.profiles", "hmc_mcp.ssh.lpar"),
    ],
)
def test_sibling_modules_import_in_either_order(
    modules: tuple[str, str],
) -> None:
    imports = "; ".join(f"import {module}" for module in modules)

    completed = subprocess.run(
        [sys.executable, "-c", imports],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
