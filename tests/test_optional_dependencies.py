import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
#: Packages only the ``app`` extra may pull in: the presentation stack plus
#: the HTTP server stack. Nothing here belongs to the dependency-free core.
APP_ONLY_PACKAGES = {"fastmcp-slim", "mcp", "rich", "typer", "uvicorn"}


def _dependency_names(requirements: list[str]) -> set[str]:
    return {
        requirement.partition("[")[0].partition("=")[0] for requirement in requirements
    }


def test_app_only_dependencies_are_confined_to_the_app_extra() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]

    assert _dependency_names(project["dependencies"]).isdisjoint(APP_ONLY_PACKAGES)
    assert set(project["optional-dependencies"]) == {"app"}
    assert (
        _dependency_names(project["optional-dependencies"]["app"])
        == APP_ONLY_PACKAGES
    )


def test_core_imports_do_not_load_presentation_packages() -> None:
    script = """
from importlib.abc import MetaPathFinder
from importlib import import_module
from pkgutil import iter_modules
import sys


#: Import names behind the ``app`` extra -- kept in one place so the finder and
#: the closing ``sys.modules`` sweep cannot drift apart.
_BLOCKED = {"fastmcp", "mcp", "rich", "typer", "uvicorn"}


class BlockAppOnlyPackages(MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.partition(".")[0] in _BLOCKED:
            raise ModuleNotFoundError(fullname)
        return None


sys.meta_path.insert(0, BlockAppOnlyPackages())

import hmc_mcp
from hmc_mcp import operations
from hmc_mcp.client.core import HMCClient
from hmc_mcp.config import HMCConfig
from hmc_mcp.errors import HMCError

assert HMCClient
assert HMCConfig
assert HMCError
operation_modules = sorted(
    module.name
    for module in iter_modules(operations.__path__)
    if not module.name.startswith("_")
)
assert operation_modules
for module in operation_modules:
    import_module(f"hmc_mcp.operations.{module}")
assert not (_BLOCKED & set(sys.modules))
"""

    subprocess.run([sys.executable, "-c", script], check=True)
