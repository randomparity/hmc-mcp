"""CLI commands for the raw REST escape hatch.
"""

from __future__ import annotations


import typer

from .cli_app import (
    _client,
    _run,
    console,
    raw_app,
)



@raw_app.command("get")
def raw_get(path: str = typer.Argument(..., help="Path under the HMC, e.g. /rest/api/uom/VirtualSwitch")) -> None:
    """Raw GET against the HMC; prints the XML response body."""

    async def _go():
        async with _client() as hmc:
            return await hmc.raw_get(path)

    console.print(_run(_go))


@raw_app.command("post")
def raw_post(
    path: str = typer.Argument(..., help="Path to POST to"),
    body: str = typer.Argument(..., help="XML request body (string) or @file.xml"),
    content_type: str = typer.Option("application/xml", "--content-type", "-c"),
) -> None:
    """Raw POST against the HMC. Use @file.xml to read the body from a file."""

    if body.startswith("@"):
        body = open(body[1:], encoding="utf-8").read()

    async def _go():
        async with _client() as hmc:
            return await hmc.raw_post(path, body, content_type=content_type)

    console.print(_run(_go))


