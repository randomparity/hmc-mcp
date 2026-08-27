"""Import-order regressions for operation-module dependency boundaries."""

from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "modules",
    [
        ("hmc_mcp.operations_lpar", "hmc_mcp.operations_ssh_network"),
        ("hmc_mcp.operations_ssh_network", "hmc_mcp.operations_lpar"),
    ],
)
def test_lpar_and_ssh_network_operations_import_in_either_order(
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
