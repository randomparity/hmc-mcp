"""CLI commands for portable LPAR snapshots."""

from __future__ import annotations

import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import NoReturn

import typer

from hmc_mcp.cli_commands.runtime import client, run
from hmc_mcp.cli_commands.output import print_json
from hmc_mcp.snapshots.operations import assess_snapshot_affinity, capture_lpar_snapshot
from hmc_mcp.operations.affinity import PolicyState
from hmc_mcp.snapshots import (
    SnapshotValidationError,
    inspect_snapshot,
    read_snapshot,
    read_snapshot_text,
    serialize_snapshot,
)


def fail(error: Exception) -> NoReturn:
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


def snapshot_capture(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    profile_name: str,
    output: Path = typer.Option(..., "--output", help="New local snapshot path."),
) -> None:
    """Capture one portable LPAR snapshot without modifying the HMC."""

    async def _go():
        async with client() as hmc:
            return await capture_lpar_snapshot(
                hmc,
                system_name_or_uuid,
                lpar_name_or_uuid,
                profile_name,
            )

    try:
        snapshot = run(_go)
        _publish(output, serialize_snapshot(snapshot))
    except (SnapshotValidationError, OSError) as exc:
        fail(exc)
    print_json({"path": str(output), "format": snapshot.format, "version": 1})


def snapshot_validate(path: Path) -> None:
    """Validate a local portable LPAR snapshot without HMC I/O."""
    try:
        snapshot = read_snapshot(path)
    except (SnapshotValidationError, OSError) as exc:
        fail(exc)
    print_json({"valid": True, "format": snapshot.format, "version": snapshot.version})


def snapshot_inspect(path: Path) -> None:
    """Inspect a local snapshot discriminator and version without HMC I/O."""
    try:
        result = inspect_snapshot(read_snapshot_text(path))
    except (SnapshotValidationError, OSError) as exc:
        fail(exc)
    print_json(result.model_dump(mode="json"))


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
        result = run(
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
        fail(exc)
    print_json(asdict(result))


def register_commands(group: typer.Typer) -> None:
    """Register this module’s commands on *group*."""
    group.command("capture")(snapshot_capture)
    group.command("validate")(snapshot_validate)
    group.command("inspect")(snapshot_inspect)
    group.command("assess-affinity")(snapshot_assess_affinity)
