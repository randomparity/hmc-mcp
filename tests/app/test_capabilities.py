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


def test_closed_vocab_enum_matches_runtime_constant():
    """The MCP enum for closed-vocab params must not drift from the runtime set.

    The tool parameters are annotated with Literal aliases that share their
    values with the runtime constants (PARTITION_TYPES / _VALID_BACKUP_TYPES);
    adding a value must be a single edit. This pins the rendered schema to the
    constant so either side changing alone is caught.
    """
    from hmc_mcp.documents import PARTITION_TYPES
    from hmc_mcp.server_vios import _VALID_BACKUP_TYPES

    by_name = _tools_by_name()

    partition_type = by_name["hmc_create_lpar"].parameters["properties"]["partition_type"]
    assert set(partition_type["enum"]) == set(PARTITION_TYPES)
    assert partition_type["default"] in PARTITION_TYPES

    backup_type = by_name["hmc_backup_vios"].parameters["properties"]["backup_type"]
    assert set(backup_type["enum"]) == set(_VALID_BACKUP_TYPES)
    assert backup_type["default"] in _VALID_BACKUP_TYPES


def test_repository_type_enum_matches_runtime_constant():
    """The MCP enum for RepositorySource.type must not drift from the Literal.

    The repository TypedDict's ``type`` field is annotated with the
    RepositoryType Literal, which renders as an enum in the tool schema; the
    jobs layer validates against _REQUIRED_KEYS keyed by that same set. This
    pins the rendered schema to the runtime set so either side changing alone
    is caught.
    """
    from hmc_mcp.jobs import _REPOSITORY_TYPES

    by_name = _tools_by_name()

    repo_type = by_name["hmc_hmc_update"].parameters["properties"]["repository"][
        "properties"
    ]["type"]
    assert set(repo_type["enum"]) == set(_REPOSITORY_TYPES)


def test_merged_metrics_tools_have_valid_output_schema():
    """hmc_processed_metrics and hmc_aggregated_metrics must expose a non-trivial
    output schema so MCP clients can understand their polymorphic return type."""
    by_name = _tools_by_name()
    for tool_name in ("hmc_processed_metrics", "hmc_aggregated_metrics"):
        tool = by_name[tool_name]
        # The tool must be registered and annotated as read-only
        assert tool.annotations is not None and tool.annotations.readOnlyHint is True, tool_name
        # The input schema must include the 'mode' parameter
        assert "mode" in tool.parameters.get("properties", {}), f"{tool_name} missing 'mode' param"
        mode_schema = tool.parameters["properties"]["mode"]
        assert set(mode_schema.get("enum", [])) == {"links", "fetch"}, (
            f"{tool_name} mode enum incorrect: {mode_schema}"
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
