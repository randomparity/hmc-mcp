"""MCP tool for bounded, non-interactive LPAR console capture (issue #385)."""

from __future__ import annotations

import base64
from typing import Any

from .._app import _run
from ..config import build_config
from ..client.client_factory import client_from_env
from ..resource_identity import is_uuid, resolve_lpar_uuid, resolve_system_name, resolve_system_uuid
from ..console_capture import capture_lpar_console
from ..ssh_lpar import _ssh_lpar_name
from ..tool_registry import tool_module

tool, register_tools, tool_security = tool_module()


@tool(effect="mutate", operation="lpar.capture_console", target_kind="lpar")
def hmc_capture_lpar_console(
    lpar_name_or_uuid: str,
    system_name_or_uuid: str,
    duration_seconds: float = 30.0,
    max_bytes: int = 65_536,
    idle_timeout_seconds: float = 10.0,
    profile: str | None = None,
) -> dict[str, Any]:
    """Capture a bounded snapshot of an LPAR's virtual console (mkvterm).

    Runs the HMC ``mkvterm`` CLI over SSH for at most ``duration_seconds``,
    returning the raw console bytes base64-encoded in ``data_base64`` (the
    stream is binary — escape sequences, partial UTF-8 — so no decoding is
    imposed), plus ``stop_reason`` (``duration`` / ``max_bytes`` / ``idle`` /
    ``remote-close`` / ``error``) and an honest ``released`` flag.

    This is a capture, not a terminal: stdin is sealed by construction, so no
    byte can reach the partition — a partition at an SMS menu, firmware
    prompt, or installer prompt cannot act on stray input. The partition's
    single vterm slot is held for the duration of the capture; if another
    session already holds it, the call fails with a distinct contention error
    and never force-closes that session. On every exit path the capture runs
    ``rmvterm`` and then *proves* the release by opening a fresh ``mkvterm``
    from an independent session; ``released`` is true only when that proof
    succeeds. If it is false, the partition's console may remain held — run
    ``rmvterm -m <system> -p <lpar>`` deliberately, or use the HMC UI, to
    recover it.

    Useful when a NIM install fails and the only diagnosis lives on the
    console: boot messages, SMS menus, open-firmware output, BOS install
    error screens.

    Requires HMC authority for mkvterm/rmvterm (typically hmcsuperadmin,
    e.g. hscroot).

    Args:
        lpar_name_or_uuid: Partition name or UUID whose console to capture.
        system_name_or_uuid: Managed-system name or UUID hosting the
            partition.
        duration_seconds: Wall-clock cap on the whole capture (max 3600).
        max_bytes: Cap on captured bytes (max 1048576); truncation never
            splits a multi-byte UTF-8 sequence or an incomplete ANSI escape.
        idle_timeout_seconds: Client-side cap on silence — time since the
            last received byte; an idle HMC vterm stream never closes by
            itself and carries no keepalives.
        profile: Optional TOML profile name; uses environment defaults when
            omitted.
    """

    async def _go() -> dict[str, Any]:
        async with client_from_env(profile) as hmc:
            system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
            lpar_uuid = await resolve_lpar_uuid(
                hmc, lpar_name_or_uuid, system_name_or_uuid=system_uuid
            )
            system_name = (
                system_name_or_uuid
                if not is_uuid(system_name_or_uuid)
                else await resolve_system_name(hmc, system_uuid)
            )
        config = build_config(profile=profile)
        lpar_name = (
            lpar_name_or_uuid
            if not is_uuid(lpar_name_or_uuid)
            else await _ssh_lpar_name(config, lpar_uuid, system_name)
        )
        capture = await capture_lpar_console(
            config,
            system_name,
            lpar_name,
            duration_seconds=duration_seconds,
            max_bytes=max_bytes,
            idle_timeout_seconds=idle_timeout_seconds,
        )
        return {
            "system": capture.system,
            "partition": capture.lpar,
            "stop_reason": capture.stop_reason,
            "released": capture.released,
            "bytes_captured": len(capture.data),
            "data_base64": base64.b64encode(capture.data).decode("ascii"),
        }

    return _run(_go)
