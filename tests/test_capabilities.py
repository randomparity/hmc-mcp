"""Tests for tool capability annotations and destructive-tool precondition guards.

The MCP server tags every tool with ToolAnnotations (readOnlyHint /
destructiveHint) so clients and gateways can gate on capability instead of
treating every tool equally. The classification lives in server.py's
READ_ONLY_TOOLS / DESTRUCTIVE_TOOLS sets; these tests pin the live registry to
that spec so a new tool must pick a category and be tagged. They also cover the
precondition guards on hmc_delete_lpar / hmc_delete_vios (refuse to delete a
partition that is not powered off), the pattern established by
hmc_remove_memory_pool.
"""

import asyncio

import httpx
import pytest

from hmc_mcp.client import HMCError
from hmc_mcp.server import (
    DESTRUCTIVE_TOOLS,
    READ_ONLY_TOOLS,
    hmc_delete_lpar,
    hmc_delete_vios,
    mcp,
)

LPAR_UUID = "aaaa0000-0000-0000-0000-000000000001"


def _hmc_env(monkeypatch) -> None:
    """Set env vars so HMCConfig() succeeds inside the tool."""
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "abc123")


# ------------------------------------------------------------------ #
# Tool capability classification
# ------------------------------------------------------------------ #

def _tools_by_name():
    return {t.name: t for t in asyncio.run(mcp.list_tools())}


def test_classification_sets_are_disjoint():
    assert not (READ_ONLY_TOOLS & DESTRUCTIVE_TOOLS)


def test_every_registered_tool_matches_its_category():
    """The live registry must carry exactly the documented annotations."""
    by_name = _tools_by_name()
    assert READ_ONLY_TOOLS | DESTRUCTIVE_TOOLS <= set(by_name)
    for name, tool in by_name.items():
        ann = tool.annotations
        if name in READ_ONLY_TOOLS:
            assert ann is not None and ann.readOnlyHint is True, name
            assert ann.destructiveHint is not True, name
        elif name in DESTRUCTIVE_TOOLS:
            assert ann is not None and ann.destructiveHint is True, name
            assert ann.readOnlyHint is not True, name
        else:
            # Untagged tools are state-changing lifecycle/admin operations.
            assert ann is None or (
                ann.readOnlyHint is not True and ann.destructiveHint is not True
            ), name


def test_arbitrary_command_tool_is_neither_readonly_nor_destructive():
    """hmc_run_command executes arbitrary HMC CLI — it must not be gated as
    uniformly read-only or destructive, since either depends on the command."""
    tool = _tools_by_name()["hmc_run_command"]
    ann = tool.annotations
    assert ann is None or (
        ann.readOnlyHint is not True and ann.destructiveHint is not True
    )


# ------------------------------------------------------------------ #
# Delete precondition guards (hmc_delete_lpar / hmc_delete_vios)
# ------------------------------------------------------------------ #

def _mock_state_and_delete(router, state: str, status: int = 200):
    router.get(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/quick/PartitionState"
    ).mock(return_value=httpx.Response(status, text=state))
    return router.delete(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}"
    ).mock(return_value=httpx.Response(204))


def test_delete_lpar_refuses_when_active(monkeypatch, mock_hmc):
    """Deleting a running LPAR is refused before any DELETE is issued."""
    _hmc_env(monkeypatch)
    delete_route = _mock_state_and_delete(mock_hmc, "running")

    with pytest.raises(HMCError) as exc_info:
        hmc_delete_lpar(LPAR_UUID)

    assert exc_info.value.status_code == 409
    assert "not activated" in str(exc_info.value)
    assert not delete_route.called


def test_delete_lpar_succeeds_when_powered_off(monkeypatch, mock_hmc):
    _hmc_env(monkeypatch)
    _mock_state_and_delete(mock_hmc, "not activated")

    result = hmc_delete_lpar(LPAR_UUID)

    assert result == f"Deleted LPAR {LPAR_UUID}"


def test_delete_vios_refuses_when_active(monkeypatch, mock_hmc):
    _hmc_env(monkeypatch)
    delete_route = _mock_state_and_delete(mock_hmc, "shutting down")

    with pytest.raises(HMCError) as exc_info:
        hmc_delete_vios(LPAR_UUID)

    assert exc_info.value.status_code == 409
    assert not delete_route.called


def test_delete_vios_succeeds_when_powered_off(monkeypatch, mock_hmc):
    _hmc_env(monkeypatch)
    _mock_state_and_delete(mock_hmc, "not activated")

    result = hmc_delete_vios(LPAR_UUID)

    assert result == f"Deleted VIOS {LPAR_UUID}"
