"""Output formatting and terminal error handling for CLI commands."""

from __future__ import annotations

import json
from typing import Any, NoReturn

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

console = Console()
err_console = Console(stderr=True)


def _print_json(data: Any) -> None:
    console.print_json(json.dumps(data, default=str))


def _resource(entry: dict[str, Any]) -> dict[str, Any]:
    return entry.get("Resource") or {}


def _first_field(entry: dict[str, Any], *names: str, default: str = "-") -> str:
    """Get the first present resource field as a string."""
    resource = _resource(entry)
    for name in names:
        value = resource.get(name)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, dict) and "text" in value:
            return str(value["text"])
    return default


def _output(
    entries: Any,
    as_json: bool,
    table: Table | None = None,
    empty_msg: str = "No results",
) -> None:
    if as_json:
        _print_json(entries)
    elif table is not None:
        if table.row_count == 0:
            err_console.print(f"[yellow]{empty_msg}[/yellow]")
        else:
            console.print(table)
    elif not entries:
        err_console.print(f"[yellow]{empty_msg}[/yellow]")
    else:
        _print_json(entries)


def _fail(exc: Exception, *, code: int = 1) -> NoReturn:
    """Report an exception and exit with the requested runtime-error code."""
    err_console.print(f"[red]Error:[/red] {escape(str(exc))}")
    raise typer.Exit(code=code)


def _usage_error(message: str) -> NoReturn:
    """Report invalid command arguments using Typer's usage-error exit code."""
    err_console.print(f"[red]Error:[/red] {escape(message)}")
    raise typer.Exit(code=2)


def _partition_not_found(value: str) -> NoReturn:
    """Report a failed partition lookup consistently across CLI domains."""
    err_console.print(f"[yellow]Partition '{escape(value)}' not found[/yellow]")
    raise typer.Exit(code=1)
