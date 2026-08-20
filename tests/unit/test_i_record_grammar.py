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

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "hmc_mcp"


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
    [(",", "comma"), ("=", "equals sign")],
)
def test_build_attribute_record_rejects_record_delimiters(bad, wording):
    """A structural character in any value is refused, naming char and field."""
    with pytest.raises(HMCCLIError) as excinfo:
        build_attribute_record([("name", "lpar1"), ("description", f"x{bad}y")])
    message = str(excinfo.value)
    assert wording in message
    assert "description" in message


@pytest.mark.parametrize(("bad", "wording"), [(" ", "space"), (";", "semicolon")])
@pytest.mark.parametrize("attribute", ["name", "lpar_name", "profile_name"])
def test_build_attribute_record_rejects_unsafe_characters_in_object_names(
    attribute, bad, wording
):
    """Attributes carrying an HMC object name also refuse a space and a ';'."""
    with pytest.raises(HMCCLIError) as excinfo:
        build_attribute_record([(attribute, f"lp{bad}ar")])
    message = str(excinfo.value)
    assert wording in message
    assert attribute in message


def test_build_attribute_record_allows_spaces_in_a_description():
    """A description keeps spaces — the ownership token of ADR 0011 needs them."""
    record = build_attribute_record(
        [("name", "lpar1"), ("description", "[hmc-mcp owner:agent-a created:2026-08-19]")]
    )
    assert record == "name=lpar1,description=[hmc-mcp owner:agent-a created:2026-08-19]"


def test_build_attribute_record_rejects_a_malformed_attribute_name():
    """An attribute name outside the HMC's identifier form is refused."""
    with pytest.raises(HMCCLIError, match="attribute name"):
        build_attribute_record([("na me", "lpar1")])


def test_build_attribute_record_rejects_an_empty_record():
    """A record with no attributes is a caller bug, not an empty command."""
    with pytest.raises(HMCCLIError, match="at least one attribute"):
        build_attribute_record([])


# ---------------------------------------------------------------------- #
# validate_lpar_description — the fast tool-layer refusal
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize(("bad", "wording"), [(",", "comma"), ("=", "equals sign")])
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


def _calls_builder(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when *func* calls :func:`build_attribute_record`."""
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        if isinstance(callee, ast.Name) and callee.id == BUILDER_NAME:
            return True
        if isinstance(callee, ast.Attribute) and callee.attr == BUILDER_NAME:
            return True
    return False


def _record_building_functions() -> list[tuple[str, ast.AST]]:
    """Return every ``(qualified name, node)`` that builds an ``-i`` record."""
    found: list[tuple[str, ast.AST]] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if _builds_an_i_record(node):
                found.append((f"{path.name}::{node.name}", node))
    return found


def test_every_i_record_site_is_built_by_the_shared_builder():
    """A function that builds a ``-i`` command must call the record builder.

    This is the recurrence guard.  ``shlex.quote`` around an f-string looks
    safe and is not; the only durable defence is that the record grammar has
    exactly one implementation and every site reaches it.
    """
    sites = _record_building_functions()
    assert sites, "no -i record sites found — the AST scan stopped working"

    skipping = [name for name, node in sites if not _calls_builder(node)]
    assert not skipping, (
        f"these functions build an HMC CLI -i attribute record without "
        f"{BUILDER_NAME}(): {skipping}. shlex.quote protects the shell word "
        f"only; the record's own ',' and '=' delimiters need the builder."
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
