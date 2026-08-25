"""Direct tests for the CLI-group smoke script.

The script exists to stop `just verify` from naming groups by hand, so what these
tests hold it to is that its list is *derived*: a group registered on the app
appears without anyone editing the script, and an app that offers no groups is
reported rather than passed over.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import typer

from hmc_mcp.cli import app

ROOT = Path(__file__).parents[2]
MODULE_PATH = ROOT / "scripts" / "smoke_cli_groups.py"
MODULE_SPEC = importlib.util.spec_from_file_location("smoke_cli_groups", MODULE_PATH)
assert MODULE_SPEC is not None
assert MODULE_SPEC.loader is not None
smoke_cli_groups = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = smoke_cli_groups
MODULE_SPEC.loader.exec_module(smoke_cli_groups)

# The six the recipe used to name by hand. Kept as the floor the derived list has
# to clear -- the live count is not pinned, because pinning it is the defect one
# layer up.
FORMERLY_LISTED = ("lpars", "storage", "network", "templates", "metrics", "snapshot")


def _throwaway(help_text: str = "Throwaway.") -> typer.Typer:
    """A sub-app with one command, so Typer can build a group out of it."""
    group = typer.Typer(help=help_text)

    @group.command("noop")
    def _noop() -> None:
        """Do nothing."""

    return group


def test_group_names_match_the_apps_own_registry() -> None:
    names = smoke_cli_groups.group_names(app)

    # Two independent derivations of the same set: the built command tree (what
    # the script reads) and the registry `cli_app` writes to.
    assert set(names) == {group.name for group in app.registered_groups}
    assert set(FORMERLY_LISTED) < set(names)
    # `serve` is a top-level command, not a group; `serve --help` is not a group
    # help page and rendering it here would misreport what is covered.
    assert "serve" not in names


def test_a_newly_registered_group_is_picked_up_without_editing_the_script() -> None:
    """The bite: the derivation follows the app, not a list in the script."""
    before = smoke_cli_groups.group_names(app)
    assert "zzz-throwaway" not in before

    app.add_typer(_throwaway(), name="zzz-throwaway")
    try:
        after = smoke_cli_groups.group_names(app)
    finally:
        app.registered_groups.pop()

    assert set(after) - set(before) == {"zzz-throwaway"}
    assert smoke_cli_groups.group_names(app) == before


def test_the_live_cli_renders_every_group_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The same rendering `just verify` performs, inside the suite."""
    assert smoke_cli_groups.main([]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.startswith(
        f"Rendered --help for {len(smoke_cli_groups.group_names(app))} CLI groups."
    )


def test_an_app_with_no_groups_is_reported_rather_than_passed_over() -> None:
    bare = typer.Typer()

    @bare.command("only")
    def _only() -> None:
        """Not a group."""

    names, problems = smoke_cli_groups.smoke(bare)

    assert names == []
    assert problems == ["no CLI groups are registered; nothing was smoke-loaded"]


def test_a_group_whose_help_cannot_render_is_reported_with_its_traceback() -> None:
    # Typer's default `rich_markup_mode="rich"` renders help strings as markup, and
    # a stray closing tag raises only while *that* group's page renders -- the root
    # help lists the group without rendering its commands' short helps. This is the
    # failure the nine unlisted groups had nothing to catch it with.
    broken = typer.Typer()
    broken.add_typer(_throwaway(), name="fine")
    broken.add_typer(_throwaway("Broken [/bold] markup."), name="unrenderable")

    names, problems = smoke_cli_groups.smoke(broken)

    assert names == ["fine", "unrenderable"]
    assert len(problems) == 1
    assert problems[0].startswith("unrenderable --help exited 1")
    assert "MarkupError" in problems[0]


def test_a_group_typer_cannot_build_fails_instead_of_reporting_nothing() -> None:
    unsupported = typer.Typer()

    @unsupported.command("bad")
    def _bad(value: complex = typer.Option(1j, "--value")) -> None:
        """Take a parameter Typer has no converter for."""

    root = typer.Typer()
    root.add_typer(unsupported, name="unbuildable")

    with pytest.raises(RuntimeError, match="complex"):
        smoke_cli_groups.smoke(root)
