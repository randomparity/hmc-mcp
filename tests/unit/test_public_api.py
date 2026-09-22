"""Contract tests for the core reusable Python facade (ADR 0118)."""

from __future__ import annotations

import ast
import inspect
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path

from hmcpctl import api
from hmcpctl.client.core import HMCClient, TLSVerificationDisabledWarning
from hmcpctl.config import ConfigError, HMCConfig
from hmcpctl.errors import HMCError, HMCTransportError

_EXPECTED_EXPORTS = [
    "HMCClient",
    "HMCConfig",
    "ConfigError",
    "HMCError",
    "HMCTransportError",
    "TLSVerificationDisabledWarning",
]
_SUPPORTED_CLIENT_LIFECYCLE = frozenset(
    {"__init__", "__aenter__", "__aexit__", "is_logged_on", "logon", "logoff"}
)


def test_public_api_has_only_the_core_contract() -> None:
    assert api.__all__ == _EXPECTED_EXPORTS
    assert api.HMCClient is HMCClient
    assert api.HMCConfig is HMCConfig
    assert api.ConfigError is ConfigError
    assert api.HMCError is HMCError
    assert api.HMCTransportError is HMCTransportError
    assert api.TLSVerificationDisabledWarning is TLSVerificationDisabledWarning


def test_public_api_does_not_reexport_domain_operations() -> None:
    assert not hasattr(api, "list_vios")
    assert not hasattr(api, "migrate_lpar")
    assert not hasattr(api, "VolumeGroup")


def test_old_package_name_is_absent() -> None:
    assert find_spec("hmc_mcp") is None


def test_hmc_client_supported_lifecycle_members_are_present() -> None:
    assert {
        name for name in _SUPPORTED_CLIENT_LIFECYCLE if hasattr(api.HMCClient, name)
    } == _SUPPORTED_CLIENT_LIFECYCLE


def test_hmc_config_isolated_construction_member_is_supported() -> None:
    assert str(inspect.signature(api.HMCConfig.from_mapping)) == (
        "(values: 'Mapping[str, Any]') -> 'Self'"
    )
    isolated = api.HMCConfig.from_mapping({"host": "hmc.example.test"})
    assert type(isolated) is api.HMCConfig
    assert set(isolated.model_dump()) == set(api.HMCConfig.model_fields)


def test_public_error_hierarchy_is_frozen() -> None:
    assert issubclass(api.HMCTransportError, api.HMCError)
    assert issubclass(api.ConfigError, ValueError)


def test_package_initializers_do_not_define_compatibility_manifests() -> None:
    package_root = Path(__file__).resolve().parents[2] / "src/hmcpctl"
    offenders = []
    for path in package_root.rglob("__init__.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(
            isinstance(node, (ast.Assign, ast.AnnAssign))
            and any(
                isinstance(target, ast.Name) and target.id == "__all__"
                for target in (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
            )
            for node in tree.body
        ):
            offenders.append(path.relative_to(package_root).as_posix())
    assert offenders == []


def test_importing_public_api_does_not_import_presentation_modules() -> None:
    script = """
import sys
import hmcpctl.api

loaded = sorted(
    name for name in sys.modules
    if name == 'hmcpctl._app'
    or name == 'hmcpctl.cli'
    or name.startswith('hmcpctl.cli_commands')
    or name == 'hmcpctl.server'
    or name.startswith('hmcpctl.server_tools')
)
assert loaded == [], loaded
"""
    subprocess.run([sys.executable, "-c", script], check=True)
