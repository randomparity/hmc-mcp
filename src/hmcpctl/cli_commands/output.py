"""Output formatting and terminal error handling for CLI commands."""

from __future__ import annotations

import json
from typing import Any, NoReturn, TypeVar

import typer
from rich.console import Console, RenderableType
from rich.style import StyleType
from rich.table import Table
from rich.text import Text, TextType

# Emoji codes are off: no CLI string uses one, and an HMC value such as ``a:smile:b``
# must print as received (#1029).
console = Console(emoji=False)
err_console = Console(stderr=True, emoji=False)

_T = TypeVar("_T")


class VerbatimTable(Table):
    """A table whose string title, caption, headers and cells print exactly as given.

    Rich parses a plain ``str`` as console markup, so an HMC name holding ``[word]``
    would lose that segment; a ``Text`` is never parsed (#1029).
    """

    def __init__(
        self,
        *,
        title: TextType | None = None,
        caption: TextType | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(title=_verbatim(title), caption=_verbatim(caption), **kwargs)

    def add_column(
        self, header: RenderableType = "", footer: RenderableType = "", **kwargs: Any
    ) -> None:
        super().add_column(_verbatim(header), _verbatim(footer), **kwargs)

    def add_row(
        self,
        *renderables: RenderableType | None,
        style: StyleType | None = None,
        end_section: bool = False,
    ) -> None:
        cells = (_verbatim(cell) for cell in renderables)
        super().add_row(*cells, style=style, end_section=end_section)


def _verbatim(value: _T) -> _T | Text:
    return Text(value) if isinstance(value, str) else value


def print_json(data: Any) -> None:
    console.print_json(json.dumps(data, default=str))


def _resource(entry: dict[str, Any]) -> dict[str, Any]:
    return entry.get("Resource") or {}


def first_field(entry: dict[str, Any], *names: str, default: str = "-") -> str:
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


def output(
    entries: Any,
    as_json: bool,
    table: VerbatimTable | None = None,
    empty_msg: str = "No results",
) -> None:
    if as_json:
        print_json(entries)
    elif table is not None:
        if table.row_count == 0:
            err_console.print(empty_msg, style="yellow", markup=False)
        else:
            console.print(table)
    elif not entries:
        err_console.print(empty_msg, style="yellow", markup=False)
    else:
        print_json(entries)


def fail(exc: Exception, *, code: int = 1) -> NoReturn:
    """Report an exception and exit with the requested runtime-error code."""
    err_console.print(Text.assemble(("Error:", "red"), f" {exc}"))
    raise typer.Exit(code=code)


def usage_error(message: str) -> NoReturn:
    """Report invalid command arguments using Typer's usage-error exit code."""
    err_console.print(Text.assemble(("Error:", "red"), f" {message}"))
    raise typer.Exit(code=2)


def partition_not_found(value: str) -> NoReturn:
    """Report a failed partition lookup consistently across CLI domains."""
    err_console.print(f"Partition '{value}' not found", style="yellow", markup=False)
    raise typer.Exit(code=1)
