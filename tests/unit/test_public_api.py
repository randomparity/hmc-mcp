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
from dataclasses import dataclass
from types import ModuleType
from typing import Annotated, Literal, TypeVar, get_args, get_origin, get_type_hints

import pytest

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


class FacadeContractError(AssertionError):
    """A signature this contract cannot read, reported with what it was reading.

    The clauses below walk annotation source text. When that text does not parse, the
    guard cannot say whether the contract holds -- which is a failure of the guard, not
    a passing signature. Raising a named error carrying the operation and the raw
    annotation keeps that distinguishable from a bare ``SyntaxError`` surfacing from
    inside ``ast``.
    """

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
        "RemoteRestartOperation",
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
    """Record every ``hmc_mcp``-owned type reachable from one resolved annotation.

    ``Callable[[X], Y]`` puts its parameter list in a plain ``list``, so the
    arguments of a subscripted hint are walked through sequences as well.
    A ``TypeVar`` names no type; its bound and constraints are walked instead.
    """
    if isinstance(hint, TypeVar):
        for constraint in (hint.__bound__, *hint.__constraints__):
            _collect_owned_types(constraint, owned)
        return
    if isinstance(hint, (list, tuple)):
        for element in hint:
            _collect_owned_types(element, owned)
        return
    origin = get_origin(hint)
    if origin is not None:
        _collect_owned_types(origin, owned)
        _collect_owned_types(get_args(hint), owned)
        return
    module_name = getattr(hint, "__module__", None)
    type_name = getattr(hint, "__name__", None)
    if not isinstance(module_name, str) or not isinstance(type_name, str):
        return
    if type_name.startswith("_"):
        return  # ADR 0029: every underscore name is internal.
    if module_name == "hmc_mcp" or module_name.startswith("hmc_mcp."):
        owned[(module_name, type_name)] = hint


def _attribute_path(node: ast.Attribute) -> tuple[str, ...] | None:
    """``jobs.RemoteRestartOperation`` -> ``("jobs", "RemoteRestartOperation")``."""
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return tuple(reversed(parts))


def _is_literal_subscript(node: ast.AST) -> bool:
    """Whether *node* subscripts ``Literal``, however ``Literal`` was imported."""
    if not isinstance(node, ast.Subscript):
        return False
    value = node.value
    if isinstance(value, ast.Attribute):  # typing.Literal, t.Literal
        return value.attr == "Literal"
    return isinstance(value, ast.Name) and value.id == "Literal"


def _annotation_paths(annotation: str, *, origin: str = "") -> set[tuple[str, ...]]:
    """Every dotted path an annotation's source text refers to.

    ``jobs.RemoteRestartOperation`` names an alias as surely as a bare
    ``RemoteRestartOperation`` does, and a quoted forward reference hides one inside a
    string literal, so both are walked. Underscore segments are internal and dropped.

    A ``Literal``'s arguments are values, not type references, so the walk does not
    descend into them. Descending treats the value as source: ``Literal["ok"]`` would
    contribute a phantom ``ok`` alias, and a value that is not a Python expression --
    ``Literal["Virtual IO Server"]``, which ``provision_lpar`` carries -- would fail
    to parse at all.
    """
    try:
        tree = ast.parse(annotation, mode="eval")
    except SyntaxError as exc:
        raise FacadeContractError(
            f"{origin or 'annotation'}: {annotation!r} does not parse as an "
            f"annotation, so ADR 0029's type and literal-alias clauses cannot be "
            f"applied to it ({exc.msg})."
        ) from exc

    paths: set[tuple[str, ...]] = set()
    literal_slices = {
        id(node.slice) for node in ast.walk(tree) if _is_literal_subscript(node)
    }
    skip: set[int] = set()
    for node in ast.walk(tree):
        if id(node) in literal_slices:
            skip |= {id(child) for child in ast.walk(node)}
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        if isinstance(node, ast.Name):
            paths.add((node.id,))
        elif isinstance(node, ast.Attribute):
            path = _attribute_path(node)
            if path is not None:
                paths.add(path)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            paths |= _annotation_paths(node.value, origin=origin)
    return {path for path in paths if not any(s.startswith("_") for s in path)}


def _literal_alias(
    namespace: dict[str, object], path: tuple[str, ...]
) -> object | None:
    """Resolve a dotted path in a namespace, keeping only what is a literal alias.

    ``Annotated[Literal[...], ...]`` is unwrapped first: the metadata is decoration and
    the alias underneath is what ADR 0029's literal clause is about.
    """
    value: object | None = namespace.get(path[0])
    for segment in path[1:]:
        value = getattr(value, segment, None)
    if getattr(value, "__metadata__", None) is not None:
        value = get_args(value)[0]
    return value if get_origin(value) is Literal else None


@functools.cache
def _module_import_bindings(module: ModuleType) -> dict[str, str]:
    """``name -> source module`` for every ``from ... import`` in a module's source."""
    if getattr(module, "__file__", None) is None:
        return {}
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    return {
        alias.asname or alias.name: (
            f"hmc_mcp.{node.module}" if node.level else node.module
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
        for alias in node.names
    }


def _defining_module(module: ModuleType, name: str) -> str | None:
    """Follow ``from ... import`` bindings to the module that defines a bare name.

    A literal alias is a plain assignment with no ``__module__`` to read, so its owner
    is recovered from the import statements instead, keeping the type clause keyed by
    the defining module the way the resolved-hint walk already is. ``None`` means the
    name entered from outside the package, and ADR 0029 makes an imported transport
    type no facade export.
    """
    seen: set[str] = set()
    while module.__name__ not in seen:
        if not module.__name__.startswith("hmc_mcp"):
            return None
        seen.add(module.__name__)
        origin = _module_import_bindings(module).get(name)
        if origin is None:
            return module.__name__
        module = import_module(origin)
    return module.__name__


def _alias_owner(module: ModuleType, path: tuple[str, ...]) -> str | None:
    """The module defining the alias a dotted path names, or ``None`` if outside."""
    if len(path) == 1:
        return _defining_module(module, path[0])
    container: object = module
    for segment in path[:-1]:
        container = getattr(container, segment, None)
    if not isinstance(container, ModuleType):
        return None
    return _defining_module(container, path[-1])


def _owned_literal_aliases(
    selected: set[tuple[str, str]], modules: dict[str, ModuleType]
) -> dict[tuple[str, str], object]:
    """The literal-alias clause, read from annotation source rather than resolved hints.

    ``get_type_hints`` evaluates ``PcmCategory`` down to ``Literal["ManagedSystem",
    ...]`` and the alias's name is gone with it, so the walk above cannot see one.
    Every ``operations_*`` module carries ``from __future__ import annotations``, which
    leaves the raw annotation as source text — asserted below rather than assumed,
    because a module without it would drop out of this clause entirely.
    """
    aliases: dict[tuple[str, str], object] = {}
    for module_name, name in selected:
        module = modules[module_name]
        for annotation in inspect.get_annotations(getattr(module, name)).values():
            assert isinstance(annotation, str), (
                f"{module_name}.{name} has an evaluated annotation; ADR 0029's "
                "literal-alias clause needs `from __future__ import annotations`"
            )
            for path in _annotation_paths(annotation, origin=f"{module_name}.{name}"):
                value = _literal_alias(vars(module), path)
                owner = _alias_owner(module, path) if value is not None else None
                if owner is not None:
                    aliases[(owner, path[-1])] = value
    return aliases


def _unexported_owned_types(
    selected: set[tuple[str, str]], modules: dict[str, ModuleType]
) -> dict[str, list[str]]:
    """Apply ADR 0029's type clause to every selected operation's signature.

    Opaque HMC payloads (``dict[str, Any]`` and friends) own no ``hmc_mcp`` type and
    so fall out by construction, which is exactly the distinction ADR 0029's
    Consequences section draws. The walk reaches the types an operation *names*; it
    does not descend into an owned model's own fields, so a type reachable only
    through a field of an exported model is out of this clause's scope (#482).
    """
    owned: dict[tuple[str, str], object] = {}
    for module_name, name in selected:
        for hint in get_type_hints(getattr(modules[module_name], name)).values():
            _collect_owned_types(hint, owned)
    owned.update(_owned_literal_aliases(selected, modules))

    manifest = set(api.__all__)
    exported = {
        key
        for key, value in owned.items()
        if key[1] in manifest and getattr(api, key[1], None) is value
    }
    return _selection_faults(set(owned), exported, ADR_0029_TYPE_EXCLUSIONS)


def test_facade_type_set_matches_adr_0029_selection_rule() -> None:
    """A supported call may not hand back a type with no supported import path.

    The PEP 561 marker (#367) is what makes this consumer-visible: a downstream
    type-checker resolves the real annotation and finds no way to name it.
    """
    modules = _operations_modules()
    faults = _unexported_owned_types(_selected_operations(modules), modules)
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


@dataclass(frozen=True)
class SyntheticResult:
    """An owned result type no facade exports. Module-level so ``get_type_hints``
    can resolve it the way it resolves a real operation's annotation."""

    value: str


# Reassigned after ``@dataclass`` runs, not before: the decorator resolves field types
# through ``sys.modules[cls.__module__]`` and would fail on a name that is not one.
SyntheticResult.__module__ = "hmc_mcp.operations_typed"
SyntheticFlavour = Literal["thin", "thick"]


def test_adr_0029_type_rule_reddens_end_to_end() -> None:
    """Drive signature -> hints -> walk -> comparison against an unexported type.

    Once the facade is correct the real check exercises the clean path only, so
    without this the whole walk could return nothing and stay green. This module
    carries ``from __future__ import annotations``, matching every ``operations_*``
    module, so the literal-alias half reads real source text here too.
    """
    module = ModuleType("hmc_mcp.operations_typed")

    async def synthetic_operation(
        hmc: object, flavour: SyntheticFlavour
    ) -> list[SyntheticResult | None]: ...

    synthetic_operation.__module__ = module.__name__
    module.SyntheticFlavour = SyntheticFlavour  # type: ignore[attr-defined]
    module.synthetic_operation = synthetic_operation  # type: ignore[attr-defined]

    faults = _unexported_owned_types(
        {(module.__name__, "synthetic_operation")}, {module.__name__: module}
    )
    assert faults["selected but not exported or excluded"] == [
        "hmc_mcp.operations_typed:SyntheticFlavour",
        "hmc_mcp.operations_typed:SyntheticResult",
    ]


def test_adr_0029_type_rule_requires_a_manifest_entry_not_a_bare_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Binding a type on ``hmc_mcp.api`` is not exporting it.

    ``__all__`` is the manifest this ADR freezes and the minor-release policy keys
    on; a merely-bound attribute records no supported import path and ``import *``
    does not carry it. Every facade name is both bound and listed today, so the
    distinction has no live case and stays unproven — and therefore free to be
    weakened back — unless a synthetic one drives it.
    """
    module = ModuleType("hmc_mcp.operations_typed")

    async def synthetic_operation(hmc: object) -> SyntheticResult: ...

    synthetic_operation.__module__ = module.__name__
    module.synthetic_operation = synthetic_operation  # type: ignore[attr-defined]

    monkeypatch.setattr(api, "SyntheticResult", SyntheticResult, raising=False)
    assert "SyntheticResult" not in api.__all__

    faults = _unexported_owned_types(
        {(module.__name__, "synthetic_operation")}, {module.__name__: module}
    )
    assert faults["selected but not exported or excluded"] == [
        "hmc_mcp.operations_typed:SyntheticResult"
    ]


def test_owned_type_walk_reaches_nested_and_callable_annotations() -> None:
    pcm_resource = import_module("hmc_mcp.operations_pcm").PcmResource
    collected: dict[tuple[str, str], object] = {}
    _collect_owned_types(list[pcm_resource | None], collected)
    # A ``Callable`` parameter list is a plain list, not a subscripted generic.
    _collect_owned_types(Callable[[pcm_resource], None], collected)
    # An opaque HMC payload mapping contributes nothing.
    _collect_owned_types(dict[str, object], collected)
    # Nor does an underscore-private owned type: ADR 0029 keeps it internal.
    _collect_owned_types(
        type("_Internal", (), {"__module__": "hmc_mcp.operations_pcm"}), collected
    )
    assert collected == {("hmc_mcp.operations_pcm", "PcmResource"): pcm_resource}

    # A ``TypeVar`` names no type either; its bound is walked in its place.
    from_bound: dict[tuple[str, str], object] = {}
    _collect_owned_types(TypeVar("Bound", bound=pcm_resource), from_bound)
    assert from_bound == collected


def test_literal_alias_clause_reads_paths_the_resolved_hints_lose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The alias half must see what ``get_type_hints`` erases, however it is written.

    A dotted reference and a quoted forward reference each name an alias as surely as
    a bare name does. Both are attributed to the module that *defines* the alias, not
    the one whose operation consumes it: ``operations_lpm`` names
    ``RemoteRestartOperation`` and ``jobs`` owns it.
    """
    lpm = import_module("hmc_mcp.operations_lpm")
    jobs = import_module("hmc_mcp.jobs")

    async def dotted(hmc: object, operation: jobs.RemoteRestartOperation) -> None: ...

    async def quoted(hmc: object, operation: "RemoteRestartOperation") -> None: ...  # noqa: F821

    for operation in (dotted, quoted):
        operation.__module__ = lpm.__name__
        monkeypatch.setattr(lpm, operation.__name__, operation, raising=False)
    monkeypatch.setattr(lpm, "jobs", jobs, raising=False)

    # Each form is checked alone: together, either one would cover for the other.
    for name in ("dotted", "quoted"):
        assert _owned_literal_aliases({(lpm.__name__, name)}, {lpm.__name__: lpm}) == {
            ("hmc_mcp.jobs", "RemoteRestartOperation"): api.RemoteRestartOperation
        }, name


def test_annotation_walk_does_not_read_literal_values_as_type_references() -> None:
    """A ``Literal``'s arguments are data, so neither crash nor phantom alias.

    A walk that descends into literal values treats the value as source. With a space
    in it — ``Literal["Virtual IO Server"]``, the shape ``PartitionType`` holds — the
    re-parse raises; without one, it quietly contributes an alias named after a value
    that is not a type at all. Same bug, one loud half and one silent half.

    No selected operation writes an inline ``Literal`` today: all 101 annotate through
    an alias, so neither half is reachable from the package as it stands. This is a
    guard against the first signature that does, not a repair of a live failure.
    """
    assert _annotation_paths('Literal["Virtual IO Server"]') == {("Literal",)}
    assert _annotation_paths('Literal["ok"]') == {("Literal",)}
    assert ("ok",) not in _annotation_paths('Literal["ok"]')
    # A dotted path contributes its root too, as every attribute walk here does.
    assert _annotation_paths('typing.Literal["Virtual IO Server"]') == {
        ("typing",),
        ("typing", "Literal"),
    }
    assert _annotation_paths('dict[str, Literal["a b"]] | None') == {
        ("dict",),
        ("str",),
        ("Literal",),
    }


def test_annotation_walk_still_resolves_a_quoted_forward_reference() -> None:
    """The literal skip must not cost the quoted-forward-reference walk."""
    assert ("jobs", "RemoteRestartOperation") in _annotation_paths(
        '"jobs.RemoteRestartOperation"'
    )


def test_annotation_walk_names_the_operation_when_an_annotation_will_not_parse() -> None:
    """An unreadable signature is a guard failure, and must report as one."""
    with pytest.raises(FacadeContractError) as error:
        _annotation_paths("not a valid annotation", origin="operations_x.do_thing")

    assert "operations_x.do_thing" in str(error.value)
    assert "not a valid annotation" in str(error.value)


def test_literal_alias_clause_unwraps_annotated_and_stops_at_the_package_edge() -> None:
    decorated = Annotated[Literal["thin", "thick"], "decoration"]
    namespace: dict[str, object] = {"Decorated": decorated, "Plain": SyntheticFlavour}
    assert _literal_alias(namespace, ("Decorated",)) is SyntheticFlavour
    assert _literal_alias(namespace, ("Plain",)) is SyntheticFlavour
    assert _literal_alias(namespace, ("Absent",)) is None

    # An alias a module imports from outside the package is no facade export:
    # ``operations_pcm`` takes ``Literal`` itself from ``typing``.
    pcm = import_module("hmc_mcp.operations_pcm")
    assert _defining_module(pcm, "Literal") is None
    assert _defining_module(pcm, "PcmCategory") == "hmc_mcp.operations_pcm"


def test_literal_alias_clause_rejects_an_evaluated_annotation() -> None:
    """The clause rests on the future import; a module without it must not go quiet."""
    module = ModuleType("hmc_mcp.operations_evaluated")

    async def evaluated(hmc: object) -> None: ...

    evaluated.__module__ = module.__name__
    evaluated.__annotations__ = {"return": None}
    module.evaluated = evaluated  # type: ignore[attr-defined]

    with pytest.raises(AssertionError, match="from __future__ import annotations"):
        _owned_literal_aliases(
            {(module.__name__, "evaluated")}, {module.__name__: module}
        )


def _unselectable_shape(module_name: str, name: str, value: object) -> str | None:
    """Classify a public module attribute ADR 0029's coroutine rule cannot select.

    A *synchronous* ``functools.partial`` is an ordinary transformation helper, which
    ADR 0029's synchronous-exclusion clause already covers, so only an asynchronous
    one is a shape the operation rule would have wanted and missed.
    """
    if inspect.isasyncgenfunction(value):
        return "asynchronous generator"
    if not inspect.iscoroutinefunction(value):
        return None
    if isinstance(value, functools.partial):
        return "functools.partial"
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
    iterates it, so a duplicate stays inert at runtime and invisible to all of them.
    The one test that compares the whole list would have caught a *new* duplicate, but
    the list it compares against had the existing one written into it.
    """
    repeated = sorted({name for name in api.__all__ if api.__all__.count(name) > 1})
    assert repeated == [], f"api.__all__ repeats {repeated}"


def _inventory_region() -> list[str]:
    """The lines between ADR 0029's inventory fence markers."""
    text = _ADR_0029_PATH.read_text(encoding="utf-8")
    for marker in (_INVENTORY_BEGIN, _INVENTORY_END):
        assert text.count(marker) == 1, f"ADR 0029 must carry one {marker} exactly"
    begin = text.index(_INVENTORY_BEGIN) + len(_INVENTORY_BEGIN)
    end = text.index(_INVENTORY_END)
    assert begin < end, "ADR 0029's inventory fence markers are inverted"
    return text[begin:end].splitlines()


def _inventory_entries() -> dict[str, str]:
    """The clause text of each ADR 0029 inventory bullet, keyed by module.

    Only the fenced region is read, and every line in it must be accounted for: an
    entry bullet, its wrapped continuation, a ``- Note:`` sub-bullet, that note's
    continuation, or a blank. Anything else is rejected rather than skipped.

    A note runs until the indentation dedents back to its own bullet or past it, not
    until the next blank line — so an entry may be followed by a blank line and then its
    note, and a note may carry more than one paragraph. Only text indented deeper than a
    ``- Note:`` bullet is narrative the parser passes over; a sub-bullet that is not a
    note is rejected outright, so a clause cannot masquerade as one.

    An entry is one bullet whose text is a semicolon-separated list of labelled
    clauses. That shape is what lets the document be asserted against the code rather
    than maintained beside it.
    """
    entries: dict[str, str] = {}
    current: str | None = None
    note_indent: int | None = None
    for line in _inventory_region():
        entry = _INVENTORY_ENTRY.match(line)
        stripped = line.strip()
        if entry is not None:
            current, note_indent = entry.group(1), None
            assert current not in entries, f"ADR 0029 inventories {current} twice"
            entries[current] = entry.group(2)
        elif not stripped:
            continue  # a note ends where the indentation dedents, not at a blank line
        elif line.startswith("  "):
            assert current is not None, f"ADR 0029 has stray inventory text: {line!r}"
            indent = len(line) - len(line.lstrip())
            if stripped.startswith("- "):
                assert stripped.startswith("- Note:"), (
                    f"ADR 0029's only inventory sub-bullet is a note: {line!r}"
                )
                note_indent = indent
            elif note_indent is None:
                entries[current] += " " + stripped
            else:
                assert indent > note_indent, (
                    f"ADR 0029 has stray inventory text: {line!r}"
                )
        else:
            raise AssertionError(f"ADR 0029 has stray inventory text: {line!r}")

    assert list(entries) == sorted(entries), "ADR 0029's inventory is not in order"
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
        for chunk in text.strip().rstrip(".").split("; "):
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


def _entries_from(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str
) -> dict[str, dict[str, list[str]]]:
    """Run the inventory parser over a synthetic fence.

    Read through ``_adr_0029_inventory`` rather than ``_inventory_entries``: a stray
    line is rejected by whichever layer reaches it first — the line walk refuses text
    it cannot place, and clause parsing refuses an entry that swallowed a paragraph.
    What matters to ADR 0029's claim is that the fence rejects it, not which layer did.
    """
    document = tmp_path / "adr_0029_probe.md"
    document.write_text(
        f"prose\n\n{_INVENTORY_BEGIN}\n{body}\n{_INVENTORY_END}\n", encoding="utf-8"
    )
    monkeypatch.setattr(sys.modules[__name__], "_ADR_0029_PATH", document)
    return _adr_0029_inventory()


_PROBE_ENTRY = "- `operations_vios` — operations: `power_vios`."
_PROBE_NOTE = "  - Note: narrative the parser passes over."

_REJECTED_FENCE_BODIES = {
    "a bare paragraph": f"{_PROBE_ENTRY}\n  Normative and unchecked.",
    "a fake clause sub-bullet": f"{_PROBE_ENTRY}\n  - types: `TotallyReal`",
    "a fake clause behind a note": f"{_PROBE_ENTRY}\n{_PROBE_NOTE}\n  - types: `X`",
    "a fake clause behind a note and a blank": (
        f"{_PROBE_ENTRY}\n{_PROBE_NOTE}\n\n  - types: `X`"
    ),
    "text dedented back out of a note": (
        f"{_PROBE_ENTRY}\n    - Note: narrative.\n  Normative and unchecked."
    ),
    "an unindented stray line": f"{_PROBE_ENTRY}\nNormative and unchecked.",
}


def test_inventory_parser_rejects_text_that_is_not_a_note(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The fence's only unchecked text is narrative indented under a ``- Note:``.

    ADR 0029 says a stray line between the fence markers fails the suite. That claim
    is only true if every way of writing one is rejected, so each way is driven here:
    a bare paragraph, a sub-bullet wearing a clause's clothes, and a line that dedents
    back out of a note. Without these the parser drifted once already — a blank line
    reopened the swallow, and a fake clause rode in behind a note.
    """
    assert _entries_from(monkeypatch, tmp_path, _PROBE_ENTRY) == {
        "operations_vios": {"operations": ["power_vios"]}
    }
    # A blank line before a note, and a second paragraph inside one, are ordinary
    # markdown and stay legal — a note ends by dedenting, not at the first blank line.
    for legal in (
        f"{_PROBE_ENTRY}\n\n{_PROBE_NOTE}\n    wrapped narrative.",
        f"{_PROBE_ENTRY}\n{_PROBE_NOTE}\n\n    a second paragraph of narrative.",
    ):
        assert set(_entries_from(monkeypatch, tmp_path, legal)) == {"operations_vios"}

    accepted = []
    for label, body in _REJECTED_FENCE_BODIES.items():
        try:
            _entries_from(monkeypatch, tmp_path, body)
        except AssertionError:
            continue
        accepted.append(label)
    assert accepted == [], f"ADR 0029's fence accepted stray text: {accepted}"


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
    inventory = _adr_0029_inventory()
    assert set(inventory) == set(expected), (
        f"inventoried but not a facade source: {sorted(set(inventory) - set(expected))}; "
        f"a facade source with no entry: {sorted(set(expected) - set(inventory))}"
    )
    for module, clauses in sorted(expected.items()):
        assert inventory[module] == clauses, f"ADR 0029's {module} entry has drifted"

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

        Last moved by issue #446, which exported the two types ADR 0029's
        newly mechanised type clause found missing from the manifest:
        `PcmResource`, the frozen dataclass `resolve_pcm_resource` returns, and
        `RemoteRestartOperation`, the literal alias `remote_restart_lpar` takes.
        Both report a signature, so adding them to `__all__` adds digest entries
        even though no operation's parameters changed.
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
    # Moved by #446: `PcmResource` enters the manifest with its dataclass
    # constructor, and `RemoteRestartOperation` with the `(*args, **kwargs)` every
    # literal alias reports. Recomputed over #371's baseline 44e83b7a.
    expected_digest = "960b037616127748f8e89bd517892d499571711ee2c2f9958ddc659f23a1bf9e"  # pragma: allowlist secret
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
    assert get_args(api.RemoteRestartOperation) == (
        "validate",
        "recover",
        "restart",
        "cleanup",
        "cancel",
    )


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
