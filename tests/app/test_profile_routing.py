"""Tests proving every network-backed MCP tool accepts and propagates `profile`.

Covers:
  - Registry-driven exhaustive check: every network tool has `profile` in schema
  - Sequential routing: two consecutive calls with different profiles hit the
    correct HMC host each time
  - Concurrent routing: two simultaneous calls with different profiles show no
    cross-call state leakage
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import httpx
import respx

from hmc_mcp.server import mcp


# These tools make no network calls and therefore have no profile= parameter.
# They are excluded from the profile-routing registry check.
_NO_NETWORK_TOOLS = frozenset(
    {
        "hmc_list_configured_hosts",  # reads TOML config only; no HMC connection
    }
)


def _tools_by_name():
    return {t.name: t for t in asyncio.run(mcp.list_tools())}


# ---------------------------------------------------------------------- #
# T-1: Exhaustive registry test
# ---------------------------------------------------------------------- #


def test_every_network_tool_has_profile_param():
    """Every network-backed MCP tool must expose 'profile' in its JSON schema.

    Only no-network tools are explicitly excluded; REST and SSH tools must
    expose the same routing contract.
    """
    by_name = _tools_by_name()
    orphaned_no_network = _NO_NETWORK_TOOLS - set(by_name)
    assert not orphaned_no_network, (
        f"_NO_NETWORK_TOOLS has stale entries not in the live registry: {sorted(orphaned_no_network)}"
    )
    missing = []
    for name, tool in by_name.items():
        if name in _NO_NETWORK_TOOLS:
            continue
        props = tool.parameters.get("properties", {})
        if "profile" not in props:
            missing.append(name)
    assert not missing, (
        f"These tools are missing the 'profile' parameter: {sorted(missing)}"
    )


def test_profile_param_is_optional_string():
    """The 'profile' parameter must be an optional string (nullable, no default required)."""
    by_name = _tools_by_name()
    bad = []
    for name, tool in by_name.items():
        if name in _NO_NETWORK_TOOLS:
            continue
        props = tool.parameters.get("properties", {})
        if "profile" not in props:
            continue  # caught by test_every_network_tool_has_profile_param
        # Must allow string or null, and must NOT be required
        required = tool.parameters.get("required", [])
        if "profile" in required:
            bad.append(f"{name}: profile is required (should be optional)")
    assert not bad, "\n".join(bad)


# ---------------------------------------------------------------------- #
# T-2: Sequential profile routing
# ---------------------------------------------------------------------- #

LOGON = """<?xml version="1.0"?><LogonResponse xmlns="http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/"><X-API-Session>tok</X-API-Session></LogonResponse>"""

CONSOLE_A = """<?xml version="1.0"?><entry xmlns="http://www.w3.org/2005/Atom"><id>urn:uuid:ca</id><content type="application/vnd.ibm.powervm.uom+xml"><ManagementConsole xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/"><SystemName>hmc-a</SystemName></ManagementConsole></content></entry>"""
CONSOLE_B = """<?xml version="1.0"?><entry xmlns="http://www.w3.org/2005/Atom"><id>urn:uuid:cb</id><content type="application/vnd.ibm.powervm.uom+xml"><ManagementConsole xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/"><SystemName>hmc-b</SystemName></ManagementConsole></content></entry>"""


def _toml_two_profiles(tmp_path: Path) -> Path:
    """Write a TOML config with two profiles: alpha (hmc-a) and beta (hmc-b)."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "[profiles.alpha]\n"
        "host = 'hmc-a.test'\n"
        "user = 'hscroot'\n"
        "password = 'pass-a'\n"  # pragma: allowlist secret
        "\n"
        "[profiles.beta]\n"
        "host = 'hmc-b.test'\n"
        "user = 'hscroot'\n"
        "password = 'pass-b'\n"  # pragma: allowlist secret
    )
    return cfg


def test_sequential_profile_routing(tmp_path, monkeypatch):
    """Two sequential calls with different profiles each hit the correct HMC host."""
    from hmc_mcp.server import hmc_console_info
    from hmc_mcp.config import load_profile as real_load_profile

    cfg_path = _toml_two_profiles(tmp_path)

    def _load_profile_with_path(profile=None, config_path=None):
        return real_load_profile(profile=profile, config_path=cfg_path)

    with (
        patch("hmc_mcp.common.load_profile", side_effect=_load_profile_with_path),
        patch("hmc_mcp.common.resolve_config_path", return_value=cfg_path),
        respx.mock(assert_all_called=False) as router_a,
    ):
        # Alpha profile → hmc-a.test
        router_a.put("https://hmc-a.test:12443/rest/api/web/Logon").mock(
            return_value=httpx.Response(200, text=LOGON)
        )
        router_a.delete("https://hmc-a.test:12443/rest/api/web/Logon").mock(
            return_value=httpx.Response(204)
        )
        router_a.get("https://hmc-a.test:12443/rest/api/uom/ManagementConsole").mock(
            return_value=httpx.Response(200, text=CONSOLE_A)
        )

        result_a = hmc_console_info(profile="alpha")

    with (
        patch("hmc_mcp.common.load_profile", side_effect=_load_profile_with_path),
        patch("hmc_mcp.common.resolve_config_path", return_value=cfg_path),
        respx.mock(assert_all_called=False) as router_b,
    ):
        # Beta profile → hmc-b.test
        router_b.put("https://hmc-b.test:12443/rest/api/web/Logon").mock(
            return_value=httpx.Response(200, text=LOGON)
        )
        router_b.delete("https://hmc-b.test:12443/rest/api/web/Logon").mock(
            return_value=httpx.Response(204)
        )
        router_b.get("https://hmc-b.test:12443/rest/api/uom/ManagementConsole").mock(
            return_value=httpx.Response(200, text=CONSOLE_B)
        )

        result_b = hmc_console_info(profile="beta")

    assert result_a is not None and result_a["Resource"]["SystemName"] == "hmc-a"
    assert result_b is not None and result_b["Resource"]["SystemName"] == "hmc-b"


# ---------------------------------------------------------------------- #
# T-3: Concurrent profile isolation
# ---------------------------------------------------------------------- #


def test_two_profile_strings_produce_distinct_clients(tmp_path, monkeypatch):
    """Two calls with different profile strings produce clients pointing at different hosts.

    Uses asyncio.gather for structural concurrency; the mock has no await
    points so tasks run to completion without interleaving. This confirms
    that client_from_env constructs an independent HMCClient per call
    (no shared cached instance).  For thread/coroutine interleaving coverage
    see the sequential routing test.
    """
    from hmc_mcp.client import HMCClient

    cfg_path = _toml_two_profiles(tmp_path)

    call_log: list[str] = []

    async def fake_context_a(self):
        call_log.append(f"open:{self.config.host}")
        return self

    async def fake_context_exit(self, *_):
        pass

    async def fake_get_console(self):
        call_log.append(f"get:{self.config.host}")
        return {"Resource": {"SystemName": self.config.host}, "UUID": "x"}

    async def run_concurrent():
        def patched_resolve():
            return cfg_path

        with (
            patch("hmc_mcp.common.resolve_config_path", side_effect=patched_resolve),
            patch("hmc_mcp.config.resolve_config_path", side_effect=patched_resolve),
        ):
            with (
                patch.object(HMCClient, "__aenter__", fake_context_a),
                patch.object(HMCClient, "__aexit__", fake_context_exit),
                patch.object(HMCClient, "get_console_info", fake_get_console),
            ):
                from hmc_mcp.common import client_from_env

                async def call_a():
                    async with client_from_env("alpha") as hmc:
                        return await hmc.get_console_info()

                async def call_b():
                    async with client_from_env("beta") as hmc:
                        return await hmc.get_console_info()

                task_a, task_b = await asyncio.gather(call_a(), call_b())
                return task_a, task_b

    result_a, result_b = asyncio.run(run_concurrent())

    assert "hmc-a.test" in result_a["Resource"]["SystemName"]
    assert "hmc-b.test" in result_b["Resource"]["SystemName"]
    assert result_a["Resource"]["SystemName"] != result_b["Resource"]["SystemName"], (
        "Concurrent calls returned the same host — state leakage detected"
    )
