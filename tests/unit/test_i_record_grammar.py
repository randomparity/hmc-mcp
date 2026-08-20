"""Regression tests: every HMC CLI ``-i`` attribute record is built by one guard.

``chsyscfg``/``mksyscfg`` take a comma-delimited, equals-delimited attribute
record as a single ``-i`` argument.  ``shlex.quote`` makes that record one safe
shell *word*; the HMC splits the record itself afterwards, so quoting does
nothing about the record's own delimiters.  A caller value containing ``,`` or
``=`` therefore used to add or override attributes the caller was never given.

:func:`hmc_mcp.ssh_commands.build_attribute_record` owns the record grammar.
These tests pin three things: the grammar itself, a per-site refusal for every
function that builds a record, and the coupling — a new ``-i`` site that skips
the builder fails here rather than waiting for a reviewer.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hmc_mcp.config import HMCConfig
from hmc_mcp.ssh import HMCCLIError
from hmc_mcp.ssh_commands import (
    assign_profile_io_slot,
    build_attribute_record,
    create_lpar_via_cli,
    set_lpar_description,
    set_lpar_msp,
    set_lpar_proc_compat,
    sync_lpar_profile,
    validate_lpar_description,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNED_ROOTS = (_REPO_ROOT / "src" / "hmc_mcp", _REPO_ROOT / "scripts")


def _config() -> HMCConfig:
    return HMCConfig(
        host="hmc.test",
        user="hscroot",
        password="abc123",  # pragma: allowlist secret
        _env_file=None,
    )


def _ssh_mock(stdout: str = "") -> MagicMock:
    """Return a minimal asyncssh connection mock."""
    result = MagicMock()
    result.stdout = stdout

    conn = AsyncMock()
    conn.run = AsyncMock(return_value=result)
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    return conn


# ---------------------------------------------------------------------- #
# build_attribute_record — the grammar itself
# ---------------------------------------------------------------------- #


def test_build_attribute_record_joins_pairs_in_order():
    """The builder renders ``k=v`` pairs comma-joined, in the order given."""
    record = build_attribute_record(
        [("name", "lpar1"), ("description", "web tier"), ("msp", 1)]
    )
    assert record == "name=lpar1,description=web tier,msp=1"


def test_build_attribute_record_allows_an_append_operator():
    """``io_slots+`` renders as the HMC's ``io_slots+=`` append form."""
    record = build_attribute_record([("io_slots+", "553713767//0")])
    assert record == "io_slots+=553713767//0"


@pytest.mark.parametrize(
    ("bad", "wording"),
    [(",", "comma"), ("=", "equals sign"), ('"', "double quote")],
)
def test_build_attribute_record_rejects_record_delimiters(bad, wording):
    """A structural character in any value is refused, naming char and field.

    The double quote is structure too: it is the HMC's own escape for a value
    containing a comma, so a value carrying one opens a quoted region that
    swallows the attributes after it.
    """
    with pytest.raises(HMCCLIError) as excinfo:
        build_attribute_record([("name", "lpar1"), ("description", f"x{bad}y")])
    message = str(excinfo.value)
    assert wording in message
    assert "description" in message


@pytest.mark.parametrize("control", ["\n", "\r", "\x00", "\x1b", "\x7f"])
def test_build_attribute_record_rejects_control_characters(control):
    """A control character is refused in every value, not only descriptions.

    The record is one line, and the ``-f`` file form reads the same data format
    one record per line — so a newline may terminate the record and a NUL may
    truncate it inside the HMC, acting on a different partition than the one
    the caller named.
    """
    with pytest.raises(HMCCLIError, match="control character"):
        build_attribute_record([("name", f"lp{control}ar")])


@pytest.mark.parametrize(
    "value",
    [
        "No comma name",  # spaces are legal in an unquoted HMC name value
        "lpar;name",
        "[hmc-mcp owner:agent-a created:2026-08-19]",
    ],
)
def test_build_attribute_record_keeps_characters_that_are_not_structure(value):
    """The grammar refuses record structure only, not everything unusual.

    IBM's own escaping note shows an unquoted ``name=No comma name``, so a
    space is not structure.  ADR 0011's ownership token needs spaces too.
    """
    assert build_attribute_record([("name", value)]) == f"name={value}"


def test_build_attribute_record_rejects_a_malformed_attribute_name():
    """An attribute name outside the HMC's identifier form is refused."""
    with pytest.raises(HMCCLIError, match="attribute name"):
        build_attribute_record([("na me", "lpar1")])


def test_build_attribute_record_rejects_a_repeated_attribute():
    """A record naming one attribute twice is refused, not sent.

    ADR 0045 records that the HMC's handling of a duplicate attribute was never
    verified; the component that owns the grammar should not be the one that
    produces such a record.
    """
    with pytest.raises(HMCCLIError, match="appears twice"):
        build_attribute_record([("name", "lp1"), ("name", "lp2")])


def test_build_attribute_record_rejects_an_empty_record():
    """A record with no attributes is a caller bug, not an empty command."""
    with pytest.raises(HMCCLIError, match="at least one attribute"):
        build_attribute_record([])


@pytest.mark.parametrize(("bad", "wording"), [(" ", "space"), (";", "semicolon")])
def test_set_lpar_description_keeps_its_historical_lpar_name_rejections(bad, wording):
    """Space and ';' stay refused where they always were, and only there.

    Neither is record structure, so the builder does not refuse them; extending
    the rejection to the other five records would refuse HMC-legal names on
    tools that accept them today (ADR 0045).
    """
    with pytest.raises(HMCCLIError, match=wording):
        asyncio.run(set_lpar_description(_config(), "sys", f"lp{bad}ar", "text"))


# ---------------------------------------------------------------------- #
# validate_lpar_description — the fast tool-layer refusal
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("bad", "wording"),
    [(",", "comma"), ("=", "equals sign"), ('"', "double quote")],
)
def test_validate_lpar_description_rejects_record_delimiters(bad, wording):
    """The description validator refuses record structure with ValueError."""
    with pytest.raises(ValueError) as excinfo:
        validate_lpar_description(f"owner{bad}alice")
    message = str(excinfo.value)
    assert wording in message
    assert "description" in message


def test_validate_lpar_description_still_accepts_an_ownership_token():
    """ADR 0011's ownership token has no record delimiter and stays valid."""
    validate_lpar_description("[hmc-mcp owner:agent-a created:2026-08-19]")


# ---------------------------------------------------------------------- #
# Per-site refusal — one hostile value for every function building a record
# ---------------------------------------------------------------------- #

HOSTILE = "x,injected=1"


def test_create_lpar_via_cli_rejects_a_hostile_name():
    with pytest.raises(HMCCLIError, match="comma"):
        asyncio.run(create_lpar_via_cli(_config(), "sys", HOSTILE))


def test_create_lpar_via_cli_rejects_a_hostile_profile_name():
    with pytest.raises(HMCCLIError, match="comma"):
        asyncio.run(
            create_lpar_via_cli(_config(), "sys", "lpar1", profile_name=HOSTILE)
        )


def test_set_lpar_description_rejects_a_hostile_description():
    with pytest.raises(ValueError, match="comma"):
        asyncio.run(set_lpar_description(_config(), "sys", "lpar1", HOSTILE))


def test_set_lpar_msp_rejects_a_hostile_lpar_name():
    """The msp record refuses a hostile name after the lpar_env probe."""
    conn = _ssh_mock("vioserver\n")
    with (
        patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn),
        pytest.raises(HMCCLIError, match="comma"),
    ):
        asyncio.run(set_lpar_msp(_config(), "sys", HOSTILE, True))


def test_set_lpar_proc_compat_rejects_a_hostile_mode():
    with pytest.raises(HMCCLIError, match="comma"):
        asyncio.run(set_lpar_proc_compat(_config(), "sys", "lpar1", HOSTILE))


def test_sync_lpar_profile_rejects_a_hostile_lpar_name():
    with pytest.raises(HMCCLIError, match="comma"):
        asyncio.run(sync_lpar_profile(_config(), "sys", HOSTILE))


def test_assign_profile_io_slot_rejects_a_hostile_drc_index():
    with pytest.raises(HMCCLIError, match="comma"):
        asyncio.run(
            assign_profile_io_slot(_config(), "sys", "lpar1", "prof1", HOSTILE)
        )


def test_assign_profile_io_slot_rejects_a_hostile_profile_name():
    with pytest.raises(HMCCLIError, match="comma"):
        asyncio.run(
            assign_profile_io_slot(_config(), "sys", "lpar1", HOSTILE, "553713767")
        )


def test_assign_profile_io_slot_rejects_a_hostile_lpar_name():
    with pytest.raises(HMCCLIError, match="comma"):
        asyncio.run(
            assign_profile_io_slot(_config(), "sys", HOSTILE, "prof1", "553713767")
        )


# ---------------------------------------------------------------------- #
# The coupling — a new -i site cannot skip the builder
# ---------------------------------------------------------------------- #

BUILDER_NAME = "build_attribute_record"


def _static_text(node: ast.AST) -> str:
    """Return the literal text of a string constant or f-string, or ``""``.

    For an f-string only the static segments are returned; interpolations
    contribute nothing, which is what a flag search wants.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value
            for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
    return ""


def _docstring_nodes(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[int]:
    """Return the ``id()`` of *func*'s docstring node, if it has one."""
    first = func.body[0] if func.body else None
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return {id(first.value)}
    return set()


RECORD_COMMANDS = ("chsyscfg", "mksyscfg")


def _builds_an_i_record(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when *func* composes a record command carrying the ``-i`` flag.

    Both signals must be present in *func*'s own non-docstring string literals:
    a command that takes an attribute record, and the ``-i`` flag that carries
    it.  Looking at the function rather than at a single literal keeps the
    check working when the command is assembled from several pieces.
    """
    skip = _docstring_nodes(func)
    texts = [
        _static_text(node) for node in ast.walk(func) if id(node) not in skip
    ]
    names_a_command = any(
        command in text for text in texts for command in RECORD_COMMANDS
    )
    carries_the_flag = any(
        " -i " in text or text.endswith(" -i") for text in texts
    )
    return names_a_command and carries_the_flag


def _is_builder_call(node: ast.AST) -> bool:
    """True when *node* is a call to :func:`build_attribute_record`."""
    if not isinstance(node, ast.Call):
        return False
    callee = node.func
    if isinstance(callee, ast.Name):
        return callee.id == BUILDER_NAME
    return isinstance(callee, ast.Attribute) and callee.attr == BUILDER_NAME


def _unwrap_shlex_quote(node: ast.AST) -> ast.AST:
    """Return the argument of a ``shlex.quote(...)`` call, else *node*."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "quote"
        and len(node.args) == 1
    ):
        return node.args[0]
    return node


def _builder_bound_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Return the local names assigned from a builder call inside *func*."""
    names: set[str] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign) or not _is_builder_call(node.value):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _unguarded_i_values(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Return the ``-i`` payload expressions in *func* not built by the builder.

    Follows the value actually interpolated after the ``-i`` flag rather than
    asking whether the builder is called anywhere in the function, so a
    function that builds one record correctly and a second by f-string is still
    reported.
    """
    bound = _builder_bound_names(func)
    skip = _docstring_nodes(func)
    unguarded: list[str] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.JoinedStr) or id(node) in skip:
            continue
        parts = node.values
        for index, part in enumerate(parts[:-1]):
            text = _static_text(part)
            if not text.rstrip().endswith("-i"):
                continue
            payload = parts[index + 1]
            if not isinstance(payload, ast.FormattedValue):
                unguarded.append(ast.unparse(part))
                continue
            expression = _unwrap_shlex_quote(payload.value)
            guarded = _is_builder_call(expression) or (
                isinstance(expression, ast.Name) and expression.id in bound
            )
            if not guarded:
                unguarded.append(ast.unparse(payload.value))
    return unguarded


def _record_building_functions() -> list[tuple[str, ast.AST]]:
    """Return every ``(qualified name, node)`` that builds an ``-i`` record."""
    found: list[tuple[str, ast.AST]] = []
    for path in sorted(p for root in SCANNED_ROOTS for p in root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if _builds_an_i_record(node):
                found.append((f"{path.name}::{node.name}", node))
    return found


def test_every_i_record_site_is_built_by_the_shared_builder():
    """Every value interpolated after ``-i`` must come from the record builder.

    This is the recurrence guard.  ``shlex.quote`` around an f-string looks
    safe and is not; the only durable defence is that the record grammar has
    exactly one implementation and every site reaches it.
    """
    sites = _record_building_functions()
    assert sites, "no -i record sites found — the AST scan stopped working"

    skipping = {
        name: unguarded
        for name, node in sites
        if (unguarded := _unguarded_i_values(node))
    }
    assert not skipping, (
        f"these -i payloads are not built by {BUILDER_NAME}(): {skipping}. "
        f"shlex.quote protects the shell word only; the record's own ',', '=' "
        f"and '\"' structure needs the builder."
    )


def test_the_scan_finds_every_known_record_site():
    """Pin the six known sites so a silently-narrowed scan is visible."""
    names = {name.split("::", 1)[1] for name, _ in _record_building_functions()}
    assert {
        "create_lpar_via_cli",
        "set_lpar_description",
        "set_lpar_msp",
        "set_lpar_proc_compat",
        "sync_lpar_profile",
        "assign_profile_io_slot",
    } <= names
