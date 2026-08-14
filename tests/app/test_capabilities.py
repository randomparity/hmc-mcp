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
from unittest.mock import AsyncMock, patch

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


def test_arbitrary_command_tool_is_disabled_by_default():
    assert "hmc_run_command" not in _tools_by_name()


def test_attach_disk_is_state_changing_not_destructive():
    assert "hmc_attach_disk_to_lpar" not in READ_ONLY_TOOLS
    assert "hmc_attach_disk_to_lpar" not in DESTRUCTIVE_TOOLS
    annotations = _tools_by_name()["hmc_attach_disk_to_lpar"].annotations
    assert annotations is None or (
        annotations.readOnlyHint is not True and annotations.destructiveHint is not True
    )


def test_arbitrary_command_tool_configuration_is_symmetric_and_idempotent():
    from hmc_mcp import server_command

    try:
        asyncio.run(server_command.configure_arbitrary_command_tool(False, mcp))
        asyncio.run(server_command.configure_arbitrary_command_tool(False, mcp))
        asyncio.run(server_command.configure_arbitrary_command_tool(True, mcp))
        asyncio.run(server_command.configure_arbitrary_command_tool(True, mcp))

        tools = [
            tool
            for tool in asyncio.run(mcp.list_tools())
            if tool.name == "hmc_run_command"
        ]
        assert len(tools) == 1
        assert tools[0].annotations is not None
        assert tools[0].annotations.readOnlyHint is False
        asyncio.run(server_command.configure_arbitrary_command_tool(False, mcp))
        asyncio.run(server_command.configure_arbitrary_command_tool(False, mcp))
        assert "hmc_run_command" not in _tools_by_name()
    finally:
        asyncio.run(server_command.configure_arbitrary_command_tool(False, mcp))


def test_closed_vocab_enum_matches_runtime_constant():
    """The MCP enum for closed-vocab params must not drift from the runtime set.

    The tool parameters are annotated with Literal aliases that share their
    values with the runtime constants (PARTITION_TYPES / _VALID_BACKUP_TYPES);
    adding a value must be a single edit. This pins the rendered schema to the
    constant so either side changing alone is caught.
    """
    from hmc_mcp.client_adapters import ADAPTER_TYPES
    from hmc_mcp.client_users import (
        LDAP_REMOVAL_RESOURCES,
        _VALID_USER_TYPES,
    )
    from hmc_mcp.documents import PARTITION_TYPES, STORAGE_KINDS, TASK_ROLES
    from hmc_mcp.jobs import DEVICE_TYPES, LU_TYPES
    from hmc_mcp.server_vios import _VALID_BACKUP_TYPES
    from hmc_mcp.ssh_commands import _VALID_PCI_CLASSES, _VALID_SRIOV_MODES

    by_name = _tools_by_name()

    partition_type = by_name["hmc_create_lpar"].parameters["properties"][
        "partition_type"
    ]
    assert set(partition_type["enum"]) == set(PARTITION_TYPES)
    assert partition_type["default"] in PARTITION_TYPES

    backup_type = by_name["hmc_backup_vios"].parameters["properties"]["backup_type"]
    assert set(backup_type["enum"]) == set(_VALID_BACKUP_TYPES)
    assert backup_type["default"] in _VALID_BACKUP_TYPES

    expected_enums = {
        ("hmc_create_user", "taskrole"): TASK_ROLES,
        ("hmc_list_adapters", "adapter_type"): ADAPTER_TYPES,
        ("hmc_delete_adapter", "adapter_type"): ADAPTER_TYPES,
        ("hmc_map_storage_to_lpar", "storage_kind"): STORAGE_KINDS,
        ("hmc_create_logical_unit", "lu_type"): LU_TYPES,
        ("hmc_create_logical_unit", "device_type"): DEVICE_TYPES,
        ("hmc_remove_ldap_config", "resource"): LDAP_REMOVAL_RESOURCES,
        ("hmc_list_users", "user_type"): _VALID_USER_TYPES,
        ("hmc_set_sriov_adapter_mode", "mode"): _VALID_SRIOV_MODES,
        ("hmc_list_io_slots", "pci_class"): _VALID_PCI_CLASSES,
    }
    for (tool_name, parameter_name), values in expected_enums.items():
        parameter = by_name[tool_name].parameters["properties"][parameter_name]
        assert set(parameter["enum"]) == set(values)


def test_destructive_partition_tools_expose_system_scope():
    by_name = _tools_by_name()

    for tool_name in (
        "hmc_power_off_lpar",
        "hmc_delete_vios",
        "hmc_restore_vios",
        "hmc_power_off_vios",
    ):
        properties = by_name[tool_name].parameters["properties"]
        assert "system_name_or_uuid" in properties


def test_partition_creation_tools_share_resource_object_schema():
    by_name = _tools_by_name()
    lpar_properties = by_name["hmc_create_lpar"].parameters["properties"]
    vios_properties = by_name["hmc_create_vios"].parameters["properties"]

    assert "resources" in lpar_properties
    assert "resources" in vios_properties
    for legacy_name in (
        "min_memory",
        "desired_memory",
        "max_memory",
        "min_vcpus",
        "desired_vcpus",
        "max_vcpus",
        "min_procs",
        "desired_procs",
        "max_procs",
    ):
        assert legacy_name not in vios_properties


def test_password_policy_mutations_use_settings_object_schema():
    by_name = _tools_by_name()
    for tool_name in ("hmc_create_password_policy", "hmc_modify_password_policy"):
        properties = by_name[tool_name].parameters["properties"]
        assert set(properties) == {"policy_name", "settings", "profile"}
        assert properties["settings"]["type"] == "object"


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

    repo_type = by_name["hmc_update_console_software"].parameters["properties"][
        "repository"
    ]["properties"]["type"]
    assert set(repo_type["enum"]) == set(_REPOSITORY_TYPES)


def test_metrics_tools_have_stable_output_schemas():
    """Metric fetch and discovery tools expose distinct stable schemas."""
    by_name = _tools_by_name()
    for tool_name in (
        "hmc_processed_metrics",
        "hmc_aggregated_metrics",
        "hmc_processed_metric_links",
        "hmc_aggregated_metric_links",
    ):
        tool = by_name[tool_name]
        assert tool.annotations is not None and tool.annotations.readOnlyHint is True, (
            tool_name
        )
        assert "mode" not in tool.parameters.get("properties", {})


def test_wait_for_job_has_one_stable_output_schema():
    by_name = _tools_by_name()
    schema = by_name["hmc_wait_for_job"].output_schema

    assert schema["type"] == "object"
    assert set(schema["properties"]) == {
        "job_id",
        "status",
        "timed_out",
        "error",
        "job",
    }
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["properties"]["job_id"] == {"type": "string"}
    assert schema["properties"]["timed_out"] == {"type": "boolean"}
    for nullable_field in ("status", "error", "job"):
        assert {
            variant["type"] for variant in schema["properties"][nullable_field]["anyOf"]
        } == {
            "object" if nullable_field == "job" else "string",
            "null",
        }

    assert by_name["hmc_get_job"].output_schema == {
        "properties": {
            "result": {
                "anyOf": [
                    {"additionalProperties": True, "type": "object"},
                    {"type": "null"},
                ]
            }
        },
        "required": ["result"],
        "type": "object",
        "x-fastmcp-wrap-result": True,
    }


# ------------------------------------------------------------------ #
# Delete precondition guards (hmc_delete_lpar / hmc_delete_vios)
# ------------------------------------------------------------------ #


def _mock_state_and_delete(router, state: str, status: int = 200):
    router.get(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/quick/PartitionState").mock(
        return_value=httpx.Response(status, text=state)
    )
    return router.delete(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(204)
    )


def test_delete_lpar_refuses_when_active(monkeypatch, mock_hmc):
    """Deleting a running LPAR is refused before any DELETE is issued."""
    _hmc_env(monkeypatch)
    delete_route = _mock_state_and_delete(mock_hmc, "running")

    with (
        patch(
            "hmc_mcp.operations_lpar.resolve_lpar_ownership_names",
            new=AsyncMock(return_value=("system-1", "lpar-1")),
        ),
        patch(
            "hmc_mcp.operations_lpar.authorize_lpar_mutation", new=AsyncMock()
        ) as guard,
        pytest.raises(HMCError) as exc_info,
    ):
        hmc_delete_lpar(SYSTEM_UUID, LPAR_UUID, ownership_override=True)

    assert exc_info.value.status_code == 409
    assert "not activated" in str(exc_info.value)
    assert not delete_route.called
    assert guard.await_args.kwargs == {"ownership_override": True}


def test_delete_lpar_succeeds_when_powered_off(monkeypatch, mock_hmc):
    _hmc_env(monkeypatch)
    _mock_state_and_delete(mock_hmc, "not activated")

    with (
        patch(
            "hmc_mcp.operations_lpar.resolve_lpar_ownership_names",
            new=AsyncMock(return_value=("system-1", "lpar-1")),
        ),
        patch(
            "hmc_mcp.operations_lpar.authorize_lpar_mutation", new=AsyncMock()
        ) as guard,
    ):
        result = hmc_delete_lpar(SYSTEM_UUID, LPAR_UUID, ownership_override=True)

    assert result == f"Deleted LPAR {LPAR_UUID}"
    assert guard.await_args.kwargs == {"ownership_override": True}


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


# ------------------------------------------------------------------ #
# hmc_power_on_lpar precondition guard
# ------------------------------------------------------------------ #

POWER_ON_JOB_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:job-uuid-power-on</id>
  <title>Job</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <Job xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <JobID>job-uuid-power-on</JobID>
      <Status>RUNNING</Status>
    </Job>
  </content>
</entry>
"""


def _mock_power_on_guard(router, state: str):
    router.get(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/quick/PartitionState").mock(
        return_value=httpx.Response(200, text=state)
    )
    return router.put(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/do/PowerOn").mock(
        return_value=httpx.Response(202, text=POWER_ON_JOB_ENTRY)
    )


def test_power_on_lpar_already_running_returns_message(monkeypatch, mock_hmc):
    """hmc_power_on_lpar returns an already-running dict without submitting a job."""
    from hmc_mcp.server import hmc_power_on_lpar

    _hmc_env(monkeypatch)
    power_on_route = _mock_power_on_guard(mock_hmc, "running")

    result = hmc_power_on_lpar(LPAR_UUID)

    assert not power_on_route.called
    assert result is not None
    assert result.get("already_running") is True
    assert LPAR_UUID in result.get("message", "")


def test_power_on_lpar_not_activated_submits_job(monkeypatch, mock_hmc):
    """hmc_power_on_lpar submits the PowerOn job when partition is not activated."""
    from hmc_mcp.server import hmc_power_on_lpar

    _hmc_env(monkeypatch)
    power_on_route = _mock_power_on_guard(mock_hmc, "not activated")

    result = hmc_power_on_lpar(LPAR_UUID)

    assert power_on_route.called
    assert result["Resource"]["JobID"] == "job-uuid-power-on"


def test_power_on_lpar_force_skips_guard(monkeypatch, mock_hmc):
    """hmc_power_on_lpar(force=True) submits the job even when running."""
    from hmc_mcp.server import hmc_power_on_lpar

    _hmc_env(monkeypatch)
    # When force=True the state check endpoint is not called; only the job PUT matters.
    power_on_route = mock_hmc.put(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/do/PowerOn"
    ).mock(return_value=httpx.Response(202, text=POWER_ON_JOB_ENTRY))

    result = hmc_power_on_lpar(LPAR_UUID, force=True)

    assert power_on_route.called
    assert result["Resource"]["JobID"] == "job-uuid-power-on"


# ------------------------------------------------------------------ #
# hmc_create_lpar name-collision guard
# ------------------------------------------------------------------ #

EXISTING_LPAR_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:{uuid}</id>
    <title>LogicalPartition:existing-lpar</title>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <LogicalPartition xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <PartitionName>existing-lpar</PartitionName>
        <PartitionState>not activated</PartitionState>
      </LogicalPartition>
    </content>
  </entry>
</feed>
""".format(uuid=LPAR_UUID)

EMPTY_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom"/>
"""

SYSTEM_UUID = "00000000-0000-0000-0000-000000000001"

NEW_LPAR_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:new-lpar-uuid-0001</id>
    <title>LogicalPartition:new-lpar</title>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <LogicalPartition xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <PartitionName>new-lpar</PartitionName>
      </LogicalPartition>
    </content>
  </entry>
</feed>
"""


def test_create_lpar_refuses_name_collision(monkeypatch, mock_hmc):
    """hmc_create_lpar raises ValueError when a partition with the same name exists."""
    from hmc_mcp.server import hmc_create_lpar

    _hmc_env(monkeypatch)
    mock_hmc.get(
        "/rest/api/uom/LogicalPartition/search/(PartitionName==existing-lpar)"
    ).mock(return_value=httpx.Response(200, text=EXISTING_LPAR_FEED))
    create_route = mock_hmc.put(
        f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition"
    )

    with pytest.raises(ValueError, match="existing-lpar"):
        hmc_create_lpar(SYSTEM_UUID, name="existing-lpar")

    assert not create_route.called


def test_create_lpar_proceeds_when_no_collision(monkeypatch, mock_hmc):
    """hmc_create_lpar creates the partition when no LPAR with the same name exists."""
    from unittest.mock import AsyncMock, patch
    from hmc_mcp.server import hmc_create_lpar

    _hmc_env(monkeypatch)
    mock_hmc.get(
        "/rest/api/uom/LogicalPartition/search/(PartitionName==new-lpar)"
    ).mock(return_value=httpx.Response(200, text=EMPTY_FEED))
    create_route = mock_hmc.put(
        f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition"
    ).mock(return_value=httpx.Response(201, text=NEW_LPAR_FEED))

    with (
        patch(
            "hmc_mcp.operations_lpar.stamp_lpar_ownership",
            new=AsyncMock(return_value="tok"),
        ),
        patch(
            "hmc_mcp.operations_lpar._system_name",
            new=AsyncMock(return_value="sys1"),
        ),
    ):
        result = hmc_create_lpar(SYSTEM_UUID, name="new-lpar")

    assert create_route.called
    # result is now wrapped: {"lpar": <entry>, "ownership_stamped": ..., "warnings": []}
    assert result.lpar["Resource"]["PartitionName"] == "new-lpar"
