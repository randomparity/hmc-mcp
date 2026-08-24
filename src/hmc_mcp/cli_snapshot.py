"""CLI commands for portable LPAR snapshots."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import typer

from hmc_mcp.cli_app import _client, _print_json, _run, _ssh_config, snapshot_app
from hmc_mcp.operations_snapshot import capture_lpar_snapshot
from hmc_mcp.snapshot import (
    inspect_snapshot,
    read_snapshot,
    read_snapshot_text,
    serialize_snapshot,
)


def _publish(path: Path, text: str) -> None:
    path.parent.mkdir(parents=False, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".hmc-mcp-snapshot-", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@snapshot_app.command("capture")
def snapshot_capture(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    profile_name: str,
    output: Path = typer.Option(..., "--output", help="New local snapshot path."),
) -> None:
    """Capture one portable LPAR snapshot without modifying the HMC."""

    async def _go():
        async with _client() as hmc:
            return await capture_lpar_snapshot(
                hmc,
                _ssh_config(),
                system_name_or_uuid,
                lpar_name_or_uuid,
                profile_name,
            )

    snapshot = _run(_go)
    _publish(output, serialize_snapshot(snapshot))
    _print_json({"path": str(output), "format": snapshot.format, "version": 1})


@snapshot_app.command("validate")
def snapshot_validate(path: Path) -> None:
    """Validate a local portable LPAR snapshot without HMC I/O."""
    snapshot = read_snapshot(path)
    _print_json({"valid": True, "format": snapshot.format, "version": snapshot.version})


@snapshot_app.command("inspect")
def snapshot_inspect(path: Path) -> None:
    """Inspect a local snapshot discriminator and version without HMC I/O."""
    _print_json(inspect_snapshot(read_snapshot_text(path)).model_dump(mode="json"))
