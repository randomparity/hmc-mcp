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
    build_filter,
    list_fc_ports,
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


# ---------------------------------------------------------------------- #
# Quoted-pair support (ADR 0061)
# ---------------------------------------------------------------------- #


def test_build_attribute_record_quotes_a_marked_list_value():
    """A marked value carrying a comma renders as the IBM quoted pair."""
    record = build_attribute_record(
        [("port_vlan_id", 0), ("backing_devices", "dev1,dev2")],
        quoted=("backing_devices",),
        surface="chhwres -a record",
    )
    assert record == 'port_vlan_id=0,"backing_devices=dev1,dev2"'


def test_build_attribute_record_leaves_a_marked_value_without_commas_bare():
    """A marked value without a comma is byte-identical to the unmarked form."""
    record = build_attribute_record(
        [("backing_devices", "sriov/vios1/100/1/1/2")], quoted=("backing_devices",)
    )
    assert record == "backing_devices=sriov/vios1/100/1/1/2"


@pytest.mark.parametrize("bad", ['"', "="])
def test_build_attribute_record_refuses_structure_inside_marked_values(bad):
    """Only the comma is permitted inside a quoted region; the rest is refused."""
    with pytest.raises(HMCCLIError, match="backing_devices"):
        build_attribute_record(
            [("backing_devices", f"dev{bad}1")], quoted=("backing_devices",)
        )


@pytest.mark.parametrize("control", ["\n", "\r", "\x00"])
def test_build_attribute_record_refuses_control_characters_in_marked_values(control):
    """Control characters stay refused even in a quotable attribute."""
    with pytest.raises(HMCCLIError, match="control character"):
        build_attribute_record(
            [("backing_devices", f"dev{control}1")], quoted=("backing_devices",)
        )


def test_build_attribute_record_refuses_a_duplicate_across_marked_and_unmarked():
    """Duplicate detection compares attribute names regardless of quoting."""
    with pytest.raises(HMCCLIError, match="appears twice"):
        build_attribute_record(
            [("backing_devices", "a"), ("backing_devices", "b")],
            quoted=("backing_devices",),
        )


def test_duplicate_refusal_precedes_value_validation():
    """The duplicate pre-pass fires before any per-value check, as today."""
    with pytest.raises(HMCCLIError, match="appears twice"):
        build_attribute_record([("name", "a"), ("name", "b,x")])


def test_build_filter_joins_pairs_in_order():
    """build_filter renders name=value pairs comma-joined, in order."""
    record = build_filter([("lpar_names", "lpar1"), ("profile_names", "default")])
    assert record == "lpar_names=lpar1,profile_names=default"


def test_build_filter_refuses_a_delimiter_in_a_value():
    """A delimiter in a filter value would add or rewrite a pair; refused."""
    with pytest.raises(HMCCLIError, match="comma"):
        build_filter([("lpar_names", "lpar1,lpar2")])


def test_build_filter_names_the_filter_surface_in_refusals():
    """A --filter refusal names --filter, not -i."""
    with pytest.raises(HMCCLIError, match="--filter attribute"):
        build_filter([("lpar_names", "lpar1,lpar2")])


def test_build_filter_refuses_duplicates_and_empty_input():
    """Repeated filter attributes and empty expressions are refused."""
    with pytest.raises(HMCCLIError, match="twice"):
        build_filter([("lpar_names", "a"), ("lpar_names", "b")])
    with pytest.raises(HMCCLIError, match="at least one"):
        build_filter([])


# ---------------------------------------------------------------------- #
# Per-site refusal — one hostile value for every function building a filter
# ---------------------------------------------------------------------- #

HOSTILE_FILTER = "x,injected=1"


@pytest.mark.parametrize(
    ("fn_name", "extra_args"),
    [
        ("list_sriov_physical_port_rows", ()),
        ("list_sriov_configured_logical_port_rows", ()),
        ("read_sriov_lpar_state", ()),
        ("read_sriov_profile_ports", ("default_profile",)),
        ("list_fc_ports", ()),
        ("list_sea_adapters", ()),
        ("list_vnics", ()),
        ("list_vnic_rows", ()),
        ("read_vios_identity", ()),
        ("get_lpar_description", ()),
        ("get_lpar_msp", ()),
        ("set_lpar_msp", (True,)),
        ("get_lpar_proc_compat", ()),
    ],
)
def test_filter_site_refuses_a_hostile_name(fn_name, extra_args):
    """A delimiter-carrying name is refused before any command is built."""
    import hmc_mcp.ssh_commands as mod

    fn = getattr(mod, fn_name)
    with pytest.raises(HMCCLIError, match="comma"):
        asyncio.run(fn(_config(), "sys-a", HOSTILE_FILTER, *extra_args))


def test_list_fc_ports_renders_the_whole_expression_quoted():
    """A space-carrying name quotes the whole expression (normalized shape)."""
    sent = []

    async def fake_run(config, command):
        sent.append(command)
        return ""

    with patch("hmc_mcp.ssh_commands.run_hmc_command", side_effect=fake_run):
        asyncio.run(list_fc_ports(_config(), "system-a", "my name"))
    assert "--filter 'lpar_names=my name'" in sent[0]


def test_remove_memory_pool_refuses_a_delimiter_in_the_pool_name():
    """The mempool bare-value form validates against the same table."""
    from hmc_mcp.ssh_commands import remove_memory_pool

    with pytest.raises(HMCCLIError, match="comma"):
        asyncio.run(remove_memory_pool(_config(), "sys-a", "pool,extra=1"))


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


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Return the ``id()`` of every docstring constant in *tree*.

    A docstring quotes the command it documents, so it names ``chsyscfg`` and
    ``-i`` without building anything.  Module, class, and function docstrings
    are all excluded.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            ids.add(id(first.value))
    return ids


RECORD_COMMANDS = ("chsyscfg", "mksyscfg")


def _is_an_i_record_literal(node: ast.AST) -> bool:
    """True when one string literal both names a record command and flags ``-i``.

    Selection and payload inspection deliberately share this unit.  A rule that
    selected on the whole function and inspected one literal could select a
    function and then examine nothing — which passes, silently, exactly where
    the check matters most.

    The literal has to *open* with the command, which is what a command string
    does and what prose about a command does not.  Interpolations contribute no
    static text, so ``f"{host_prefix}chsyscfg …"`` still opens with it.
    """
    if not isinstance(node, ast.Constant | ast.JoinedStr):
        return False
    text = _static_text(node).lstrip()
    if not text.startswith(RECORD_COMMANDS):
        return False
    return " -i " in text or text.rstrip().endswith("-i")


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


def _unguarded_payloads(literal: ast.AST, bound: set[str]) -> list[str]:
    """Return *literal*'s ``-i`` payloads that do not come from the builder.

    *bound* holds the local names assigned from a builder call in the enclosing
    function.  A selected literal that yields no traceable payload at all is
    itself reported: an unexaminable record command is not a passing one.
    """
    if not isinstance(literal, ast.JoinedStr):
        return [
            f"{ast.unparse(literal)} (a record command that is not an f-string, "
            "so its -i payload cannot be traced to the builder)"
        ]
    unguarded: list[str] = []
    examined = 0
    parts = literal.values
    for index, part in enumerate(parts):
        if not _static_text(part).rstrip().endswith("-i"):
            continue
        payload = parts[index + 1] if index + 1 < len(parts) else None
        if not isinstance(payload, ast.FormattedValue):
            unguarded.append(
                f"{ast.unparse(literal)} (the -i flag is not followed by an "
                "interpolation in this f-string)"
            )
            continue
        examined += 1
        expression = _unwrap_shlex_quote(payload.value)
        guarded = _is_builder_call(expression) or (
            isinstance(expression, ast.Name) and expression.id in bound
        )
        if not guarded:
            unguarded.append(ast.unparse(payload.value))
    if not examined and not unguarded:
        unguarded.append(
            f"{ast.unparse(literal)} (selected as a record command, but no -i "
            "payload was found to check)"
        )
    return unguarded


def _i_record_literals(node: ast.AST) -> list[ast.AST]:
    """Return the ``-i`` record command literals inside *node*, docstrings aside."""
    skip = _docstring_nodes(node)
    return [
        child
        for child in ast.walk(node)
        if id(child) not in skip and _is_an_i_record_literal(child)
    ]


def _unguarded_i_values(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Return *func*'s ``-i`` payload expressions not built by the builder."""
    bound = _builder_bound_names(func)
    return [
        problem
        for literal in _i_record_literals(func)
        for problem in _unguarded_payloads(literal, bound)
    ]


def _scanned_modules() -> list[tuple[Path, ast.Module]]:
    """Return every scanned source file parsed to an AST."""
    return [
        (path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for path in sorted(p for root in SCANNED_ROOTS for p in root.rglob("*.py"))
    ]


def _record_building_functions() -> list[tuple[str, ast.AST]]:
    """Return every ``(qualified name, node)`` that builds an ``-i`` record."""
    found: list[tuple[str, ast.AST]] = []
    for path, tree in _scanned_modules():
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if _i_record_literals(node):
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


def test_no_record_command_literal_lives_outside_a_function():
    """A module-level record template would sit outside the payload check.

    The guard follows the payload interpolated into a literal inside the
    function that builds it.  A command template hoisted to a module constant
    is out of that view, so this refuses the hoist rather than pretending to
    check it.
    """
    hoisted: dict[str, list[str]] = {}
    for path, tree in _scanned_modules():
        in_functions = {
            id(literal)
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            for literal in _i_record_literals(node)
        }
        outside = [
            ast.unparse(literal)
            for literal in _i_record_literals(tree)
            if id(literal) not in in_functions
        ]
        if outside:
            hoisted[path.name] = outside
    assert not hoisted, (
        f"these -i record command literals live outside any function: {hoisted}. "
        f"Build the record with {BUILDER_NAME}() in the function that runs it."
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
        "_change_profile_io_slot",
    } <= names
