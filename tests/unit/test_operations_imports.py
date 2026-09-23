"""Import-order regressions for operation-module dependency boundaries."""

from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "modules",
    [
        ("hmcpctl.operations.lpar", "hmcpctl.operations.virtualization.vnic"),
        ("hmcpctl.operations.virtualization.vnic", "hmcpctl.operations.lpar"),
        ("hmcpctl.ssh.lpar", "hmcpctl.ssh.profiles"),
        ("hmcpctl.ssh.profiles", "hmcpctl.ssh.lpar"),
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
