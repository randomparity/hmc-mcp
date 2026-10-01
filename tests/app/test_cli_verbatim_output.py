"""HMC and argument text reaches the terminal verbatim, never as Rich markup -- #1029.

Rich parses every ``str`` it prints as console markup, so an HMC name, a raw REST body
or a CLI argument holding ``[word]`` loses that segment, and a crafted argument can
restyle a confirmation line. Each command test below prints such a value and asserts it
survives; the AST guard keeps new print sites and tables from reintroducing the defect.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from typer.testing import CliRunner

import hmcpctl.cli_commands
from hmcpctl import cli
from hmcpctl.cli_commands import console as cli_console
from hmcpctl.cli_commands import raw as cli_raw
from hmcpctl.cli_commands.lpar import config as cli_lpars
from hmcpctl.cli_commands.lpar import inventory as cli_lpar_inventory
from hmcpctl.cli_commands.output import VerbatimTable
from hmcpctl.cli_commands.systems import core as cli_systems
from hmcpctl.cli_commands.systems import memory_pools as cli_memory_pools
from hmcpctl.cli_commands.virtualization import pcie as cli_pcie
from hmcpctl.operations.affinity.ssh import ResourceGroupAffinityResult
from hmcpctl.ssh.affinity import MemoptResourceGroupSelector

RUNNER = CliRunner()

CLI_COMMANDS = Path(hmcpctl.cli_commands.__file__).parent
CONSOLES = frozenset({"console", "err_console"})
# Modules that may build a Rich console or table directly: the shared owner only.
RICH_OWNERS = frozenset({"output.py"})
RICH_MODULES = frozenset({"rich.table", "rich.console"})
SAFE_CALLS = frozenset({"escape", "len"})


def _is_call_to(node: ast.AST, names: frozenset[str]) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in names
    )


def _table_names(function: ast.AST) -> set[str]:
    """Names *function* binds only to ``VerbatimTable(...)`` (or annotates as one)."""
    bound: dict[str, bool] = {}
    for node in ast.walk(function):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    is_table = _is_call_to(node.value, frozenset({"VerbatimTable"}))
                    bound[target.id] = bound.get(target.id, True) and is_table
        elif isinstance(node, ast.arg) and node.annotation is not None:
            bound[node.arg] = "VerbatimTable" in ast.unparse(node.annotation)
    return {name for name, is_table in bound.items() if is_table}


def _markup_safe(node: ast.AST, tables: set[str]) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.JoinedStr):
        return all(
            _is_call_to(part.value, SAFE_CALLS)
            for part in node.values
            if isinstance(part, ast.FormattedValue)
        )
    if isinstance(node, ast.IfExp):
        return _markup_safe(node.body, tables) and _markup_safe(node.orelse, tables)
    if isinstance(node, ast.BoolOp):
        return all(_markup_safe(value, tables) for value in node.values)
    if isinstance(node, ast.Name):
        return node.id in tables
    return _is_call_to(node, frozenset({"escape"}))


def _passes_markup_false(call: ast.Call) -> bool:
    return any(
        keyword.arg == "markup"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is False
        for keyword in call.keywords
    )


def _is_console_print(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "print"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in CONSOLES
    )


def _imports_rich_owner_module(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(alias.name in RICH_MODULES for alias in node.names)
    if isinstance(node, ast.ImportFrom) and node.module == "rich":
        return any(f"rich.{alias.name}" in RICH_MODULES for alias in node.names)
    return isinstance(node, ast.ImportFrom) and node.module in RICH_MODULES


def markup_violations(source: str, filename: str) -> list[str]:
    """Return ``file:line`` entries for print sites and imports that break the rule."""
    tree = ast.parse(source)
    found: list[str] = []
    for scope in ast.walk(tree):
        if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        tables = _table_names(scope)
        for node in ast.walk(scope):
            if not _is_console_print(node) or _passes_markup_false(node):
                continue
            if not all(_markup_safe(arg, tables) for arg in node.args):
                found.append(f"{filename}:{node.lineno}: unescaped console.print")
    if Path(filename).name not in RICH_OWNERS:
        for node in ast.walk(tree):
            if _imports_rich_owner_module(node):
                found.append(
                    f"{filename}:{node.lineno}: imports rich.table/rich.console"
                )
    return sorted(set(found))


def test_cli_commands_print_external_text_verbatim():
    violations = [
        violation
        for path in sorted(CLI_COMMANDS.rglob("*.py"))
        for violation in markup_violations(
            path.read_text(encoding="utf-8"), str(path.relative_to(CLI_COMMANDS))
        )
    ]
    assert violations == [], (
        "Rich parses these as markup; wrap each interpolated value in "
        "rich.markup.escape(), pass markup=False, or build the table with "
        "output.VerbatimTable (#1029):\n" + "\n".join(violations)
    )


@pytest.mark.parametrize(
    ("snippet", "flagged"),
    (
        ('def f():\n    console.print(f"[green]Created {name}[/green]")', True),
        ("def f():\n    console.print(body)", True),
        ("from rich.table import Table", True),
        ("import rich.console", True),
        ("from rich import table", True),
        ('async def f():\n    err_console.print(f"{name}")', True),
        ('def f():\n    console.print(f"[green]{escape(name)}[/green]")', False),
        ('def f():\n    console.print(f"Created {name}", markup=False)', False),
        ("def f():\n    t = VerbatimTable()\n    console.print(t)", False),
    ),
)
def test_markup_guard_flags_unescaped_sites(snippet, flagged):
    assert bool(markup_violations(snippet, "probe.py")) is flagged


def test_verbatim_table_renders_title_header_and_cells_as_given():
    from rich.console import Console

    table = VerbatimTable(title="t[bold]x[/bold]", caption="cap[bold]x")
    table.add_column("h[bold]x")
    table.add_row("c[bold]x[/bold] :smile:")
    recorder = Console(record=True, width=80)
    recorder.print(table)

    text = recorder.export_text()
    for expected in (
        "t[bold]x[/bold]",
        "cap[bold]x",
        "h[bold]x",
        "c[bold]x[/bold] :smile:",
    ):
        assert expected in text


# A value Rich would read as a bold tag pair and an emoji code if it parsed it.
MARKED = "x[bold]y[/bold]:smile:"


def _stub(monkeypatch, module, value, *names):
    """Make *module*'s client/SSH runners return *value* without any I/O."""
    for name in names:
        monkeypatch.setattr(module, name, lambda *_args, **_kwargs: value)
    monkeypatch.setattr(module, "ssh_config", lambda: None, raising=False)


def _rg_result(mode):
    return ResourceGroupAffinityResult(
        capability="available",
        mode=mode,
        system="sys1",
        selector=MemoptResourceGroupSelector(all=True),
        items=[
            {
                "resource_group_name": MARKED,
                "resource_group_id": 3,
                "curr_score": 90,
                "predicted_score": 95,
            }
        ],
        unavailable_reason=None,
    )


@pytest.mark.parametrize(
    ("module", "value", "runners", "argv"),
    (
        (
            cli_lpars,
            _rg_result("current"),
            ("with_client",),
            ["lpars", "resource-group-memopt-scores", "sys1", "--all"],
        ),
        (
            cli_lpars,
            _rg_result("calculated"),
            ("with_client",),
            ["lpars", "plan-resource-group-memopt-scores", "sys1", "--all"],
        ),
        (cli_raw, (f"<V>{MARKED}</V>", {}), ("with_client",), ["raw", "get", "/p"]),
        (
            cli_raw,
            f"<V>{MARKED}</V>",
            ("with_client",),
            ["raw", "post", "/p", "<x/>", "--yes"],
        ),
        (
            cli_console,
            {"link": MARKED, "Resource": {"ManagementConsoleName": MARKED}},
            ("with_client",),
            ["console", "info"],
        ),
        (cli_lpar_inventory, MARKED, ("with_client",), ["lpars", "state", "lp1"]),
        (
            cli_lpars,
            "",
            ("with_client",),
            ["lpars", "set-description", MARKED, "sys1", "d", "--yes"],
        ),
        (
            cli_lpars,
            "",
            ("run_cli_coroutine",),
            ["lpars", "set-msp", MARKED, "sys1", "true", "--yes"],
        ),
        (
            cli_lpars,
            "prof1",
            ("run_cli_coroutine",),
            ["lpars", "set-proc-compat", MARKED, "sys1", "POWER10", "--yes"],
        ),
        (
            cli_pcie,
            "",
            ("with_client",),
            ["network", "set-sriov-mode", "sys1", MARKED, "sriov"],
        ),
        (
            cli_memory_pools,
            "",
            ("run_cli_coroutine",),
            ["memory-pools", "remove", "sys1", MARKED, "--yes"],
        ),
    ),
)
def test_issue_listed_sites_print_hmc_and_argument_text_verbatim(
    monkeypatch, module, value, runners, argv
):
    _stub(monkeypatch, module, value, *runners)

    result = RUNNER.invoke(cli.app, argv)

    assert result.exit_code == 0, result.output
    assert MARKED in result.stdout


def test_systems_list_table_prints_bracketed_system_name_verbatim(monkeypatch):
    system = {"UUID": "u1", "Resource": {"SystemName": MARKED, "State": "operating"}}
    _stub(monkeypatch, cli_systems, [system], "with_client")

    result = RUNNER.invoke(cli.app, ["systems", "list"], terminal_width=200)

    assert result.exit_code == 0, result.output
    assert MARKED in result.stdout
