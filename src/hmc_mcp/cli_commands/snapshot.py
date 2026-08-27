"""CLI commands for portable LPAR snapshots."""

from __future__ import annotations

import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import NoReturn

import typer

from hmc_mcp.cli_commands.app import _client, _print_json, _run, _ssh_config, snapshot_app
from hmc_mcp.operations.snapshot import assess_snapshot_affinity, capture_lpar_snapshot
from hmc_mcp.affinity_assessment import PolicyState
from hmc_mcp.snapshot import (
    SnapshotValidationError,
    inspect_snapshot,
    read_snapshot,
    read_snapshot_text,
    serialize_snapshot,
)


def _fail(error: Exception) -> NoReturn:
    typer.echo(f"Error: {error}", err=True)
    raise typer.Exit(1) from error


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

    try:
        snapshot = _run(_go)
        _publish(output, serialize_snapshot(snapshot))
    except (SnapshotValidationError, OSError) as exc:
        _fail(exc)
    _print_json({"path": str(output), "format": snapshot.format, "version": 1})


@snapshot_app.command("validate")
def snapshot_validate(path: Path) -> None:
    """Validate a local portable LPAR snapshot without HMC I/O."""
    try:
        snapshot = read_snapshot(path)
    except (SnapshotValidationError, OSError) as exc:
        _fail(exc)
    _print_json({"valid": True, "format": snapshot.format, "version": snapshot.version})


@snapshot_app.command("inspect")
def snapshot_inspect(path: Path) -> None:
    """Inspect a local snapshot discriminator and version without HMC I/O."""
    try:
        result = inspect_snapshot(read_snapshot_text(path))
    except (SnapshotValidationError, OSError) as exc:
        _fail(exc)
    _print_json(result.model_dump(mode="json"))


@snapshot_app.command("assess-affinity")
def snapshot_assess_affinity(
    path: Path,
    current_score: int = typer.Option(..., "--current-score"),
    predicted_score: int = typer.Option(..., "--predicted-score"),
    policy_state: PolicyState = typer.Option("absent", "--policy-state"),
    configured_minimum: int | None = typer.Option(None, "--configured-minimum"),
    regression_threshold: int | None = typer.Option(None, "--regression-threshold"),
    optimization_threshold: int | None = typer.Option(None, "--optimization-threshold"),
    stale_after_seconds: int = typer.Option(86400, "--stale-after-seconds"),
) -> None:
    """Assess captured and explicit current affinity evidence without mutation."""
    try:
        result = _run(
            lambda: assess_snapshot_affinity(
                read_snapshot_text(path),
                current_score=current_score,
                predicted_score=predicted_score,
                policy_state=policy_state,
                configured_minimum=configured_minimum,
                regression_threshold=regression_threshold,
                optimization_threshold=optimization_threshold,
                stale_after_seconds=stale_after_seconds,
            )
        )
    except (SnapshotValidationError, OSError, ValueError) as exc:
        _fail(exc)
    _print_json(asdict(result))
