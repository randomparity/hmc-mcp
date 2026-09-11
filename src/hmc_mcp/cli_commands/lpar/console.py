"""Bounded, non-interactive LPAR console capture command."""

from __future__ import annotations

import base64
from typing import Any

import typer

from hmc_mcp.operations.lpar.console import capture_lpar_console_by_selector
from hmc_mcp.ssh.console import ConsoleCapture

from ..output import console, err_console, print_json
from ..runtime import with_client


def _capture_json(capture: ConsoleCapture) -> dict[str, Any]:
    return {
        "system": capture.system,
        "partition": capture.lpar,
        "stop_reason": capture.stop_reason,
        "released": capture.released,
        "error": capture.error,
        "bytes_captured": len(capture.data),
        "data_base64": base64.b64encode(capture.data).decode("ascii"),
    }


def lpars_capture_console(
    lpar_name_or_uuid: str = typer.Argument(
        ..., metavar="LPAR", help="LPAR name or UUID"
    ),
    system_name_or_uuid: str = typer.Argument(
        ..., metavar="SYSTEM", help="Managed system name or UUID"
    ),
    duration_seconds: float = typer.Option(
        30.0, "--duration", help="Maximum capture duration in seconds"
    ),
    max_bytes: int = typer.Option(
        65_536, "--max-bytes", help="Maximum number of bytes to capture"
    ),
    idle_timeout_seconds: float = typer.Option(
        10.0, "--idle-timeout", help="Stop after this many seconds without output"
    ),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """Capture a bounded snapshot of an LPAR's virtual console."""
    capture = with_client(
        lambda hmc: capture_lpar_console_by_selector(
            hmc,
            lpar_name_or_uuid,
            system_name_or_uuid,
            duration_seconds=duration_seconds,
            max_bytes=max_bytes,
            idle_timeout_seconds=idle_timeout_seconds,
        )
    )
    if as_json:
        print_json(_capture_json(capture))
        return

    metadata = (
        f"system: {capture.system}\n"
        f"partition: {capture.lpar}\n"
        f"stop reason: {capture.stop_reason}\n"
        f"released: {capture.released}\n"
        f"error: {capture.error}\n"
        f"bytes captured: {len(capture.data)}"
    )
    console.print(metadata, markup=False, highlight=False)
    console.print(
        ascii(capture.data.decode("utf-8", errors="backslashreplace")),
        markup=False,
        highlight=False,
    )
    if not capture.released:
        err_console.print(
            "WARNING: console release was not proven; the vterm may still be held.",
            markup=False,
            highlight=False,
        )


def register_commands(group: typer.Typer) -> None:
    """Register this module's commands on *group*."""
    group.command("capture-console")(lpars_capture_console)
