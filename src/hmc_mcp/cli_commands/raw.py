"""CLI commands for the raw REST escape hatch."""

from __future__ import annotations

from pathlib import Path

import typer

from .runtime import _with_client
from .output import _fail, console


def raw_get(
    path: str = typer.Argument(
        ..., help="Path under the HMC, e.g. /rest/api/uom/VirtualSwitch"
    ),
) -> None:
    """Raw GET against the HMC; prints the XML response body."""

    body, _headers = _with_client(lambda hmc: hmc.raw_get(path))
    console.print(body)


def raw_post(
    path: str = typer.Argument(..., help="Path to POST to"),
    body: str = typer.Argument(..., help="XML request body (string) or @file.xml"),
    content_type: str = typer.Option("application/xml", "--content-type", "-c"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Raw POST against the HMC. Use @file.xml to read the body from a file.

    A raw POST can create, modify, or delete HMC resources, so it is gated by
    the same confirmation convention as every other mutating CLI command.
    """

    if body.startswith("@"):
        body_path = Path(body[1:])
        try:
            body = body_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            _fail(OSError(f"cannot read body file {body_path}: {exc}"))

    if not yes and not typer.confirm(f"POST {path} to the HMC?"):
        raise typer.Abort()

    console.print(
        _with_client(lambda hmc: hmc.raw_post(path, body, content_type=content_type))
    )


def register_commands(group: typer.Typer) -> None:
    """Register this module’s commands on *group*."""
    group.command("get")(raw_get)
    group.command("post")(raw_post)
