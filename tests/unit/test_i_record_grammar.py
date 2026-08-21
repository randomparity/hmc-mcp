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
# The coupling — a new -i, -a, or --filter site cannot skip its builder
# ---------------------------------------------------------------------- #

BUILDER_NAME = "build_attribute_record"
FILTER_BUILDER_NAME = "build_filter"


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
A_RECORD_COMMANDS = ("chhwres",)

# The one value-form `-a` site: `chhwres -r mempool -o r -a <pool_name>`
# carries a bare pool name, not name=value pairs (ADR 0061).  Exempted by
# enclosing function, so a future record-form `-a` site cannot hide behind
# it; tests/unit/test_ssh_quoting.py::
# test_remove_memory_pool_quotes_hostile_pool_name pins the bare emission.
VALUE_FORM_A_FUNCTIONS = {"remove_memory_pool"}


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

    A docstring quotes the command it documents, so it names ``chsyscfg``
    and ``--filter`` without building anything.  Module, class, and function
    docstrings are all excluded.
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


def _has_flag_ending_segment(node: ast.AST, flag: str) -> bool:
    """True when any static segment of *node* ends with *flag*.

    Segments, not the concatenation: a command may carry text after the
    flag's payload (``… --filter {…} -F … --header``), and only the segment
    boundary says where the payload starts.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.rstrip().endswith(flag)
    if isinstance(node, ast.JoinedStr):
        return any(
            isinstance(part, ast.Constant)
            and isinstance(part.value, str)
            and part.value.rstrip().endswith(flag)
            for part in node.values
        )
    return False


def _is_record_literal(node: ast.AST, commands: tuple[str, ...], flag: str) -> bool:
    """True when one string literal both names a command and carries *flag*.

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
    if not text.startswith(commands):
        return False
    return _has_flag_ending_segment(node, flag)


def _is_an_i_record_literal(node: ast.AST) -> bool:
    return _is_record_literal(node, RECORD_COMMANDS, "-i")


def _is_an_a_record_literal(node: ast.AST) -> bool:
    return _is_record_literal(node, A_RECORD_COMMANDS, "-a")


def _is_a_filter_literal(node: ast.AST) -> bool:
    """True when a literal carries a ``--filter`` flag awaiting its value.

    Unlike the record selections this keys on the flag alone, not the opening
    command: the migrated sites share one whole-expression shape, and the
    ``name=`` half lives inside the nested builder argument, not the outer
    text.
    """
    return _has_flag_ending_segment(node, "--filter")


def _is_builder_call(node: ast.AST, builder_name: str = BUILDER_NAME) -> bool:
    """True when *node* is a call to *builder_name*."""
    if not isinstance(node, ast.Call):
        return False
    callee = node.func
    if isinstance(callee, ast.Name):
        return callee.id == builder_name
    return isinstance(callee, ast.Attribute) and callee.attr == builder_name


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


def _builder_bound_names(
    func: ast.FunctionDef | ast.AsyncFunctionDef, builder_name: str
) -> set[str]:
    """Return the local names assigned from *builder_name* calls in *func*."""
    names: set[str] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign) or not _is_builder_call(
            node.value, builder_name
        ):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _flag_payload_problems(
    literal: ast.AST,
    bound: set[str],
    flag: str,
    builder_name: str,
) -> list[str]:
    """Return *literal*'s *flag* payloads not produced by *builder_name*.

    *bound* holds the local names assigned from the builder in the enclosing
    function.  A selected literal that yields no traceable payload at all is
    itself reported: an unexaminable command is not a passing one.
    """
    if not isinstance(literal, ast.JoinedStr):
        return [
            f"{ast.unparse(literal)} (a command that is not an f-string, "
            f"so its {flag} payload cannot be traced to {builder_name})"
        ]
    unguarded: list[str] = []
    examined = 0
    parts = literal.values
    for index, part in enumerate(parts):
        if not _static_text(part).rstrip().endswith(flag):
            continue
        payload = parts[index + 1] if index + 1 < len(parts) else None
        if not isinstance(payload, ast.FormattedValue):
            unguarded.append(
                f"{ast.unparse(literal)} (the {flag} flag is not followed by an "
                "interpolation in this f-string)"
            )
            continue
        examined += 1
        expression = _unwrap_shlex_quote(payload.value)
        guarded = _is_builder_call(expression, builder_name) or (
            isinstance(expression, ast.Name) and expression.id in bound
        )
        if not guarded:
            unguarded.append(ast.unparse(payload.value))
    if not examined and not unguarded:
        unguarded.append(
            f"{ast.unparse(literal)} (selected as a command carrying {flag}, "
            f"but no {flag} payload was found to check)"
        )
    return unguarded


def _joined_str_fragments(node: ast.AST) -> set[int]:
    """Return the ``id()`` of every Constant piece inside a JoinedStr.

    ``ast.walk`` descends into f-string internals, so a static segment such
    as ``" --filter "`` would otherwise be visited as if it were a whole
    command literal.  Only whole literals are selectable.
    """
    return {
        id(part)
        for node in ast.walk(node)
        if isinstance(node, ast.JoinedStr)
        for part in node.values
        if isinstance(part, ast.Constant)
    }


def _keyword_value_constants(node: ast.AST) -> set[int]:
    """Return the ``id()`` of every Constant passed as a keyword argument.

    A label such as ``surface="--filter"`` is data, not a command string;
    without this exclusion the filter selection would select its own
    builder's diagnostic label.
    """
    return {
        id(keyword.value)
        for walked in ast.walk(node)
        if isinstance(walked, ast.Call)
        for keyword in walked.keywords
        if isinstance(keyword.value, ast.Constant)
    }


def _selected_literals(node: ast.AST, predicate) -> list[ast.AST]:
    """Return the selected whole literals inside *node*, docstrings aside."""
    skip = (
        _docstring_nodes(node)
        | _joined_str_fragments(node)
        | _keyword_value_constants(node)
    )
    return [
        child
        for child in ast.walk(node)
        if id(child) not in skip and predicate(child)
    ]


def _unguarded_payloads_for(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    predicate,
    flag: str,
    builder_name: str,
) -> list[str]:
    """Return *func*'s *flag* payloads not built by *builder_name*."""
    bound = _builder_bound_names(func, builder_name)
    return [
        problem
        for literal in _selected_literals(func, predicate)
        for problem in _flag_payload_problems(literal, bound, flag, builder_name)
    ]


def _unguarded_i_values(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    return _unguarded_payloads_for(func, _is_an_i_record_literal, "-i", BUILDER_NAME)


def _unguarded_a_values(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    if func.name in VALUE_FORM_A_FUNCTIONS:
        return []
    return _unguarded_payloads_for(
        func, _is_an_a_record_literal, "-a", BUILDER_NAME
    )


def _unguarded_filter_values(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[str]:
    return _unguarded_payloads_for(
        func, _is_a_filter_literal, "--filter", FILTER_BUILDER_NAME
    )


SELECTIONS = (
    ("-i", _is_an_i_record_literal, _unguarded_i_values),
    ("-a", _is_an_a_record_literal, _unguarded_a_values),
    ("--filter", _is_a_filter_literal, _unguarded_filter_values),
)


def _scanned_modules() -> list[tuple[Path, ast.Module]]:
    """Return every scanned source file parsed to an AST."""
    return [
        (path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for path in sorted(p for root in SCANNED_ROOTS for p in root.rglob("*.py"))
    ]


def _selected_functions(predicate) -> list[tuple[str, ast.AST]]:
    """Return every ``(qualified name, node)`` whose literals *predicate* selects."""
    found: list[tuple[str, ast.AST]] = []
    for path, tree in _scanned_modules():
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if _selected_literals(node, predicate):
                found.append((f"{path.name}::{node.name}", node))
    return found


@pytest.mark.parametrize(
    ("label", "predicate", "checker"),
    SELECTIONS,
    ids=[label for label, _, _ in SELECTIONS],
)
def test_every_site_is_built_by_its_shared_builder(label, predicate, checker):
    """Every value interpolated after a grammar-carrying flag is builder-built.

    This is the recurrence guard.  ``shlex.quote`` around an f-string looks
    safe and is not; the only durable defence is that the grammar has exactly
    one implementation and every site reaches it.
    """
    sites = _selected_functions(predicate)
    assert sites, f"no {label} sites found — the AST scan stopped working"

    skipping = {
        name: unguarded
        for name, node in sites
        if (unguarded := checker(node))
    }
    assert not skipping, (
        f"these {label} payloads are not built by their shared builder: "
        f"{skipping}. shlex.quote protects the shell word only; the "
        'grammar\'s own ",", "=" and "\"" structure needs the builder.'
    )


@pytest.mark.parametrize(
    ("label", "predicate"),
    [(label, predicate) for label, predicate, _ in SELECTIONS],
    ids=[label for label, _, _ in SELECTIONS],
)
def test_no_command_literal_lives_outside_a_function(label, predicate):
    """A module-level command template would sit outside the payload check.

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
            for literal in _selected_literals(node, predicate)
        }
        outside = [
            ast.unparse(literal)
            for literal in _selected_literals(tree, predicate)
            if id(literal) not in in_functions
        ]
        if outside:
            hoisted[path.name] = outside
    assert not hoisted, (
        f"these {label} command literals live outside any function: {hoisted}. "
        "Build the command with its shared builder in the function that runs it."
    )


def test_the_scan_finds_every_known_site():
    """Pin every known site per category so a narrowed scan is visible.

    Set equality, not subset: an extra unknown site surfaces exactly like a
    missing one.
    """
    by_label: dict[str, set[str]] = {}
    for label, predicate, _ in SELECTIONS:
        by_label[label] = {
            name.split("::", 1)[1] for name, _ in _selected_functions(predicate)
        }

    assert by_label["-i"] == {
        "create_lpar_via_cli",
        "set_lpar_description",
        "set_lpar_msp",
        "set_lpar_proc_compat",
        "sync_lpar_profile",
        "_change_profile_io_slot",
        "unassign_sriov_logical_port_profile",
    }
    assert by_label["-a"] == {
        "assign_sriov_logical_port_dynamic",
        "add_vnic_backing",
        "remove_memory_pool",
    }
    assert by_label["--filter"] == {
        "list_sriov_physical_port_rows",
        "list_sriov_configured_logical_port_rows",
        "read_sriov_lpar_state",
        "read_sriov_profile_ports",
        "list_fc_ports",
        "list_sea_adapters",
        "list_vnics",
        "list_vnic_rows",
        "read_vios_identity",
        "get_lpar_description",
        "get_lpar_msp",
        "set_lpar_msp",
        "get_lpar_proc_compat",
        "hmc_list_vios_backups",
        "capture_lpar_baseline",
        "mutate_lpar_properties",
        "restore_lpar_baseline",
    }


def test_prose_docstrings_are_excluded_from_selection():
    """The four ``--filter`` prose docstrings are never selected as sites."""
    saw_a_docstring = False
    for path, tree in _scanned_modules():
        skip = _docstring_nodes(tree)
        for label, predicate, _ in SELECTIONS:
            selected = {id(node) for node in _selected_literals(tree, predicate)}
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and "--filter" in node.value
                ):
                    if id(node) in skip:
                        saw_a_docstring = True
                        assert id(node) not in selected
    assert saw_a_docstring, "precondition lost: no prose docstring names --filter"


def _function_from_source(source: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """Parse *source* and return its first function definition."""
    node = ast.parse(source)
    return next(
        item
        for item in node.body
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
    )


def test_the_scan_reports_an_unguarded_whole_expression_filter():
    """A synthetic unguarded filter literal fails; a builder-built one passes."""
    unguarded_func = _function_from_source(
        "\n".join(
            [
                "def f(x):",
                "    cmd = f'lssyscfg -r lpar -m sys --filter {x}'",
                "    return cmd",
            ]
        )
    )
    assert _unguarded_filter_values(unguarded_func)

    guarded_func = _function_from_source(
        "\n".join(
            [
                "import shlex",
                "def f(x):",
                "    cmd = f'lssyscfg -r lpar -m sys --filter {shlex.quote(build_filter([(\"lpar_names\", x)]))}'",
                "    return cmd",
            ]
        )
    )
    assert _unguarded_filter_values(guarded_func) == []


def test_the_value_form_a_site_is_exempt_by_enclosing_function():
    """The mempool bare-value form is skipped; a renamed copy would not be."""
    template = [
        "import shlex",
        "def {name}(config, system, pool_name):",
        "    cmd = f'chhwres -r mempool -m sys -o r -a {shlex.quote(pool_name)}'",
        "    return cmd",
    ]
    exempt_func = _function_from_source(
        "\n".join(template).replace("{name}", "remove_memory_pool")
    )
    assert _unguarded_a_values(exempt_func) == []

    other_func = _function_from_source(
        "\n".join(template).replace("{name}", "remove_pool_copy")
    )
    assert _unguarded_a_values(other_func)
