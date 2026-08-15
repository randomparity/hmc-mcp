from pathlib import Path
import subprocess
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]
PRESENTATION_PACKAGES = {"fastmcp-slim", "mcp", "rich", "typer"}


def _dependency_names(requirements: list[str]) -> set[str]:
    return {
        requirement.partition("[")[0].partition("=")[0] for requirement in requirements
    }


def test_presentation_dependencies_are_confined_to_app_extra() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]

    assert _dependency_names(project["dependencies"]).isdisjoint(PRESENTATION_PACKAGES)
    assert set(project["optional-dependencies"]) == {"app"}
    assert (
        _dependency_names(project["optional-dependencies"]["app"])
        == PRESENTATION_PACKAGES
    )


def test_core_imports_do_not_load_presentation_packages() -> None:
    script = """
from importlib.abc import MetaPathFinder
from importlib import import_module
from pkgutil import iter_modules
import sys


class BlockPresentationPackages(MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.partition(".")[0] in {"fastmcp", "mcp", "rich", "typer"}:
            raise ModuleNotFoundError(fullname)
        return None


sys.meta_path.insert(0, BlockPresentationPackages())

import hmc_mcp
from hmc_mcp.client import HMCClient
from hmc_mcp.config import HMCConfig
from hmc_mcp.errors import HMCError

assert HMCClient
assert HMCConfig
assert HMCError
operation_modules = sorted(
    module.name
    for module in iter_modules(hmc_mcp.__path__)
    if module.name.startswith("operations_")
)
assert operation_modules
for module in operation_modules:
    import_module(f"hmc_mcp.{module}")
assert not ({"fastmcp", "mcp", "rich", "typer"} & set(sys.modules))
"""

    subprocess.run([sys.executable, "-c", script], check=True)
