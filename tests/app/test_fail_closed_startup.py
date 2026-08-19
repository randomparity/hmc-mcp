"""Composition and startup refuse without an access policy.

Covers docs/workflow/specs/2026-08-19-fail-closed-startup-design.md.

Spec item -> node id:
  R1   test_composing_without_a_policy_is_refused
  R1   test_composing_with_an_explicit_none_is_refused
  R2   test_the_module_level_application_is_gone
  R3   test_both_gates_are_derived_and_neither_is_none
  R4   test_the_transports_take_a_required_policy
  R5   test_serve_without_a_policy_exits_2
  R5a  test_serve_with_an_absent_policy_file_exits_1
  R5b  test_an_unresolvable_config_home_refuses_without_a_traceback
  R6   test_the_unselected_policy_warning_is_gone
  R12  test_the_generated_policy_authorizes_an_omitted_optional_selector
  R13  test_the_generated_policy_authorizes_a_vios_partition_id_call
  R14  test_the_generated_policy_denies_the_arbitrary_command
  R16e test_error_text_survives_square_brackets
  R16e test_error_text_survives_a_closing_tag_shape
  G1   test_the_composer_has_no_default_policy
  G2   test_the_legacy_policy_composes_the_registry_the_default_used_to
  G3   test_no_module_composes_an_application_at_import
  L1   test_serve_without_a_policy_exits_2_as_a_subprocess

R11 and R11a live in tests/app/test_cli_config.py; R7-R10, R9a and R9b in
tests/unit/test_legacy_policy.py. Each of the three modules carries its own header and
its own inventory guard, because the guard pattern reads ``__file__`` and cannot span
modules. R16, R16a-R16d and R17 are documentation requirements with no node id.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from hmc_mcp import server as server_module
from hmc_mcp.access_policy import DEFAULT_CONNECTION_TOKEN
from hmc_mcp.cli import app
from hmc_mcp.dispatch_scope import dispatch_authorizer
from hmc_mcp.legacy_policy import LEGACY_POLICY_NAME, compile_legacy_policy
from hmc_mcp.server import TOOL_SECURITY, create_mcp

REPO_ROOT = Path(__file__).parents[2]


@pytest.fixture
def steer_config(tmp_path, monkeypatch):
    """Point `config_dir()` at a temporary directory, on every platform.

    `HOME` alone does not steer it: Linux reads `XDG_CONFIG_HOME` first and darwin reads
    `Path.home()` with no override at all. Without all four steps these tests would read
    the developer's own profiles, and the generator tests would write a real
    `access-policy.toml` into their config directory.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    from hmc_mcp.config import config_dir

    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _legacy_policy(*, include_arbitrary_command: bool = False):
    return compile_legacy_policy(
        TOOL_SECURITY,
        (DEFAULT_CONNECTION_TOKEN,),
        include_arbitrary_command=include_arbitrary_command,
    )


def _names(application) -> set[str]:
    return {tool.name for tool in asyncio.run(application.list_tools())}


def _unstyle(text: str) -> str:
    """Strip ANSI styling so an assertion reads the words, not the escape codes."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


# ---------------------------------------------------------------------------
# R1 / G1 — composition requires a policy
# ---------------------------------------------------------------------------


def test_composing_without_a_policy_is_refused():
    """R1: the unbounded composition is gone, not merely discouraged."""
    with pytest.raises(TypeError):
        create_mcp()  # ty: ignore[missing-argument]


def test_composing_with_an_explicit_none_is_refused():
    """R1: an annotation does not refuse at runtime, so a guard has to.

    Without it ``create_mcp(None)`` would compose an application with no ceiling and no
    authorizer — exactly the state this change exists to remove — and every type checker
    in the world would still be happy at the call site that passed a ``None`` it got from
    somewhere else.
    """
    with pytest.raises(TypeError, match="init-access-policy"):
        create_mcp(None)  # ty: ignore[invalid-argument-type]


def test_the_composer_has_no_default_policy():
    """G1: a restored default would make R1's guard unreachable and silent."""
    parameter = inspect.signature(create_mcp).parameters["policy"]

    assert parameter.default is inspect.Parameter.empty


# ---------------------------------------------------------------------------
# R2 / G3 — no application exists at import
# ---------------------------------------------------------------------------


def test_the_module_level_application_is_gone():
    """R2: the one object that could have been an unbounded served application."""
    assert not hasattr(server_module, "mcp")

    with pytest.raises(ImportError):
        from hmc_mcp.server import mcp  # noqa: F401


def test_no_module_composes_an_application_at_import():
    """G3: import-time composition is what R2 removes; keep it removed.

    Parsed rather than grepped: a comment or a docstring mentioning ``create_mcp()``
    must not fail this, and a call nested inside a function is fine — only module scope
    is the hazard.
    """
    offenders = []
    for path in [
        *sorted((REPO_ROOT / "src" / "hmc_mcp").glob("*.py")),
        *sorted((REPO_ROOT / "scripts").glob("*.py")),
    ]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            # A call inside a function or class body runs when that is called, not at
            # import, so only the statements that execute on import are walked.
            if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                continue
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                func = call.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name == "create_mcp":
                    offenders.append(f"{path.name}:{call.lineno}")

    assert not offenders, f"application composed at import: {offenders}"


# ---------------------------------------------------------------------------
# R3 / R4 — the gates and the transports
# ---------------------------------------------------------------------------


def test_both_gates_are_derived_and_neither_is_none():
    """R3: a site given one gate without the other registers what it cannot authorize."""
    permits, authorize = server_module._gates(_legacy_policy())

    assert permits is not None and authorize is not None
    assert permits("hmc_list_systems") is True
    assert permits("hmc_run_command") is False


@pytest.mark.parametrize("entry_point", ["main_stdio", "main_http"])
def test_the_transports_take_a_required_policy(entry_point):
    """R4: neither transport can be entered without one."""
    parameter = inspect.signature(getattr(server_module, entry_point)).parameters[
        "access_policy"
    ]

    assert parameter.default is inspect.Parameter.empty


# ---------------------------------------------------------------------------
# G2 — legacy equivalence, as a test rather than as prose
# ---------------------------------------------------------------------------


def test_the_legacy_policy_composes_the_registry_the_default_used_to():
    """G2: the exact assertion the retired `test_no_policy_applies_no_ceiling` made.

    That test asserted `create_mcp()` registered `set(TOOL_SECURITY) - {hmc_run_command}`.
    The unpolicied composition is gone, so the claim moves to the policy that replaces it:
    this is what "legacy-equivalent" means, checked rather than asserted in a comment.
    """
    assert _names(create_mcp(_legacy_policy())) == set(TOOL_SECURITY) - {
        "hmc_run_command"
    }


def test_the_generated_policy_denies_the_arbitrary_command():
    """R14: the flag stays insufficient on its own (epic #218 requirement 6)."""
    assert "hmc_run_command" not in _names(create_mcp(_legacy_policy()))
    assert _legacy_policy(include_arbitrary_command=True).permits_tool("hmc_run_command")


# ---------------------------------------------------------------------------
# R12 / R13 — the two properties that make the generator legacy-*equivalent*
# ---------------------------------------------------------------------------


def test_the_generated_policy_authorizes_an_omitted_optional_selector():
    """R12: `all-targets` covers `target_scope.ABSENT`, and no targets table does.

    `hmc_list_lpars` declares an optional `system_name_or_uuid`; omitting it means "every
    system". Under a table-constrained grant that is a denial, so a generator that
    narrowed targets would silently retire every call that leaves an optional argument
    unset — a large fraction of ordinary use, and the reason the generated grant can only
    be `all-targets`.
    """
    authorize = dispatch_authorizer(_legacy_policy())
    security = TOOL_SECURITY["hmc_list_lpars"]

    # None, exactly as `tool_registry.authorized` binds an omitted optional argument.
    authorize(
        "hmc_list_lpars",
        security,
        {"profile": None, "system_name_or_uuid": None},
    )


def test_the_generated_policy_authorizes_a_vios_partition_id_call():
    """R13: the surface's only non-string selector, carried by three live tools.

    `target_scope._value` renders an int through `str()`. Without that arm these calls
    read as `UNREADABLE`, which denies under `all-targets` too — so the legacy-equivalent
    guarantee would quietly stop covering them.
    """
    authorize = dispatch_authorizer(_legacy_policy())

    authorize(
        "hmc_add_vscsi_adapter",
        TOOL_SECURITY["hmc_add_vscsi_adapter"],
        {"profile": None, "lpar_name_or_uuid": "db-01", "vios_partition_id": 2},
    )


# ---------------------------------------------------------------------------
# R6 — the warning whose condition is now unreachable
# ---------------------------------------------------------------------------


def test_the_unselected_policy_warning_is_gone():
    """R6: no server starts without a policy, so nothing can warn that none was selected."""
    assert not hasattr(server_module, "_unselected_policy_file")

    lines = server_module._startup_warnings(129, _legacy_policy(), False)

    assert not any("no access policy was selected" in line for line in lines)


# ---------------------------------------------------------------------------
# R16e — CLI error text is not markup
# ---------------------------------------------------------------------------


def test_error_text_survives_square_brackets(capsys):
    """R16e: rich deletes a bracketed token, and that token is R9b's whole payload.

    Verified against this checkout before the fix: `[profiles." prod"]` rendered as an
    empty string, so the generator's most important diagnostic named a key the operator
    could not see.
    """
    from hmc_mcp.cli_app import _fail

    with pytest.raises(typer.Exit):
        _fail(ValueError('came from a [profiles." prod"] key in config.toml'))

    assert '[profiles." prod"]' in _unstyle(capsys.readouterr().err)


def test_error_text_survives_a_closing_tag_shape(capsys):
    """R16e: and a closing-tag shape raises MarkupError, replacing exit 1 with a crash.

    `_check_entries` renders the offending value under `repr()`, so a profile key like
    `[/prod]` reaches this helper as literal text and must not be parsed as markup.
    """
    from hmc_mcp.cli_app import _usage_error

    with pytest.raises(typer.Exit):
        _usage_error("connections entry '[/prod]' is empty or padded")

    assert "'[/prod]'" in _unstyle(capsys.readouterr().err)


# ---------------------------------------------------------------------------
# R5 / R5a / R5b — the two refusals
# ---------------------------------------------------------------------------


def test_serve_without_a_policy_exits_2(steer_config):
    """R5: a usage error, naming the generator rather than only the problem."""
    result = CliRunner().invoke(app, ["serve"])

    assert result.exit_code == 2
    output = _unstyle(result.output)
    assert "config init-access-policy" in output
    assert "--access-policy" in output


def test_serve_with_an_absent_policy_file_exits_1(steer_config):
    """R5a: the likeliest upgrade order is edit the launcher, then find the file missing.

    Left alone that reaches `load_access_policy`'s unreadable-file arm and prints
    `cannot be read: [Errno 2] No such file or directory`, which names neither the
    generator nor the remedy.
    """
    result = CliRunner().invoke(app, ["serve", "--access-policy", LEGACY_POLICY_NAME])

    assert result.exit_code == 1
    assert "config init-access-policy" in _unstyle(result.output)


def test_an_unresolvable_config_home_refuses_without_a_traceback(monkeypatch):
    """R5b: `config_dir()` reaches `Path.home()`, which raises under a uid with no HOME.

    Unguarded, the refusal would raise while rendering itself — a traceback instead of a
    refusal, in the deployment least able to read one. This is what
    `_unselected_policy_file` guarded and what its removal must not take with it.
    """
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)

    def _no_home(cls):
        raise RuntimeError("Could not determine home directory")

    monkeypatch.setattr(Path, "home", classmethod(_no_home))

    result = CliRunner().invoke(app, ["serve"])

    assert result.exit_code == 2
    assert "config init-access-policy" in _unstyle(result.output)


# ---------------------------------------------------------------------------
# L1 — the refusal as an operator actually meets it
# ---------------------------------------------------------------------------


def test_serve_without_a_policy_exits_2_as_a_subprocess():
    """L1: an in-process assertion cannot see the exit code an operator sees.

    The console script, not `python -m hmc_mcp`: there is no `__main__.py`, so the module
    form would exit 1 with "No module named hmc_mcp.__main__" and satisfy a non-zero-exit
    intuition while proving nothing about the refusal.
    """
    executable = shutil.which("hmc-mcp")
    if executable is None:
        pytest.skip("the hmc-mcp console script is not on PATH")
    # The same-checkout assertion `test_authorization_audit_live.py` makes: a proof that
    # silently ran against another build is worse than no proof.
    assert str(REPO_ROOT) in str(Path(executable).resolve().parents[1]), (
        "the console script resolves outside this checkout, so the refusal proved would "
        "belong to a different build of hmc-mcp"
    )

    completed = subprocess.run(
        [executable, "serve"], capture_output=True, text=True, timeout=60
    )

    assert completed.returncode == 2
    assert "config init-access-policy" in _unstyle(completed.stderr)


# ---------------------------------------------------------------------------
# Inventory guard — see the module docstring
# ---------------------------------------------------------------------------


def test_every_spec_numbered_test_named_in_the_header_still_exists():
    """A header naming a deleted test is worse than no header.

    On #224 a slice-replacement refactor removed the only test that could observe a
    regression and left 2011 tests green.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    header = source.split('"""', 2)[1]

    named = set(re.findall(r"^  \w+\s+(test_\w+)$", header, flags=re.MULTILINE))
    defined = set(re.findall(r"^def (test_\w+)", source, flags=re.MULTILINE))

    assert named, "the header maps no test; the guard would pass vacuously"
    assert named <= defined, f"named in the header but not defined: {sorted(named - defined)}"
