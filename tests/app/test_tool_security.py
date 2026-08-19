"""Exhaustive contract tests for the live tool security classification.

Every registered MCP tool must carry one authoritative ToolSecurity record, and
the MCP annotations shipped to clients must be derived from it. These tests fail
when a tool omits the metadata, contradicts its handler, or silently changes
classification. See docs/adr/0035-enforceable-tool-security-metadata.md.
"""

from __future__ import annotations

import asyncio
import ast
from pathlib import Path

import pytest

from hmc_mcp import server_command
from hmc_mcp.server import TOOL_SECURITY, mcp
from hmc_mcp.tool_registry import (
    EFFECTS,
    REQUIRED_TARGET_ARGUMENTS,
    annotations_for,
    validate_security,
)

# The classification as it stood before ADR 0035, transcribed from _app.py at
# 20f3068. A frozen regression snapshot: no tool is ever added here.
LEGACY_READ_ONLY = frozenset(
    {
        "hmc_console_info",
        "hmc_list_systems",
        "hmc_system_summary",
        "hmc_list_lpars",
        "hmc_get_lpar",
        "hmc_get_lpar_state",
        "hmc_lpar_summary",
        "hmc_list_vios",
        "hmc_get_vios",
        "hmc_list_resources",
        "hmc_get_job",
        "hmc_list_recent_jobs",
        "hmc_fleet_health",
        "hmc_capacity_report",
        "hmc_find_placement",
        "hmc_get_system",
        "hmc_wait_for_job",
        "hmc_list_adapters",
        "hmc_list_configured_hosts",
        "hmc_list_volume_groups",
        "hmc_list_virtual_switches",
        "hmc_list_virtual_networks",
        "hmc_list_network_bridges",
        "hmc_list_fc_ports",
        "hmc_list_sea_adapters",
        "hmc_list_partition_templates",
        "hmc_get_partition_template",
        "hmc_list_clusters",
        "hmc_list_shared_storage_pools",
        "hmc_get_shared_storage_pool",
        "hmc_get_pcm_preferences",
        "hmc_processed_metrics",
        "hmc_processed_metric_links",
        "hmc_aggregated_metrics",
        "hmc_aggregated_metric_links",
        "hmc_list_users",
        "hmc_get_user",
        "hmc_list_password_policies",
        "hmc_list_password_policy_status",
        "hmc_get_ldap_config",
        "hmc_get_available_hmc_ptfs",
        "hmc_list_vios_backups",
        "hmc_get_lpar_description",
        "hmc_get_lpar_msp",
        "hmc_get_proc_compat_modes",
        "hmc_get_lpar_proc_compat",
        "hmc_list_io_slots",
        "hmc_list_memory_pools",
        "hmc_list_vnics",
        "hmc_get_media_repository",
        "hmc_list_optical_media",
        "hmc_list_optical_mappings",
        "hmc_list_storage_mappings",
    }
)

LEGACY_DESTRUCTIVE = frozenset(
    {
        "hmc_power_off_lpar",
        "hmc_delete_lpar",
        "hmc_decommission_lpar",
        "hmc_delete_vios",
        "hmc_delete_adapter",
        "hmc_delete_virtual_network",
        "hmc_delete_media_repository",
        "hmc_delete_optical_media",
        "hmc_delete_virtual_disk",
        "hmc_delete_logical_unit",
        "hmc_delete_user",
        "hmc_delete_password_policy",
        "hmc_remove_ldap_config",
        "hmc_remove_memory_pool",
        "hmc_remove_vnic",
        "hmc_power_off_system",
        "hmc_power_off_vios",
        "hmc_migrate_abort_lpar",
        "hmc_remote_restart_lpar",
        "hmc_restore_vios",
        "hmc_restore_lpar_profiles",
        "hmc_backup_lpar_profiles",
        "hmc_sync_lpar_profile",
        "hmc_unmount_optical_media",
        "hmc_detach_optical_mapping",
        "hmc_detach_storage_mapping",
    }
)


def _tools_by_name(enable_arbitrary_command: bool = False):
    asyncio.run(
        server_command.configure_arbitrary_command_tool(enable_arbitrary_command, mcp)
    )
    try:
        return {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    finally:
        if enable_arbitrary_command:
            asyncio.run(server_command.configure_arbitrary_command_tool(False, mcp))


def test_every_live_tool_has_security_metadata():
    """G1: no live tool escapes the classification, toggle on or off."""
    assert set(_tools_by_name()) <= set(TOOL_SECURITY)
    assert set(_tools_by_name(True)) == set(TOOL_SECURITY)
    assert "hmc_run_command" in TOOL_SECURITY


def test_annotations_are_derived_from_the_effect_class():
    """G4: the shipped hint is a function of the declared effect, nothing else."""
    for name, tool in _tools_by_name(True).items():
        assert tool.annotations == annotations_for(TOOL_SECURITY[name].effect), name


def test_declared_effects_use_the_closed_vocabulary():
    for name, security in TOOL_SECURITY.items():
        assert security.effect in EFFECTS, name


def test_selectors_and_connection_arguments_are_public_parameters():
    """G3: every declared selector is really an argument a caller supplies."""
    for name, tool in _tools_by_name(True).items():
        security = TOOL_SECURITY[name]
        properties = set(tool.parameters.get("properties", {}))
        for target in security.targets:
            assert target.argument in properties, (name, target.argument)
        if security.connection_argument is not None:
            assert security.connection_argument in properties, name


@pytest.mark.parametrize(
    "tool_name, argument, expected_required",
    [
        ("hmc_power_off_lpar", "system_name_or_uuid", False),
        ("hmc_power_off_vios", "system_name_or_uuid", False),
        ("hmc_delete_vios", "system_name_or_uuid", False),
        ("hmc_restore_vios", "system_name_or_uuid", False),
        ("hmc_list_lpars", "system_name_or_uuid", False),
        ("hmc_delete_lpar", "lpar_name_or_uuid", True),
        ("hmc_migrate_lpar", "target_system_name_or_uuid", True),
    ],
)
def test_optional_selectors_are_marked_optional(tool_name, argument, expected_required):
    """G3 anchors: #223 must be able to tell an omitted selector from a required one."""
    selector = next(
        target
        for target in TOOL_SECURITY[tool_name].targets
        if target.argument == argument
    )
    assert selector.required is expected_required


def test_multi_kind_tools_declare_every_target():
    """G6: the omission the argument table exists to make impossible."""
    migrate = {(t.kind, t.argument) for t in TOOL_SECURITY["hmc_migrate_lpar"].targets}
    assert ("lpar", "lpar_name_or_uuid") in migrate
    assert ("managed_system", "target_system_name_or_uuid") in migrate

    attach = {
        (t.kind, t.argument) for t in TOOL_SECURITY["hmc_attach_disk_to_lpar"].targets
    }
    assert ("lpar", "lpar_name_or_uuid") in attach
    assert ("vios", "vios_uuid") in attach


def test_target_declarations_are_internally_consistent():
    """G6: V7 and V8 hold for every record in the index.

    tool() validates what it collects, so this restates those rules for records
    that reach TOOL_SECURITY another way — today the hand-built escape-hatch
    constant, tomorrow anything registered outside the collector.
    """
    for name in _tools_by_name(True):
        security = TOOL_SECURITY[name]
        if security.target_kind == "none":
            assert security.targets == (), name
            assert security.connection_argument is None, name
            continue
        if security.target_kind != "console":
            assert any(t.kind == security.target_kind for t in security.targets), name
        arguments = [t.argument for t in security.targets]
        assert len(arguments) == len(set(arguments)), name


# Written independently of tool_registry.REQUIRED_TARGET_ARGUMENTS. Deriving the
# expectation from that table would make the coverage test tautological: deleting
# a row would silently strip every selector it produced and still pass.
EXPECTED_TARGET_ARGUMENTS = {
    "lpar_name_or_uuid": "lpar",
    "lpar_uuid": "lpar",
    "system_name_or_uuid": "managed_system",
    "target_system_name_or_uuid": "managed_system",
    "vios_name_or_uuid": "vios",
    "vios_uuid": "vios",
    "vios_partition_id": "vios",
    "cluster_uuid": "cluster",
    "ssp_uuid": "shared_storage_pool",
    "console_uuid": "console",
    "job_uuid": "job",
    "template_uuid": "template",
    "draft_template_uuid": "template",
    "policy_name": "password_policy",
    "resource_name_or_uuid": "metric_resource",
}


def test_the_argument_table_matches_its_independent_expectation():
    """G6: a deleted table row silently strips selectors; pin the table itself."""
    assert dict(REQUIRED_TARGET_ARGUMENTS) == EXPECTED_TARGET_ARGUMENTS


@pytest.mark.parametrize(
    "tool_name, expected",
    [
        ("hmc_get_available_hmc_ptfs", {("console", "console_uuid")}),
        ("hmc_update_console_software", {("console", "console_uuid")}),
        ("hmc_get_job", {("job", "job_uuid")}),
        ("hmc_get_partition_template", {("template", "template_uuid")}),
        ("hmc_get_shared_storage_pool", {("shared_storage_pool", "ssp_uuid")}),
        ("hmc_delete_password_policy", {("password_policy", "policy_name")}),
        ("hmc_processed_metrics", {("metric_resource", "resource_name_or_uuid")}),
        ("hmc_create_logical_unit", {("cluster", "cluster_uuid")}),
        (
            "hmc_add_vscsi_adapter",
            {("lpar", "lpar_name_or_uuid"), ("vios", "vios_partition_id")},
        ),
        ("hmc_delete_user", {("user", "name")}),
    ],
)
def test_selectors_are_built_for_every_table_kind(tool_name, expected):
    """G6: one live tool per table kind, pinned literally."""
    built = {(t.kind, t.argument) for t in TOOL_SECURITY[tool_name].targets}
    assert expected <= built


def test_every_table_argument_becomes_a_target():
    """G6: a handler taking an identity argument cannot silently drop it."""
    for name, tool in _tools_by_name(True).items():
        declared = {t.argument for t in TOOL_SECURITY[name].targets}
        expected = set(tool.parameters.get("properties", {})) & set(
            REQUIRED_TARGET_ARGUMENTS
        )
        assert expected <= declared, (name, sorted(expected - declared))


def test_operation_identities_are_unique():
    """G2: two tools may not claim one operation."""
    operations = [security.operation for security in TOOL_SECURITY.values()]
    assert len(operations) == len(set(operations))


DESTRUCTIVE_NAME_PREFIXES = (
    "hmc_delete_",
    "hmc_remove_",
    "hmc_power_off_",
    "hmc_restore_",
    "hmc_detach_",
    "hmc_unmount_",
    "hmc_sync_",
    "hmc_decommission_",
    "hmc_install_",
)

# Destructive tools whose names carry no such prefix. Listed so the matched set
# below can be asserted exactly, which names the offending tool on failure
# instead of reporting a count that moved.
DESTRUCTIVE_WITHOUT_PREFIX = frozenset(
    {
        "hmc_backup_lpar_profiles",
        "hmc_migrate_abort_lpar",
        "hmc_remote_restart_lpar",
    }
)


def test_destructively_named_tools_are_destructive():
    """G7: defence in depth against the likeliest misclassification.

    A heuristic, not a charter criterion: a tool deliberately named against the
    convention is a discussion, not a gate failure. It cannot catch a new tool
    named outside these prefixes, which is why the declared effect stays a
    reviewed human judgement.
    """
    matched = {n for n in TOOL_SECURITY if n.startswith(DESTRUCTIVE_NAME_PREFIXES)}
    misclassified = {n for n in matched if TOOL_SECURITY[n].effect != "destructive"}
    assert misclassified == set()

    declared = {n for n, s in TOOL_SECURITY.items() if s.effect == "destructive"}
    assert declared == matched | DESTRUCTIVE_WITHOUT_PREFIX


def test_arbitrary_command_is_absent_by_default_and_maximally_classified():
    """G8: the escape hatch is off by default and is the only arbitrary command."""
    default = _tools_by_name()
    assert "hmc_run_command" not in default
    assert not [n for n in default if TOOL_SECURITY[n].effect == "arbitrary-command"]

    enabled = _tools_by_name(True)
    assert [n for n in enabled if TOOL_SECURITY[n].effect == "arbitrary-command"] == [
        "hmc_run_command"
    ]
    validate_security(
        server_command.HMC_RUN_COMMAND_SECURITY, server_command.hmc_run_command
    )


def test_only_local_tools_open_no_hmc_connection():
    """G10: every tool that reaches an HMC declares how its connection is chosen."""
    local_only = {"hmc_list_configured_hosts", "hmc_effective_permissions"}
    for name, security in TOOL_SECURITY.items():
        if name in local_only:
            assert security.target_kind == "none", name
            assert security.connection_argument is None, name
        else:
            assert security.connection_argument == "profile", name


def test_no_classification_regresses_against_the_pre_adr_sets():
    """G11: a permutation-proof pin on the classification this change inherited."""
    for name in LEGACY_READ_ONLY:
        assert TOOL_SECURITY[name].effect == "read", name
    for name in LEGACY_DESTRUCTIVE:
        assert TOOL_SECURITY[name].effect == "destructive", name
    assert TOOL_SECURITY["hmc_read_lpar_boot_order"].effect == "read"


def test_the_derivation_tables_are_read_only():
    """The two tables producing every hint and every selector must not be edited."""
    from hmc_mcp import tool_registry

    with pytest.raises(TypeError):
        tool_registry.REQUIRED_TARGET_ARGUMENTS["lpar_name_or_uuid"] = "vios"
    with pytest.raises(TypeError):
        tool_registry._ANNOTATIONS["read"] = (False, True)
    with pytest.raises(TypeError):
        tool_registry._ANNOTATIONS["read"][0] = False


def test_annotations_for_hands_out_an_independent_copy():
    """A shared instance would let one in-place edit re-flag a whole effect class."""
    first = annotations_for("read")
    second = annotations_for("read")
    assert first == second
    assert first is not second

    first.readOnlyHint = False
    assert annotations_for("read").readOnlyHint is True


def test_the_classification_index_is_read_only():
    """A record any policy layer reads must not be replaceable at runtime."""
    with pytest.raises(TypeError):
        TOOL_SECURITY["hmc_delete_lpar"] = TOOL_SECURITY["hmc_get_lpar"]


def test_legacy_classification_sets_are_gone():
    """G9: replace, don't deprecate."""
    from hmc_mcp import _app, server

    for module in (_app, server):
        for removed in (
            "READ_ONLY_TOOLS",
            "DESTRUCTIVE_TOOLS",
            "_READ_ONLY",
            "_DESTRUCTIVE",
            "_STATE_CHANGING",
        ):
            assert not hasattr(module, removed), f"{module.__name__}.{removed}"


# A handler's connection routes through exactly these three helpers. Every one
# of them resolves an HMCConfig from `common.build_config`, so a call that omits
# the handler's declared connection argument reaches the deployment default
# whatever the caller — and the access policy — named.
_CONNECTION_BUILDERS = frozenset(
    {"build_config", "client_from_env", "_ssh_with_client"}
)

# `host` is deliberately singled out: `build_config` skips the whole profile
# branch when an explicit host is given, exactly as HMC_HOST does, so a handler
# passing one would route around ADR 0038's normalization entirely.
_CONNECTION_OVERRIDES = frozenset({"host"})

_Def = ast.FunctionDef | ast.AsyncFunctionDef


_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def _call_name(call: ast.Call) -> str | None:
    """The called name, for builder matching."""
    return getattr(call.func, "id", None) or getattr(call.func, "attr", None)


def _module_functions(tree: ast.Module) -> dict[str, _Def]:
    """Every top-level function in a module, keyed by name.

    Every one of them, not only the ``_``-prefixed ones: a public same-module
    helper that drops the selector is exactly as fail-open as a private one. No
    handler in ``src/`` routes that way today, so the synthetic
    ``dropped-by-a-public-helper`` case is what keeps this from silently
    narrowing back.
    """
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _own_scope(function: _Def | ast.Lambda):
    """Every node in *function*'s own scope, stopping at a nested one.

    A nested ``def`` or ``lambda`` is a separate frame: its parameters may shadow
    the selector, and a name bound inside it is not a rebinding of the enclosing
    one. Walking through them made both the routing check and the rebind check
    answer about the wrong binding.
    """
    body = function.body if isinstance(function.body, list) else [function.body]
    stack: list[ast.AST] = [function.args, *body]
    # Decorators and a return annotation belong to the enclosing frame and are
    # evaluated at import, so they cannot carry a per-call selector — but a
    # connection opened there is still a connection nothing authorized.
    stack.extend(getattr(function, "decorator_list", []))
    if getattr(function, "returns", None) is not None:
        stack.append(function.returns)
    while stack:
        node = stack.pop()
        yield node
        # A nested frame is yielded so the caller can recurse into it as its own
        # scope, but never descended into from here.
        if not isinstance(node, _SCOPES):
            stack.extend(ast.iter_child_nodes(node))


def _nested_selector(function: _Def | ast.Lambda, argument: str) -> str | None:
    """What *argument* is called inside *function*, or None when it is shadowed.

    A nested frame closes over the enclosing selector unless its own parameter
    list rebinds the name — except when that parameter's *default* is the
    selector, which is how ``def _go(profile=profile)`` and its rename sibling
    ``def _go(chosen=profile)`` carry the value in rather than shadow it.
    """
    arguments = function.args
    positional = [*arguments.posonlyargs, *arguments.args]
    defaults = [None] * (len(positional) - len(arguments.defaults))
    defaults += list(arguments.defaults)
    pairs = [
        *zip(positional, defaults),
        *zip(arguments.kwonlyargs, arguments.kw_defaults),
        *[(star, None) for star in (arguments.vararg, arguments.kwarg) if star],
    ]
    for parameter, default in pairs:
        if isinstance(default, ast.Name) and default.id == argument:
            return parameter.arg
    return None if any(parameter.arg == argument for parameter, _ in pairs) else argument


def _bound_names(function: _Def | ast.Lambda) -> set[str]:
    """Every name *function*'s own scope binds after its parameters.

    Store-context ``Name`` nodes cover assignment, augmented assignment,
    annotated assignment, ``for``, ``with ... as``, walrus, and tuple unpacking,
    without the false positives a target-expression walk produced for
    ``results[profile] = ...`` and ``profile.cached = 1``. The four forms that
    bind without a Store-context ``Name`` are added explicitly.
    """
    names: set[str] = set()
    for node in _own_scope(function):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.comprehension):
            names |= {
                child.id
                for child in ast.walk(node.target)
                if isinstance(child, ast.Name)
            }
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.alias) and node.asname:
            names.add(node.asname)
        elif isinstance(node, ast.MatchAs) and node.name:
            names.add(node.name)
    return names


def _bound_parameter(helper: _Def, call: ast.Call, argument: str) -> str | None:
    """The parameter *helper* binds the caller's connection argument to, if any."""
    for keyword in call.keywords:
        if isinstance(keyword.value, ast.Name) and keyword.value.id == argument:
            return keyword.arg
    positional = [
        parameter.arg
        for parameter in [*helper.args.posonlyargs, *helper.args.args]
    ]
    for index, node in enumerate(call.args):
        if (
            isinstance(node, ast.Name)
            and node.id == argument
            and index < len(positional)
        ):
            return positional[index]
    return None


def _assert_builder_call(call: ast.Call, argument: str | None, where: str) -> None:
    """Refuse a connection builder that does not receive the authorized value."""
    # A splat hides its keys from the parser, so the override check below could
    # not see a `host` travelling inside one. Refuse the shape.
    assert all(keyword.arg is not None for keyword in call.keywords), (
        f"{where} splats a mapping into a connection builder, which this check "
        "cannot read; pass the connection argument explicitly"
    )
    # The *value* must be the declared argument, wherever it is supplied.
    # `client_from_env(profile='some-other-profile')` routes somewhere the
    # authorization never decided about, and the keyword arm is the only one
    # available to the SSH family, whose `profile` is keyword-only on
    # `_app._ssh_with_client`. The keyword's own name is not constrained: a tool
    # declaring `connection_argument="connection"` writes `profile=connection`.
    supplied = [*call.args, *(keyword.value for keyword in call.keywords)]
    assert argument is not None and any(
        isinstance(node, ast.Name) and node.id == argument for node in supplied
    ), (
        f"{where} does not receive the value of the declared connection argument, "
        "so it routes to a connection the access policy did not authorize"
    )
    assert not {keyword.arg for keyword in call.keywords} & _CONNECTION_OVERRIDES, (
        f"{where} passes a connection override that skips profile resolution"
    )


def _assert_routes(
    function: _Def | ast.Lambda,
    argument: str | None,
    helpers: dict[str, _Def],
    tool: str,
    seen: set[str],
) -> int:
    """Check *function*'s connection builders, following same-module helpers.

    *argument* is the name currently holding the caller's connection selector, or
    None when the call chain did not pass one — in which case any connection
    builder below is routing to the deployment default and fails.

    Returns the number of connection builders reached, so a handler that reaches
    none at all is caught by the caller rather than passing vacuously.
    """
    name_of = getattr(function, "name", "<lambda>")
    if argument is not None:
        assert argument not in _bound_names(function), (
            f"{tool}: {name_of} rebinds {argument!r}; the connection the access "
            "policy authorized is the one the handler must use"
        )
    reached = 0
    for node in _own_scope(function):
        if isinstance(node, _SCOPES):
            # A nested frame: its parameters shadow the enclosing selector, so a
            # builder inside one that redeclares the name gets no selector at all.
            reached += _assert_routes(
                node,
                _nested_selector(node, argument) if argument is not None else None,
                helpers,
                tool,
                seen,
            )
        elif isinstance(node, ast.Call):
            called = _call_name(node)
            if called in _CONNECTION_BUILDERS:
                reached += 1
                _assert_builder_call(
                    node, argument, f"{tool}: {called}() at {name_of}:{node.lineno}"
                )
            elif (
                isinstance(node.func, ast.Name)
                and called in helpers
                and called not in seen
            ):
                helper = helpers[called]
                reached += _assert_routes(
                    helper,
                    _bound_parameter(helper, node, argument)
                    if argument is not None
                    else None,
                    helpers,
                    tool,
                    seen | {called},
                )
    return reached


def _assert_handler_routes(
    function: _Def, argument: str | None, helpers: dict[str, _Def], tool: str
) -> int:
    """Check one handler end to end, including that it opens a connection at all.

    A handler declaring *no* connection argument needs no second rule: it enters
    the walk with ``argument=None``, so the first connection builder it reaches
    is refused for receiving nothing — which is the correct verdict, since a
    connection nothing selects is a connection no access policy can scope.
    """
    reached = _assert_routes(function, argument, helpers, tool, {tool})
    if argument is not None:
        assert reached, f"{tool}: declares {argument!r} but opens no HMC connection"
    return reached


def test_every_handler_routes_the_connection_argument_it_declares():
    """G12: a declared connection argument is used, not merely accepted.

    Authorization decides on the value of ``connection_argument``; a handler that
    then resolves its client without it makes the decision be about a connection
    the call does not make. That is a fail-open, and it is what
    ``hmc_set_lpar_boot_order`` and ``hmc_clear_lpar_boot_order`` did before #222.

    The check is static and follows same-module helpers down the call chain,
    which is how the metrics, vios, and composite tools reach their client. It
    does not follow a helper imported from another module, a ``functools.partial``,
    or a callable held in a variable; ADR 0038 records that residual.

    The two tools that declare *no* connection argument are checked in the same
    pass, from the other side: entering the walk with no selector, the first
    builder either of them reached would be refused. Nothing else would notice
    one growing a `client_from_env()` call, since neither `validate_security`
    nor the dispatch wrapper reads a handler's body.
    """
    root = Path(server_command.__file__).parent
    checked: set[str] = set()

    for path in sorted(root.glob("server_*.py")):
        functions = _module_functions(ast.parse(path.read_text(encoding="utf-8")))
        for name in sorted(functions.keys() & set(TOOL_SECURITY)):
            _assert_handler_routes(
                functions[name],
                TOOL_SECURITY[name].connection_argument,
                functions,
                name,
            )
            # A handler that builds its own HMCConfig hands it straight to
            # HMCClient, reaching an HMC through no builder this walk knows.
            # None does today; refusing the construction keeps the set closed.
            assert "HMCConfig" not in {
                _call_name(node)
                for node in ast.walk(functions[name])
                if isinstance(node, ast.Call)
            }, f"{name}: constructs its own HMCConfig, bypassing profile resolution"
            checked.add(name)

    # `hmc_effective_permissions` is defined inside a factory rather than at
    # module level, so it is the one name this pass cannot reach; every other
    # tool, including the two that declare no connection argument, is checked.
    assert set(TOOL_SECURITY) - checked == {"hmc_effective_permissions"}


def _walk(source: str, argument: str | None = "profile") -> int:
    """Run the G12 walk over a synthetic module, as it runs over a real one."""
    functions = _module_functions(ast.parse(source))
    return _assert_handler_routes(
        functions["hmc_probe"], argument, functions, "hmc_probe"
    )


# Each source is a shape no handler in `src/` exhibits, so without these the
# corresponding assertion in `_assert_routes` could be deleted and the suite
# would stay green — which is exactly what happened to three of them.
_REFUSED = {
    "no-argument-at-all": (
        "does not receive the value",
        """
def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    return client_from_env()
""",
    ),
    "hardcoded-keyword": (
        "does not receive the value",
        """
def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    return client_from_env(profile="prod")
""",
    ),
    "another-parameter-as-the-keyword": (
        "does not receive the value",
        """
def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    return client_from_env(profile=system_name_or_uuid)
""",
    ),
    "mapping-splat": (
        "splats a mapping",
        """
def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    return client_from_env(profile, **{"host": "attacker"})
""",
    ),
    "host-override": (
        "connection override",
        """
def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    return client_from_env(profile, host="attacker")
""",
    ),
    "rebound-to-a-literal": (
        "rebinds 'profile'",
        """
def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    profile = "prod"
    return client_from_env(profile)
""",
    ),
    "rebound-in-a-loop": (
        "rebinds 'profile'",
        """
def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    for profile in ["prod"]:
        pass
    return client_from_env(profile)
""",
    ),
    "rebound-inside-a-helper": (
        "rebinds 'chosen'",
        """
def open_client(chosen=None):
    chosen = "prod"
    return client_from_env(chosen)


def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    return open_client(profile)
""",
    ),
    "dropped-by-a-public-helper": (
        "does not receive the value",
        """
def open_client(chosen=None):
    return client_from_env()


def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    return open_client(profile)
""",
    ),
    "shadowed-by-a-nested-parameter": (
        "does not receive the value",
        """
def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    async def _go(profile=None):
        async with client_from_env(profile) as hmc:
            return hmc

    return _run(_go)
""",
    ),
    "rebound-in-a-comprehension": (
        "rebinds 'profile'",
        """
def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    return [client_from_env(profile) for profile in ["prod"]]
""",
    ),
    "rebound-by-an-except-clause": (
        "rebinds 'profile'",
        """
def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    try:
        pass
    except ValueError as profile:
        pass
    return client_from_env(profile)
""",
    ),
    "rebound-by-an-import-alias": (
        "rebinds 'profile'",
        """
def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    import os as profile

    return client_from_env(profile)
""",
    ),
    "rebound-by-a-walrus": (
        "rebinds 'profile'",
        """
def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    if (profile := "prod"):
        return client_from_env(profile)
    return None
""",
    ),
    "rebound-by-tuple-unpacking": (
        "rebinds 'profile'",
        """
def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    profile, _ = ("prod", 1)
    return client_from_env(profile)
""",
    ),
    "rebound-by-a-with-clause": (
        "rebinds 'profile'",
        """
def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    with open("x") as profile:
        return client_from_env(profile)
""",
    ),
    "connection-opened-in-a-decorator": (
        "does not receive the value",
        """
def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    @_wrap(client_from_env())
    def _go():
        return None

    return client_from_env(profile)
""",
    ),
    "dropped-at-the-helper-hop": (
        "does not receive the value",
        """
def open_client(chosen=None):
    return client_from_env(chosen)


def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    return open_client()
""",
    ),
}

_ACCEPTED = {
    "direct-positional": """
def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    return client_from_env(profile)
""",
    "direct-keyword": """
def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    return build_config(profile=profile)
""",
    "keyword-only-hop": """
def _open(system, *, chosen=None):
    return client_from_env(chosen)


def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    return _open(system_name_or_uuid, chosen=profile)
""",
    "positional-only-hop": """
def open_client(chosen=None, /):
    return client_from_env(chosen)


def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    return open_client(profile)
""",
    "renamed-across-two-hops": """
def _outer(picked=None):
    return _inner(picked)


def _inner(final=None):
    return client_from_env(final)


def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    return _outer(profile)
""",
    "subscript-target-is-not-a-rebind": """
def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    results = {}
    results[profile] = client_from_env(profile)
    return results
""",
    "attribute-target-is-not-a-rebind": """
def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    client = client_from_env(profile)
    client.chosen = profile
    return client
""",
    "a-nested-helper-may-use-the-name-for-its-own-parameter": """
def _fmt(profile):
    profile = profile.upper()
    return profile


def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    return client_from_env(profile)
""",
    "a-differently-named-selector": """
def hmc_probe(system_name_or_uuid: str, connection: str | None = None):
    return client_from_env(profile=connection)
""",
    "nested-parameter-carrying-the-selector": """
def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    async def _go(profile=profile):
        async with client_from_env(profile) as hmc:
            return hmc

    return _run(_go)
""",
    "nested-parameter-renaming-the-selector": """
def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    async def _go(chosen=profile):
        async with client_from_env(chosen) as hmc:
            return hmc

    return _run(_go)
""",
    "nested-async-body": """
def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    async def _go():
        async with client_from_env(profile) as hmc:
            return hmc

    return _run(_go)
""",
}


@pytest.mark.parametrize(("expected", "source"), _REFUSED.values(), ids=_REFUSED)
def test_the_routing_guardrail_refuses_every_unrouted_shape(expected, source):
    """Each assertion in the G12 walk, pinned against a synthetic handler.

    No handler in ``src/`` exhibits any of these, so this is the only thing
    standing between the guardrail and a silent regression.
    """
    with pytest.raises(AssertionError, match=expected):
        _walk(source)


@pytest.mark.parametrize("source", _ACCEPTED.values(), ids=_ACCEPTED)
def test_the_routing_guardrail_accepts_every_correct_shape(source):
    """The other half: a guard that refused these would fail on correct code."""
    argument = "connection" if "connection: str" in source else "profile"
    assert _walk(source, argument) == 1


def test_the_guardrail_refuses_a_handler_that_opens_no_connection():
    """A handler that declares a selector and never connects passes vacuously."""
    with pytest.raises(AssertionError, match="opens no HMC connection"):
        _walk(
            """
def hmc_probe(system_name_or_uuid: str, profile: str | None = None):
    return {}
"""
        )


def test_the_guardrail_refuses_a_connectionless_handler_that_connects():
    """The other direction: no declared selector means no connection may be opened.

    Neither `validate_security` nor the dispatch wrapper reads a handler's body,
    so this is the only thing that would notice `hmc_list_configured_hosts`
    growing an HMC call.
    """
    with pytest.raises(AssertionError, match="does not receive the value"):
        _walk(
            """
def hmc_probe(system_name_or_uuid: str):
    return client_from_env()
""",
            None,
        )
