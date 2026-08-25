"""Contract tests for the supported reusable Python API."""

from __future__ import annotations

import ast
import functools
import hashlib
from importlib import import_module
import inspect
import json
from pathlib import Path
import pkgutil
import re
import subprocess
import sys
from collections.abc import Callable
from types import ModuleType
from typing import get_args, get_origin, get_type_hints

import hmc_mcp
from hmc_mcp import api
from hmc_mcp.client_contracts import PcmClient
from hmc_mcp.client_templates import TemplatesMixin

# ADR 0029's Decision section selects, from each ``operations_*`` module, "every non-underscore
# top-level coroutine function the module itself defines, and each package-owned input, result,
# enum, or literal-alias type appearing in a selected function's public signature". Both halves
# are keyed by ``(defining module, name)``: two ``operations_*`` modules may define the same
# public name, and a bare-name key would let exporting either one satisfy both entries.
#
# A selected name may stay out of ``api.__all__`` only with a recorded justification that cites
# the ADR text excluding it — ``_ADR_CITATION`` enforces the citation, so "internal" is not an
# acceptable excuse. The tests below also reject entries that no longer describe a real omission,
# so neither mapping can silently accumulate dead excuses.
ADR_0029_OPERATION_EXCLUSIONS: dict[tuple[str, str], str] = {}
ADR_0029_TYPE_EXCLUSIONS: dict[tuple[str, str], str] = {}

_ADR_CITATION = re.compile(r"ADR 0029|docs/adr/0029")

_ADR_0029_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs/adr/0029-supported-reusable-python-api-contract.md"
)
_INVENTORY_BEGIN = "<!-- ADR-0029-INVENTORY:BEGIN -->"
_INVENTORY_END = "<!-- ADR-0029-INVENTORY:END -->"
_INVENTORY_ENTRY = re.compile(r"^- `([a-z_][a-z0-9_]*)` — (.+)$")
_INVENTORY_CLAUSE = re.compile(
    r"^(operations|types|excluded synchronous|exports): (.+)$"
)
_INVENTORY_NAME = re.compile(r"^`([A-Za-z_][A-Za-z0-9_]*)`$")


# ADR 0029: HMCClient's supported surface is exactly this allowlist. Inherited
# mixin methods stay callable but are unsupported, so no contract gate covers
# them.
SUPPORTED_CLIENT_LIFECYCLE = frozenset(
    {"__init__", "__aenter__", "__aexit__", "is_logged_on", "logon", "logoff"}
)


def test_public_api_exports_the_adr_inventory() -> None:
    assert api.__all__ == [
        "HMCClient",
        "AffinityAssessmentInput",
        "AffinityAssessmentResult",
        "AffinityEvidence",
        "CapturedPolicyState",
        "PolicyState",
        "HMCConfig",
        "ConfigError",
        "load_profile",
        "HMCError",
        "HMCTransportError",
        "HMCCLIError",
        "list_adapters",
        "add_network_adapter",
        "add_vios_adapter",
        "delete_adapter",
        "AdapterResult",
        "AdapterType",
        "capacity_report",
        "find_placement",
        "lpar_summary",
        "system_summary",
        "decommission_lpar",
        "DecommissionResult",
        "fleet_health",
        "FleetHealthResult",
        "install_lpar_os",
        "install_vios",
        "assess_post_activation_affinity",
        "authorize_decommission_lpar_ownership_snapshot",
        "authorize_lpar_mutation",
        "resolve_lpar_ownership_names",
        "list_lpar_ownership",
        "stamp_created_lpar_ownership",
        "create_and_stamp_lpar",
        "set_lpar_ownership_description",
        "delete_lpar",
        "power_lpar",
        "rename_lpar",
        "set_lpar_processors",
        "set_lpar_memory",
        "LparCreation",
        "LparCreationResult",
        "LparPowerResult",
        "read_lpar_boot_order",
        "set_lpar_boot_order",
        "clear_lpar_boot_order",
        "BootDeviceSelector",
        "migrate_lpar",
        "migrate_lpar_with_affinity_preflight",
        "run_lpm_affinity_preflight",
        "abort_lpar_migration",
        "recover_lpar_migration",
        "remote_restart_lpar",
        "LpmResult",
        "LpmAffinityPreflightRequest",
        "LpmAffinityPreflightOutcome",
        "LpmAffinityMigrationResult",
        "list_virtual_switches",
        "list_virtual_networks",
        "create_virtual_network",
        "delete_virtual_network",
        "list_network_bridges",
        "resolve_pcm_resource",
        "get_pcm_preferences",
        "set_pcm_preferences",
        "metric_links",
        "metric_data",
        "PcmCategory",
        "MetricKind",
        "PcmResource",
        "DedicatedSlot",
        "InventoryResult",
        "InventorySelector",
        "PcieAssignmentUnavailableError",
        "SriovAdapter",
        "SriovLogicalPort",
        "SriovPhysicalPort",
        "assign_dedicated_pcie_slot",
        "list_dedicated_slots",
        "list_sriov_adapters",
        "list_sriov_logical_ports",
        "list_sriov_physical_ports",
        "unassign_dedicated_pcie_slot",
        "SriovLogicalPortCapabilityError",
        "SriovLogicalPortChangeResult",
        "SriovLogicalPortPartialError",
        "SriovLogicalPortSnapshot",
        "assign_sriov_logical_port",
        "set_sriov_adapter_mode",
        "unassign_sriov_logical_port",
        "attach_disk_to_lpar",
        "provision_lpar",
        "ProvisionAffinityAssessment",
        "ProvisionNetwork",
        "ProvisionStorage",
        "ProvisionResult",
        "AttachDiskResult",
        "LparResources",
        "PartitionType",
        "list_fc_ports",
        "get_lpar_memopt_score",
        "get_minimum_affinity_policy",
        "set_minimum_affinity_policy",
        "get_system_memopt_score",
        "list_lpar_memopt_scores",
        "plan_lpar_memopt_scores",
        "plan_system_memopt_score",
        "MemoptLparSelector",
        "MemoptResourceGroupSelector",
        "ResourceGroupAffinityResult",
        "MinimumAffinityPolicyResult",
        "MinimumAffinityPolicy",
        "list_resource_group_memopt_scores",
        "plan_resource_group_memopt_scores",
        "list_sea_adapters",
        "list_vnics",
        "VnicBackingSelector",
        "VnicBackingSnapshot",
        "VnicSnapshot",
        "VnicChangeResult",
        "VnicCapabilityError",
        "VnicPartialError",
        "add_vnic",
        "remove_vnic",
        "SriovMode",
        "AssignmentResult",
        "AssignmentStep",
        "DedicatedPcieAssignment",
        "LparPcieAssignments",
        "LparPcieWorkflowResult",
        "SriovLogicalPortAssignment",
        "VnicAssignment",
        "apply_lpar_pcie_assignments",
        "prevalidate_lpar_pcie_assignments",
        "list_volume_groups",
        "create_volume_group",
        "create_virtual_disk",
        "delete_virtual_disk",
        "map_storage",
        "upload_iso",
        "create_media_repository",
        "create_optical_media",
        "delete_media_repository",
        "delete_optical_media",
        "get_media_repository",
        "list_optical_media",
        "list_optical_mappings",
        "mount_optical_media",
        "unmount_optical_media",
        "list_storage_mappings",
        "detach_storage_mapping",
        "create_logical_unit",
        "delete_logical_unit",
        "StorageKind",
        "LuType",
        "DeviceType",
        "power_system",
        "list_partition_templates",
        "get_partition_template",
        "deploy_partition_template",
        "power_vios",
        "capture_lpar_console",
        "ConsoleCapture",
        "ConsoleHeldError",
        "LparSnapshot",
        "SnapshotInspection",
        "SnapshotValidationError",
        "capture_lpar_snapshot",
        "assess_snapshot_affinity",
        "inspect_lpar_snapshot",
        "validate_lpar_snapshot",
        "get_job",
        "wait_for_job",
        "JobOutcome",
    ]


def _operations_modules() -> dict[str, ModuleType]:
    """Every ``hmc_mcp.operations_*`` module ADR 0029's selection rule governs."""
    return {
        f"hmc_mcp.{found.name}": import_module(f"hmc_mcp.{found.name}")
        for found in pkgutil.iter_modules(hmc_mcp.__path__)
        if found.name.startswith("operations_")
    }


def _selected_operations(modules: dict[str, ModuleType]) -> set[tuple[str, str]]:
    """Apply ADR 0029's rule mechanically: non-underscore, coroutine, module-owned.

    A coroutine an operation module merely imported is owned by the module that
    defined it, so ``__module__`` decides ownership and no name is selected twice.
    Selection is keyed by ``(module, name)`` rather than by bare name: two
    ``operations_*`` modules defining the same public name are two distinct
    obligations, and a bare-name key would let exporting either one discharge both.
    """
    selected: set[tuple[str, str]] = set()
    for module_name, module in modules.items():
        for name, value in vars(module).items():
            if name.startswith("_") or not inspect.iscoroutinefunction(value):
                continue
            if getattr(value, "__module__", None) == module_name:
                selected.add((module_name, name))
    return selected


def _facade_operations(modules: dict[str, ModuleType]) -> set[tuple[str, str]]:
    """The ``(module, name)`` pairs the facade actually publishes as operations."""
    return {
        (getattr(api, name).__module__, name)
        for name in set(api.__all__)
        if inspect.iscoroutinefunction(getattr(api, name))
        and getattr(getattr(api, name), "__module__", None) in modules
    }


def _selection_faults(
    selected: set[tuple[str, str]],
    exported: set[tuple[str, str]],
    exclusions: dict[tuple[str, str], str],
) -> dict[str, list[str]]:
    """Every way the facade can disagree with one half of ADR 0029's selection rule."""
    unexported = selected - exported
    return {
        "selected but not exported or excluded": sorted(
            map(":".join, unexported - set(exclusions))
        ),
        "excluded but actually exported": sorted(
            map(":".join, set(exclusions) - unexported)
        ),
        "excluded without citing the ADR": sorted(
            ":".join(key)
            for key, reason in exclusions.items()
            if not _ADR_CITATION.search(reason)
        ),
    }


def test_facade_operation_set_matches_adr_0029_selection_rule() -> None:
    modules = _operations_modules()
    selected = _selected_operations(modules)
    facade_operations = _facade_operations(modules)

    faults = _selection_faults(
        selected, facade_operations, ADR_0029_OPERATION_EXCLUSIONS
    )
    assert faults == {key: [] for key in faults}, faults
    assert facade_operations == selected - set(ADR_0029_OPERATION_EXCLUSIONS)


def _collect_owned_types(hint: object, owned: dict[tuple[str, str], object]) -> None:
    """Record every ``hmc_mcp``-owned type reachable from one resolved annotation."""
    origin = get_origin(hint)
    if origin is not None:
        _collect_owned_types(origin, owned)
        for argument in get_args(hint):
            _collect_owned_types(argument, owned)
        return
    module_name = getattr(hint, "__module__", None)
    type_name = getattr(hint, "__name__", None)
    if not isinstance(module_name, str) or not isinstance(type_name, str):
        return
    if module_name == "hmc_mcp" or module_name.startswith("hmc_mcp."):
        owned[(module_name, type_name)] = hint


def _owned_types_in_signatures(
    selected: set[tuple[str, str]], modules: dict[str, ModuleType]
) -> dict[tuple[str, str], object]:
    """The second half of ADR 0029's rule, applied to resolved signatures.

    ``get_type_hints`` erases a literal alias to its value set — ``Literal["a", "b"]``
    carries no ``hmc_mcp`` module — so the literal-alias clause is covered by
    ``test_exported_literal_value_sets_are_frozen`` rather than here. Opaque HMC
    payloads (``dict[str, Any]`` and friends) are excluded by owning no ``hmc_mcp``
    type, which is exactly the distinction ADR 0029's Consequences section draws.
    """
    owned: dict[tuple[str, str], object] = {}
    for module_name, name in selected:
        operation = getattr(modules[module_name], name)
        for hint in get_type_hints(operation).values():
            _collect_owned_types(hint, owned)
    return owned


def test_facade_type_set_matches_adr_0029_selection_rule() -> None:
    """A supported call may not hand back a type with no supported import path.

    The PEP 561 marker (#367) is what makes this consumer-visible: a downstream
    type-checker resolves the real annotation and finds no way to name it.
    """
    modules = _operations_modules()
    owned = _owned_types_in_signatures(_selected_operations(modules), modules)
    exported = {
        key for key, value in owned.items() if getattr(api, key[1], None) is value
    }

    faults = _selection_faults(set(owned), exported, ADR_0029_TYPE_EXCLUSIONS)
    assert faults == {key: [] for key in faults}, faults


_CLEAN_FAULTS = {
    "selected but not exported or excluded": [],
    "excluded but actually exported": [],
    "excluded without citing the ADR": [],
}


def test_adr_0029_selection_rule_rejects_undeclared_operations() -> None:
    """The guard above is only worth its place if it reddens; prove that it does.

    ``ADR_0029_OPERATION_EXCLUSIONS`` is empty, so the real check exercises the
    clean path only. Drive the same helpers with a synthetic operations module.
    """
    module = ModuleType("hmc_mcp.operations_synthetic")

    async def stray_operation(hmc: object) -> None: ...

    async def borrowed_operation(hmc: object) -> None: ...

    async def _private_operation(hmc: object) -> None: ...

    def sync_helper(hmc: object) -> None: ...

    borrowed_operation.__module__ = "hmc_mcp.operations_storage"
    for value in (stray_operation, _private_operation, sync_helper):
        value.__module__ = module.__name__
    for value in (stray_operation, borrowed_operation, _private_operation, sync_helper):
        setattr(module, value.__name__, value)

    stray = ("hmc_mcp.operations_synthetic", "stray_operation")

    # Only the public coroutine the module itself defines is selected.
    selected = _selected_operations({module.__name__: module})
    assert selected == {stray}

    label = ":".join(stray)
    assert _selection_faults(selected, set(), {})[
        "selected but not exported or excluded"
    ] == [label]
    assert _selection_faults(selected, set(), {stray: "internal"})[
        "excluded without citing the ADR"
    ] == [label]
    assert _selection_faults(selected, selected, {stray: "ADR 0029"})[
        "excluded but actually exported"
    ] == [label]
    assert _selection_faults(selected, selected, {}) == _CLEAN_FAULTS
    assert (
        _selection_faults(selected, set(), {stray: "ADR 0029 §x excludes it"})
        == _CLEAN_FAULTS
    )


def test_adr_0029_selection_survives_a_name_two_modules_define() -> None:
    """Keying by ``(module, name)`` is what stops one export discharging two obligations.

    A bare-name key collapses a colliding pair into a single entry, so exporting
    either definition satisfies both and the other leaves the facade silently.
    There is no collision in the package today, which is why this is proven
    synthetically rather than against the real modules.
    """
    first = ModuleType("hmc_mcp.operations_first")
    second = ModuleType("hmc_mcp.operations_second")
    for module in (first, second):

        async def collide(hmc: object) -> None: ...

        collide.__module__ = module.__name__
        module.collide = collide  # type: ignore[attr-defined]

    selected = _selected_operations({m.__name__: m for m in (first, second)})
    assert selected == {
        ("hmc_mcp.operations_first", "collide"),
        ("hmc_mcp.operations_second", "collide"),
    }

    exported_one = {("hmc_mcp.operations_first", "collide")}
    assert _selection_faults(selected, exported_one, {})[
        "selected but not exported or excluded"
    ] == ["hmc_mcp.operations_second:collide"]


def test_adr_0029_type_rule_walks_and_reddens() -> None:
    """Prove the types guard reddens, and that the walk reaches nested annotations.

    Once the facade is correct the real check exercises the clean path only, so the
    fault shapes are driven here against a synthetic owned type.
    """
    owned_key = ("hmc_mcp.operations_synthetic", "SyntheticResult")
    assert _selection_faults({owned_key}, set(), {})[
        "selected but not exported or excluded"
    ] == [":".join(owned_key)]
    assert _selection_faults({owned_key}, {owned_key}, {}) == _CLEAN_FAULTS

    # A type buried in a container and a union is still owned, and an opaque HMC
    # payload mapping contributes nothing.
    pcm_resource = import_module("hmc_mcp.operations_pcm").PcmResource
    collected: dict[tuple[str, str], object] = {}
    _collect_owned_types(list[pcm_resource | None], collected)
    _collect_owned_types(dict[str, object], collected)
    assert collected == {("hmc_mcp.operations_pcm", "PcmResource"): pcm_resource}


def _unselectable_shape(module_name: str, name: str, value: object) -> str | None:
    """Classify a public module attribute ADR 0029's coroutine rule cannot select."""
    if isinstance(value, functools.partial):
        return "functools.partial"
    if inspect.isasyncgenfunction(value):
        return "asynchronous generator"
    if not inspect.iscoroutinefunction(value):
        return None
    owner = getattr(value, "__module__", None)
    if owner == module_name:
        return None
    published = getattr(sys.modules.get(owner or ""), name, None)
    if published is value:
        return None
    return "coroutine its declared module does not publish"


def _unselectable_operation_shapes(
    modules: dict[str, ModuleType],
) -> dict[str, list[str]]:
    """Public operation shapes ADR 0029's mechanical rule cannot see.

    The rule keys on ``inspect.iscoroutinefunction`` plus ``__module__`` ownership, so
    an asynchronous generator, a ``functools.partial``, and an operation built by a
    factory that lives in another module all fall out of the selection with nothing
    noticing. ADR 0029 places all three out of scope, and this turns their arrival into
    a red suite rather than an invisible omission. ``functools.wraps`` is unaffected: it
    copies ``__module__``, so an ordinary decorator preserves ownership.
    """
    faults: dict[str, list[str]] = {
        "functools.partial": [],
        "asynchronous generator": [],
        "coroutine its declared module does not publish": [],
    }
    for module_name, module in modules.items():
        for name, value in vars(module).items():
            if name.startswith("_"):
                continue
            shape = _unselectable_shape(module_name, name, value)
            if shape is not None:
                faults[shape].append(f"{module_name}:{name}")
    return {shape: sorted(names) for shape, names in faults.items()}


def test_operations_modules_define_no_unselectable_operation_shapes() -> None:
    faults = _unselectable_operation_shapes(_operations_modules())
    assert faults == {shape: [] for shape in faults}, (
        "ADR 0029 places these shapes out of the selection rule's scope; adding one "
        f"needs a superseding decision, not a silent omission: {faults}"
    )


def test_unselectable_shape_detector_recognises_each_shape() -> None:
    module = ModuleType("hmc_mcp.operations_shapes")

    async def owned(hmc: object) -> None: ...

    async def elsewhere(hmc: object) -> None: ...

    async def streamer(hmc: object):
        yield hmc

    owned.__module__ = module.__name__
    streamer.__module__ = module.__name__
    # A factory in another module stamps its own ``__module__`` on what it builds, and
    # that module does not publish the result under this name.
    elsewhere.__module__ = "hmc_mcp.operations_storage"
    module.owned = owned  # type: ignore[attr-defined]
    module.streamer = streamer  # type: ignore[attr-defined]
    module.factory_built = elsewhere  # type: ignore[attr-defined]
    module.partial_built = functools.partial(owned)  # type: ignore[attr-defined]
    module._private_stream = streamer  # type: ignore[attr-defined]
    # An ordinary re-export stays clean: the declaring module publishes it by that name.
    module.map_storage = api.map_storage  # type: ignore[attr-defined]

    assert _unselectable_operation_shapes({module.__name__: module}) == {
        "functools.partial": ["hmc_mcp.operations_shapes:partial_built"],
        "asynchronous generator": ["hmc_mcp.operations_shapes:streamer"],
        "coroutine its declared module does not publish": [
            "hmc_mcp.operations_shapes:factory_built"
        ],
    }


def _facade_import_bindings() -> dict[str, str]:
    """Every ``name -> module`` pair ``hmc_mcp/api.py``'s import statements create.

    Reading the facade's own imports keeps source attribution mechanical: unlike the
    hand-written map this replaced, nothing here is maintained alongside ``__all__``
    by the same edit, so it cannot drift silently out of step with it.
    """
    tree = ast.parse(Path(api.__file__).read_text(encoding="utf-8"))
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        module = f"hmc_mcp.{node.module}" if node.level else node.module
        for alias in node.names:
            assert alias.asname is None, f"the facade renames {alias.name} on import"
            assert alias.name not in bindings, f"the facade imports {alias.name} twice"
            bindings[alias.name] = module
    return bindings


def test_public_api_manifest_has_no_repeated_entry() -> None:
    """ADR 0029 calls ``__all__`` "an exhaustive compatibility manifest"; a repeated
    entry makes it malformed. Every other contract test reads ``set(api.__all__)`` or
    iterates it, so a duplicate stays inert at runtime and invisible to all of them —
    ``test_public_api_exports_the_adr_inventory`` froze one in rather than catching it.
    """
    repeated = sorted({name for name in api.__all__ if api.__all__.count(name) > 1})
    assert repeated == [], f"api.__all__ repeats {repeated}"
    assert len(api.__all__) == len(set(api.__all__))


def _inventory_entries() -> dict[str, str]:
    """The clause text of each ADR 0029 inventory bullet, keyed by module.

    Only the fenced region is read. An entry is one bullet whose text is a
    semicolon-separated list of labelled clauses; narrative belongs in an indented
    ``- Note:`` sub-bullet, which closes the entry and is skipped. That shape is what
    lets the document be asserted against the code rather than maintained beside it.
    """
    text = _ADR_0029_PATH.read_text(encoding="utf-8")
    begin, end = text.find(_INVENTORY_BEGIN), text.find(_INVENTORY_END)
    assert 0 <= begin < end, "ADR 0029 has lost its inventory fence markers"

    entries: dict[str, str] = {}
    current: str | None = None
    for line in text[begin:end].splitlines():
        entry = _INVENTORY_ENTRY.match(line)
        if entry is not None:
            current = entry.group(1)
            assert current not in entries, f"ADR 0029 inventories {current} twice"
            entries[current] = entry.group(2)
        elif current is not None and line.startswith("  ") and line[2] != "-":
            entries[current] += " " + line.strip()
        else:
            current = None
    return entries


def _parse_inventory_names(module: str, value: str) -> list[str]:
    names = []
    for token in value.split(", "):
        match = _INVENTORY_NAME.match(token.strip())
        assert match is not None, (
            f"ADR 0029's {module} entry has a stray token {token!r}"
        )
        names.append(match.group(1))
    return names


def _adr_0029_inventory() -> dict[str, dict[str, list[str]]]:
    """ADR 0029's per-module inventory as ``module -> clause -> names``."""
    inventory: dict[str, dict[str, list[str]]] = {}
    for module, text in _inventory_entries().items():
        clauses: dict[str, list[str]] = {}
        for chunk in text.rstrip(".").split("; "):
            match = _INVENTORY_CLAUSE.match(chunk.strip())
            assert match is not None, (
                f"ADR 0029's {module} entry has an unlabelled clause {chunk!r}"
            )
            label, value = match.group(1), match.group(2).strip()
            assert label not in clauses, f"ADR 0029's {module} entry repeats {label}"
            clauses[label] = (
                [] if value == "none" else _parse_inventory_names(module, value)
            )
        inventory[module] = clauses
    return inventory


def _excluded_synchronous(module_name: str, module: ModuleType) -> list[str]:
    """Public synchronous functions a module defines, which ADR 0029 keeps internal."""
    return sorted(
        name
        for name, value in vars(module).items()
        if not name.startswith("_")
        and inspect.isfunction(value)
        and not inspect.iscoroutinefunction(value)
        and getattr(value, "__module__", None) == module_name
    )


def _expected_inventory() -> dict[str, dict[str, list[str]]]:
    """Derive the inventory ADR 0029 must carry from the facade and the modules."""
    modules = _operations_modules()
    selected = _selected_operations(modules)
    bindings = _facade_import_bindings()

    expected: dict[str, dict[str, list[str]]] = {}
    for module_name in {*modules, *bindings.values()}:
        imported = sorted(n for n, m in bindings.items() if m == module_name)
        short = module_name.removeprefix("hmc_mcp.")
        if module_name not in modules:
            expected[short] = {"exports": imported}
            continue
        operations = sorted(n for m, n in selected if m == module_name)
        expected[short] = {
            "operations": operations,
            "types": sorted(set(imported) - set(operations)),
            "excluded synchronous": _excluded_synchronous(
                module_name, modules[module_name]
            ),
        }
    return expected


def test_adr_0029_inventory_matches_the_facade() -> None:
    """The inventory prose is asserted against the code, not maintained beside it.

    Three whole modules were missing from it and several entries had drifted before
    this check existed, because nothing compared the document to the package. Every
    name in ``__all__`` appears in exactly one entry, keyed by the module ``api.py``
    imports it from, so the manifest and the document cannot disagree.
    """
    expected = _expected_inventory()
    assert _adr_0029_inventory() == expected

    inventoried = [
        name
        for clauses in expected.values()
        for clause, names in clauses.items()
        if clause != "excluded synchronous"
        for name in names
    ]
    assert sorted(inventoried) == sorted(api.__all__)
    assert not set(api.__all__).intersection(
        name
        for clauses in expected.values()
        for name in clauses.get("excluded synchronous", ())
    )


def test_public_api_reexports_implementation_objects_directly() -> None:
    bindings = _facade_import_bindings()
    assert set(bindings) == set(api.__all__), (
        f"imported but not in __all__: {sorted(set(bindings) - set(api.__all__))}; "
        f"in __all__ but not imported: {sorted(set(api.__all__) - set(bindings))}"
    )
    for name, module_name in bindings.items():
        assert getattr(api, name) is getattr(import_module(module_name), name), (
            f"api.{name} is not the object {module_name} publishes under that name"
        )


def test_runtime_httpx_annotations_remain_resolvable() -> None:
    assert get_type_hints(PcmClient)["_http"].__module__ == "httpx"
    assert get_type_hints(PcmClient._request)["return"].__module__ == "httpx"
    assert get_type_hints(TemplatesMixin)["_http"].__module__ == "httpx"


def test_public_operations_are_async_and_signatures_are_frozen() -> None:
    """ADR 0029: the supported signatures move only with a recorded decision.

        Last moved by issue #446, which exported `PcmResource` — the frozen
        dataclass `resolve_pcm_resource` returns, and the one type ADR 0029's
        newly mechanised type clause found missing from the manifest. A
        dataclass has a constructor signature, so adding it to `__all__` adds a
        digest entry even though no operation's parameters changed.
        Before that, issue #371 (ADR 0092 §4) moved it twice over: it
        added ``ownership_override`` to ``power_lpar``, and it added the
        ``authorize_power_operations`` field to ``HMCConfig``, whose pydantic
        ``__init__`` signature is derived from its fields.
        Before that, issue #365 extracted the DLPAR processor and
        memory workflows out of the ``hmc_dlpar_proc`` / ``hmc_dlpar_mem``
        tool bodies into ``set_lpar_processors`` and ``set_lpar_memory`` —
        async, guarded per ADR 0092 §3.2, and callable from inside a running
        event loop, which the ``asyncio.run`` tool bodies were not. ADR 0094
        records how each derives the managed system its ownership guard needs
        when the caller omits the optional selector.
        Before that, issue #366 extracted the ``installios`` install
        orchestration out of the MCP tool bodies into ``operations_install``
        and exported ``install_lpar_os`` and ``install_vios``. Both return the
        CLI bridge's detach handle, not an HMC job identifier: ADR 0069 found
        no ``InstallLPAR``/``InstallVIOS`` REST job on any surveyed HMC and
        ADR 0070 replaced them with the detached CLI submission, so that
        addition composes with #364's ``wait_for_job`` nowhere.
        Before that, issue #364 added the cross-process job-polling
        operations ``get_job`` and ``wait_for_job`` and exported the
        ``JobOutcome`` result model (ADR 0093).
        Before that, issue #363 exported four operations ADR 0029's
        selection rule already covered but the manifest omitted: the
        optical-media operations ``list_optical_mappings``,
        ``mount_optical_media``, and ``unmount_optical_media``, which #205
        shipped unexported, and ``assess_post_activation_affinity``, which
        #318 shipped unexported. None of the four ever left ``__all__``; none
        of them entered it.
        Before that, issue #320 added affinity-aware LPM preflight.
        Before that, issue #318 added post-activation affinity assessment.
        Before that, issue #316 added the Power11 minimum-affinity policy write.
        Before that, issue #315 added the Power11 minimum-affinity policy read.
        Before that, issue #312 added capability-aware resource-group affinity.
        Before that, issue #311 added read-only affinity planning operations.
        Before that, issue #310 added the LPAR memory-optimization score operations.
        Before that, issue #400 added the owning-system selector to
        logical-partition PCM metric operations (ADR 0077). Before that, issue
        #401 made the destructive RemoteRestart
        operation and source-system selector explicit (ADR 0078). Before that,
        issue #385 added the ``capture_lpar_console``
    operation, the ``ConsoleCapture`` result model, and the
    ``ConsoleHeldError`` contention error (ADR 0072). Before that, #375
    added the ``list_lpar_ownership`` operation (bulk per-system LPAR
    ownership read; ADR 0071). Before that, ADR 0067 added the
    ``stamp_policy`` field to ``LparCreation`` (issue #377), and before that
    ADR 0066 added ``set_lpar_ownership_description`` (issue #376), and
    before it ADR 0064 added the optional ``caller_token`` parameter to
    ``provision_lpar``. Before that, ADR 0059 changed ``HMCConfig.port``'s
    default from 12443 to 443. ADR 0058 added declarative LPAR PCIe
    assignments, and ADR 0054 added the normalized PCIe inventory models and
    operations. Before that, ADR 0050 added
    ``HMCConfig.iso_url_allowlist`` — a pydantic model's ``__init__``
    signature is derived from its fields, so a new setting moves the digest
    even though no operation's parameters changed. Before that, ADR 0049
    narrowed ``upload_iso``'s ``iso_source`` from ``str | Path`` to ``str``.
    """
    operations = {
        name: getattr(api, name)
        for name in api.__all__
        if inspect.isfunction(getattr(api, name)) and name != "load_profile"
    }
    assert all(
        inspect.iscoroutinefunction(operation) for operation in operations.values()
    )

    signatures = {}
    for name in api.__all__:
        try:
            signatures[name] = str(inspect.signature(getattr(api, name)))
        except (TypeError, ValueError):
            continue
    encoded = json.dumps(signatures, sort_keys=True, separators=(",", ":")).encode()
    # Moved by #446: `PcmResource` enters the manifest and contributes its
    # dataclass constructor. Recomputed over #371's baseline 44e83b7a.
    expected_digest = "6bf67fc584f753f8805f000639221c5eec54954c9939154e7ad83de8293de184"  # pragma: allowlist secret
    assert hashlib.sha256(encoded).hexdigest() == expected_digest


def _bare_annotations(label: str, member: Callable[..., object]) -> list[str]:
    """An omitted ``__init__`` return is not a gap: PEP 484 infers ``None`` for
    a constructor with any annotated argument, and the argument check below is
    what establishes that."""
    try:
        signature = inspect.signature(member)
    except (TypeError, ValueError):
        return []
    bare = [
        f"{label}({parameter.name})"
        for parameter in signature.parameters.values()
        if parameter.name not in {"self", "cls"}
        and parameter.annotation is inspect.Parameter.empty
    ]
    if not label.endswith(".__init__") and (
        signature.return_annotation is inspect.Signature.empty
    ):
        bare.append(f"{label} -> (bare return)")
    return bare


def _contract_callables(name: str, exported: type) -> list[tuple[str, object]]:
    """The members ADR 0029 actually promises for an exported class.

    ``HMCClient`` is the exception: its supported surface is exactly the
    lifecycle allowlist, so the 94 inherited mixin methods the same contract
    calls unsupported are not gated here. For every other exported class a
    member inherited from ``BaseException`` or ``BaseModel`` is not this
    package's to annotate, and its bare ``*args`` is not a facade defect.

    Classmethods are collected as well as plain functions. ``getattr`` on a
    class returns a *bound* method for a classmethod, so ``inspect.isfunction``
    is False for one and an ``isfunction``-only walk would silently skip it —
    which is what happened to ``HMCConfig.from_mapping`` (ADR 0096), a supported
    member this gate is meant to cover. The ``hmc_mcp`` module filter below is
    what keeps the widening tight: pydantic and pydantic-settings contribute
    two dozen inherited classmethods (``model_validate``, ``construct``,
    ``settings_customise_sources``, ...) and every one of them is excluded by it.
    """
    members = [
        (member_name, member)
        for member_name, member in inspect.getmembers(
            exported, lambda m: inspect.isfunction(m) or inspect.ismethod(m)
        )
        if (member_name == "__init__" or not member_name.startswith("_"))
        and getattr(member, "__module__", "").startswith("hmc_mcp")
    ]
    if name != "HMCClient":
        return members
    return [
        (member_name, member)
        for member_name, member in members
        if member_name in SUPPORTED_CLIENT_LIFECYCLE
    ]


def test_every_exported_callable_is_fully_annotated() -> None:
    """The PEP 561 marker asserts the facade is typed. A bare parameter or
    return would make that assertion false for a downstream checker, which is
    worse than shipping no marker at all — the consumer gets silent ``Any``
    where it was promised a type. Covers both halves of the README's claim:
    each export's call signature, and the constructor and public methods of
    each exported package-owned model."""
    bare: list[str] = []
    for name in sorted(api.__all__):
        exported = getattr(api, name)
        if inspect.isfunction(exported):
            bare.extend(_bare_annotations(name, exported))
        elif inspect.isclass(exported):
            for member_name, member in _contract_callables(name, exported):
                bare.extend(_bare_annotations(f"{name}.{member_name}", member))

    assert not bare, f"supported facade members missing an annotation: {bare}"


def test_public_error_hierarchy_is_frozen() -> None:
    assert issubclass(api.HMCTransportError, api.HMCError)
    assert issubclass(api.HMCCLIError, api.HMCError)
    assert issubclass(api.ConfigError, ValueError)


def test_hmc_config_isolated_construction_member_is_supported() -> None:
    """ADR 0096 extends HMCConfig's supported surface by one named member.

    ADR 0029 declares "the fields and constructor of an exported package-owned
    model" supported; ``from_mapping`` is neither, so its presence, its
    signature, and its isolation guarantee are pinned here rather than resting
    on the frozen ``__init__`` digest above — which a classmethod does not move.
    """
    assert "HMCConfig" in api.__all__
    assert str(inspect.signature(api.HMCConfig.from_mapping)) == (
        "(values: 'Mapping[str, Any]') -> 'Self'"
    )

    isolated = api.HMCConfig.from_mapping({"host": "row-host.example.com"})
    assert type(isolated) is api.HMCConfig
    # The guarantee, not the mechanism: no field may be left to a lower-priority
    # settings source. Asserted here because a consumer reads this contract, not
    # the implementation.
    assert set(isolated.model_dump()) == set(api.HMCConfig.model_fields)


def test_hmc_client_supported_lifecycle_members_are_present() -> None:
    assert {
        name for name in SUPPORTED_CLIENT_LIFECYCLE if hasattr(api.HMCClient, name)
    } == SUPPORTED_CLIENT_LIFECYCLE


def test_exported_literal_value_sets_are_frozen() -> None:
    assert get_args(api.AdapterType) == (
        "ClientNetworkAdapter",
        "VirtualSCSIClientAdapter",
        "VirtualFibreChannelClientAdapter",
        "VirtualNICDedicated",
    )
    assert get_args(api.PartitionType) == (
        "AIX/Linux",
        "OS400",
        "Virtual IO Server",
    )
    assert get_args(api.StorageKind) == ("PhysicalVolume", "VirtualDisk")
    assert get_args(api.DeviceType) == ("VirtualIO_Disk", "VirtualIO_Image")
    assert get_args(api.LuType) == ("THIN", "THICK")
    assert get_args(api.MetricKind) == ("processed", "aggregated")
    assert get_args(api.PcmCategory) == ("ManagedSystem", "LogicalPartition")
    assert get_args(api.SriovMode) == ("sriov", "dedicated")


def test_public_signatures_exclude_presentation_types() -> None:
    forbidden = ("typer", "rich", "fastmcp")
    for name in api.__all__:
        value = getattr(api, name)
        try:
            signature = str(inspect.signature(value)).lower().replace("hmc_mcp.", "")
        except (TypeError, ValueError):
            continue
        assert not any(package in signature for package in forbidden), name
        assert re.search(r"(?<![\w-])mcp\.", signature) is None, name


def test_importing_public_api_does_not_import_presentation_modules() -> None:
    script = """
import sys
import hmc_mcp.api

loaded = sorted(
    name for name in sys.modules
    if name.split('.', 1)[0] in {'fastmcp', 'mcp', 'rich', 'typer'}
    or name == 'hmc_mcp._app'
    or name == 'hmc_mcp.cli'
    or name.startswith('hmc_mcp.cli_')
    or name == 'hmc_mcp.server'
    or name.startswith('hmc_mcp.server_')
)
assert loaded == [], loaded
"""
    subprocess.run([sys.executable, "-c", script], check=True)
