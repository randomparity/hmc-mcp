"""MCP tools for portable LPAR snapshot capture and local validation."""

from __future__ import annotations

from hmc_mcp._app import _run
from hmc_mcp.common import build_config, client_from_env
from hmc_mcp.operations_snapshot import capture_lpar_snapshot
from hmc_mcp.snapshot import inspect_snapshot, parse_snapshot
from hmc_mcp.tool_registry import tool_module

tool, register_tools, tool_security = tool_module()


@tool(effect="read", operation="snapshot.capture", target_kind="lpar")
def hmc_snapshot_capture(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    profile_name: str,
    profile: str | None = None,
) -> dict:
    """Capture replayable profile configuration and separate placement observations.

    Args:
        system_name_or_uuid: Managed-system name or UUID.
        lpar_name_or_uuid: LPAR name or UUID within the managed system.
        profile_name: Named HMC LPAR profile to capture.
        profile: Optional local HMC connection-profile name.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            snapshot = await capture_lpar_snapshot(
                hmc,
                build_config(profile=profile),
                system_name_or_uuid,
                lpar_name_or_uuid,
                profile_name,
            )
            return snapshot.model_dump(mode="json", exclude_none=True)

    return _run(_go)


@tool(
    effect="read",
    operation="snapshot.validate",
    target_kind="none",
    connection_argument=None,
)
def hmc_snapshot_validate(document: str) -> dict[str, object]:
    """Validate bounded snapshot JSON locally without HMC I/O.

    Args:
        document: UTF-8 snapshot JSON text, limited to 1 MiB.
    """
    snapshot = parse_snapshot(document)
    return {"valid": True, "format": snapshot.format, "version": snapshot.version}


@tool(
    effect="read",
    operation="snapshot.inspect",
    target_kind="none",
    connection_argument=None,
)
def hmc_snapshot_inspect(document: str) -> dict[str, object]:
    """Inspect a snapshot format and version locally without accepting it.

    Args:
        document: UTF-8 snapshot JSON text, limited to 1 MiB.
    """
    return inspect_snapshot(document).model_dump(mode="json")
