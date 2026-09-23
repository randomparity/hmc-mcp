"""Selector resolution and payload for bounded LPAR console capture."""

from __future__ import annotations

import base64
from typing import Any

from hmcpctl.client.core import HMCClient
from hmcpctl.resource_identity import (
    is_uuid,
    resolve_lpar_uuid,
    resolve_system_name,
    resolve_system_uuid,
)
from hmcpctl.ssh.console import ConsoleCapture, capture_lpar_console
from hmcpctl.ssh.lpar import resolve_lpar_cli_name


async def capture_lpar_console_by_selector(
    hmc: HMCClient,
    lpar_name_or_uuid: str,
    system_name_or_uuid: str,
    *,
    duration_seconds: float = 30.0,
    max_bytes: int = 65_536,
    idle_timeout_seconds: float = 10.0,
) -> ConsoleCapture:
    """Resolve selectors to HMC CLI names and capture the LPAR console."""
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    lpar_uuid = await resolve_lpar_uuid(
        hmc, lpar_name_or_uuid, system_name_or_uuid=system_uuid
    )
    system_name = (
        system_name_or_uuid
        if not is_uuid(system_name_or_uuid)
        else await resolve_system_name(hmc, system_uuid)
    )
    lpar_name = (
        lpar_name_or_uuid
        if not is_uuid(lpar_name_or_uuid)
        else await resolve_lpar_cli_name(hmc.config, lpar_uuid, system_name)
    )
    return await capture_lpar_console(
        hmc,
        system_name,
        lpar_name,
        duration_seconds=duration_seconds,
        max_bytes=max_bytes,
        idle_timeout_seconds=idle_timeout_seconds,
    )


def console_capture_payload(capture: ConsoleCapture) -> dict[str, Any]:
    """Project a capture onto the JSON shape the CLI and MCP tool share."""
    return {
        "system": capture.system,
        "partition": capture.lpar,
        "stop_reason": capture.stop_reason,
        "released": capture.released,
        "error": capture.error,
        "bytes_captured": len(capture.data),
        "data_base64": base64.b64encode(capture.data).decode("ascii"),
    }
