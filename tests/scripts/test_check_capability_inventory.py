"""Contract tests for the capability-inventory validator."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
MODULE_PATH = ROOT / "scripts" / "check_capability_inventory.py"
SPEC = importlib.util.spec_from_file_location("check_capability_inventory", MODULE_PATH)
assert SPEC is not None
assert SPEC.loader is not None
inventory = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = inventory
SPEC.loader.exec_module(inventory)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _digest(character: str) -> str:
    return "-".join([character * 8] * 8)


def _minimal_inventory(root: Path) -> None:
    root.mkdir(exist_ok=True)
    _write_json(
        root / "corpora.json",
        {
            "format_version": 1,
            "corpora": [
                {
                    "id": "commands-p10",
                    "source_url": "https://example.test/commands",
                    "archive_sha256": _digest("a"),
                    "captured_pages": 1,
                    "navigation_pages": 0,
                }
            ],
            "topics": [
                {
                    "id": "commands-p10:alpha",
                    "corpus": "commands-p10",
                    "path": "alpha.md",
                    "source_url": "https://example.test/alpha",
                    "captured": "2026-08-23T00:00:00Z",
                    "sha256": _digest("b"),
                    "classification": "operation",
                    "reason": "command reference",
                }
            ],
            "source_units": [
                {
                    "id": "commands-p10:alpha:L10",
                    "topic": "commands-p10:alpha",
                    "kind": "command-option",
                    "line": 10,
                    "sha256": _digest("c"),
                    "text": "--name | resource name",
                    "accounting": {"kind": "row", "id": "cli:alpha"},
                }
            ],
        },
    )
    _write_json(
        root / "rows.json",
        {
            "format_version": 1,
            "rows": [
                {
                    "id": "cli:alpha",
                    "kind": "cli-command",
                    "name": "alpha",
                    "modes": [],
                    "parameters": ["commands-p10:alpha:L10"],
                    "source_units": ["commands-p10:alpha:L10"],
                    "releases": ["power10"],
                    "disposition": {"kind": "coverage-child", "issue": 637},
                }
            ],
        },
    )
    _write_json(
        root / "operations.json",
        {"format_version": 1, "operations": []},
    )
    _write_json(
        root / "maturity.json",
        {
            "format_version": inventory.MATURITY_FORMAT_VERSION,
            "admission_policy": "existing-runtime-guards",
            "operations": [],
        },
    )


def _scope(
    variant: str = "default", parameters: list[dict[str, str]] | None = None
) -> dict[str, object]:
    return {"variant": variant, "parameters": parameters or []}


def _operation(
    operation: str = "system.list", state: str = "implemented"
) -> dict[str, object]:
    scope = _scope()
    return {
        "operation": operation,
        "implementation": {
            "state": state,
            "implemented_scope": [] if state == "absent" else [scope],
            "missing_scope": [scope] if state == "absent" else [],
        },
        "evidence": [],
    }


def _observation(
    *,
    identity: str = "st1-hmc-get-console-info",
    result: str = "passed",
    fingerprint: str | None = None,
    observed_at: str = "2026-09-06T12:00:00Z",
) -> dict[str, object]:
    """One format 2 observation: the single closed shape the validator admits."""
    return {
        "id": identity,
        "channel": "live",
        "result": result,
        "scenario": "st1-console-identity",
        "tested_commit": "a" * 40,
        "observed_at": observed_at,
        "hmc_release": "V10R3",
        "hardware_family": "POWER10",
        "cleanup": "not-required",
        "closure_fingerprint": fingerprint or "b" * 64,
        "assertions": ["console-uuid-present"],
    }


def _write_maturity(root: Path, records: list[dict[str, object]]) -> None:
    _write_json(
        root / "maturity.json",
        {
            "format_version": inventory.MATURITY_FORMAT_VERSION,
            "admission_policy": "existing-runtime-guards",
            "operations": records,
        },
    )


@pytest.fixture
def registered_inventory(tmp_path: Path) -> tuple[inventory.RegistryTool, ...]:
    _minimal_inventory(tmp_path)
    tool = next(
        tool
        for tool in inventory.discover_registry()
        if tool.operation == "system.list"
    )
    _write_json(
        tmp_path / "operations.json",
        {
            "format_version": 1,
            "operations": [
                {
                    "tool": tool.tool,
                    "operation": tool.operation,
                    "handler": tool.handler,
                    "signature": tool.signature,
                    "surfaces": list(tool.surfaces),
                    "row_ids": [],
                    "composite_reason": "anonymous fixture operation",
                    "tests": [],
                }
            ],
        },
    )
    return (tool,)


def _maturity_report(
    root: Path,
    registry: tuple[inventory.RegistryTool, ...],
    records: list[dict[str, object]],
) -> inventory.Report:
    _write_maturity(root, records)
    return inventory.validate_inventory(root, registry, repo_root=root)


def test_load_json_rejects_duplicate_keys(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text('{"format_version":1,"format_version":1}', encoding="utf-8")
    with pytest.raises(inventory.InventoryError, match="duplicate key.*format_version"):
        inventory.load_json(path)


def test_validation_detects_dangling_and_double_accounted_units(tmp_path: Path) -> None:
    _minimal_inventory(tmp_path)
    rows = json.loads((tmp_path / "rows.json").read_text())
    rows["rows"][0]["source_units"].append("missing-unit")
    rows["rows"].append({**rows["rows"][0], "id": "cli:beta"})
    _write_json(tmp_path / "rows.json", rows)

    report = inventory.validate_inventory(tmp_path, (), repo_root=tmp_path)

    assert any("missing-unit" in error for error in report.errors)
    assert any("accounted by more than one row" in error for error in report.errors)


def test_unknowns_and_proposed_exclusions_prevent_completeness(tmp_path: Path) -> None:
    _minimal_inventory(tmp_path)
    rows = json.loads((tmp_path / "rows.json").read_text())
    rows["rows"][0]["disposition"] = {
        "kind": "unknown",
        "question": "Source does not state whether this is supported.",
    }
    _write_json(tmp_path / "rows.json", rows)

    report = inventory.validate_inventory(tmp_path, (), repo_root=tmp_path)

    assert not report.complete
    assert report.unknown_ids == ("cli:alpha",)
    assert not report.errors


def test_registry_evidence_checks_signature_and_paths(tmp_path: Path) -> None:
    _minimal_inventory(tmp_path)
    operations = {
        "format_version": 1,
        "operations": [
            {
                "tool": "hmc_alpha",
                "operation": "alpha.read",
                "handler": "example.handler",
                "signature": "(name: str)",
                "surfaces": ["mcp"],
                "row_ids": ["cli:alpha"],
                "composite_reason": None,
                "tests": ["tests/missing.py"],
            }
        ],
    }
    _write_json(tmp_path / "operations.json", operations)
    discovered = (
        inventory.RegistryTool(
            tool="hmc_alpha",
            operation="alpha.read",
            handler="example.handler",
            signature="(other: str)",
            surfaces=("mcp",),
        ),
    )

    report = inventory.validate_inventory(tmp_path, discovered, repo_root=tmp_path)

    assert any("signature" in error for error in report.errors)
    assert any("tests/missing.py" in error for error in report.errors)


def test_verify_corpora_detects_changed_and_extra_files(tmp_path: Path) -> None:
    data = tmp_path / "data"
    source = tmp_path / "source"
    data.mkdir()
    source.mkdir()
    page = source / "alpha.md"
    page.write_text("---\nsource: https://example.test/alpha\ncaptured: now\n---\n", encoding="utf-8")
    digest = inventory.format_sha256(page.read_bytes())
    _write_json(
        data / "corpora.json",
        {
            "format_version": 1,
            "corpora": [{"id": "commands-p10"}],
            "topics": [
                {
                    "id": "commands-p10:alpha",
                    "corpus": "commands-p10",
                    "path": "alpha.md",
                    "sha256": digest,
                }
            ],
            "source_units": inventory.extract_source_units(
                "commands-p10:alpha", page.read_text()
            ),
        },
    )

    assert inventory.verify_corpora(data, {"commands-p10": source}) == []
    page.write_text("changed\n", encoding="utf-8")
    (source / "extra.md").write_text("extra\n", encoding="utf-8")
    errors = inventory.verify_corpora(data, {"commands-p10": source})
    assert any("hash mismatch" in error for error in errors)
    assert any("unexpected source file" in error for error in errors)


def test_extract_source_units_preserves_both_capture_forms() -> None:
    lower = "---\ncaptured: 2026-08-23T00:00:00Z\n---\n## SYNOPSIS\nalpha --name X\n"
    capital = "# Index\n\nCaptured: 2026-08-23T00:00:00Z\n"

    assert inventory.extract_capture_time(lower) == "2026-08-23T00:00:00Z"
    assert inventory.extract_capture_time(capital) == "2026-08-23T00:00:00Z"
    assert inventory.extract_capture_time("# Overview\n") is None
    units = inventory.extract_source_units("commands-p10:alpha", lower)
    assert any(unit["kind"] == "command-synopsis" for unit in units)


def test_source_unit_summaries_do_not_publish_example_payload_values() -> None:
    source = """# Resource

```
{"HostName": "private.example", "IPAddress": "192.0.2.1"}
```
"""

    units = inventory.extract_source_units("rest-p10:resource", source)

    assert units[0]["text"] == "payload-root:HostName"
    assert "private.example" not in json.dumps(units)
    assert "192.0.2.1" not in json.dumps(units)


def test_sparse_maturity_allows_unknown_operations(tmp_path: Path) -> None:
    _minimal_inventory(tmp_path)

    report = inventory.validate_inventory(tmp_path, (), repo_root=tmp_path)

    assert not report.errors
    assert report.maturity_operation_count == 0


def test_maturity_rejects_extra_root_keys(tmp_path: Path) -> None:
    _minimal_inventory(tmp_path)
    maturity = inventory.load_json(tmp_path / "maturity.json")
    maturity["unexpected"] = None
    _write_json(tmp_path / "maturity.json", maturity)

    report = inventory.validate_inventory(tmp_path, (), repo_root=tmp_path)

    assert any("maturity.json: expected exactly" in error for error in report.errors)


def test_maturity_rejects_boolean_format_version(tmp_path: Path) -> None:
    _minimal_inventory(tmp_path)
    maturity = inventory.load_json(tmp_path / "maturity.json")
    maturity["format_version"] = True
    _write_json(tmp_path / "maturity.json", maturity)

    report = inventory.validate_inventory(tmp_path, (), repo_root=tmp_path)

    assert "maturity.json: format_version must be integer 2" in report.errors


def test_maturity_rejects_unknown_and_duplicate_operation_ids(tmp_path: Path) -> None:
    _minimal_inventory(tmp_path)
    _write_maturity(
        tmp_path, [_operation("unknown.operation"), _operation("unknown.operation")]
    )

    report = inventory.validate_inventory(tmp_path, (), repo_root=tmp_path)

    assert any("unknown operation" in error for error in report.errors)
    assert any("duplicate operation" in error for error in report.errors)


@pytest.mark.parametrize(
    ("state", "implemented", "missing", "expected"),
    [
        ("absent", [], [_scope()], False),
        ("partial", [_scope()], [_scope("missing")], False),
        ("implemented", [_scope()], [], False),
        ("absent", [_scope()], [_scope("missing")], True),
        ("partial", [_scope()], [], True),
        ("implemented", [_scope()], [_scope("missing")], True),
        ("partial", [_scope()], [_scope()], True),
    ],
)
def test_maturity_enforces_implementation_scope(
    tmp_path: Path,
    registered_inventory: tuple[inventory.RegistryTool, ...],
    state: str,
    implemented: list[dict[str, object]],
    missing: list[dict[str, object]],
    expected: bool,
) -> None:
    record = _operation(state=state)
    record["implementation"] = {
        "state": state,
        "implemented_scope": implemented,
        "missing_scope": missing,
    }
    report = _maturity_report(tmp_path, registered_inventory, [record])

    assert (
        any("contradictory implementation scope" in error for error in report.errors)
        is expected
    )
    if not expected:
        assert not report.errors


def test_sriov_catalog_separates_confirmation_from_mode_changes() -> None:
    catalog = inventory.load_json(ROOT / "docs" / "capabilities" / "maturity.json")
    record = next(
        row for row in catalog["operations"] if row["operation"] == "sriov.set_mode"
    )

    assert record["implementation"]["state"] == "partial"
    assert [
        scope["variant"] for scope in record["implementation"]["implemented_scope"]
    ] == ["current-mode-confirmation"]
    assert [
        scope["variant"] for scope in record["implementation"]["missing_scope"]
    ] == ["adapter-mode-transition"]
    assert record["evidence"] == []


@pytest.mark.parametrize("value", [[], {}, 1, None])
@pytest.mark.parametrize("field", ["operation", "state"])
def test_maturity_reports_invalid_identity_and_state_types(
    tmp_path: Path,
    registered_inventory: tuple[inventory.RegistryTool, ...],
    field: str,
    value: object,
) -> None:
    record = _operation()
    record["evidence"] = [_observation()]
    assert not _maturity_report(tmp_path, registered_inventory, [record]).errors

    if field == "state":
        record["implementation"][field] = value
        diagnostic = "invalid implementation state or scope lists"
    else:
        record[field] = value
        diagnostic = "operation must be a non-empty string"

    errors = _maturity_report(tmp_path, registered_inventory, [record]).errors

    assert any(
        error.startswith("maturity operation ") and diagnostic in error
        for error in errors
    )


def test_a_colliding_observation_id_is_distinguished_from_a_malformed_one(
    tmp_path: Path, registered_inventory: tuple[inventory.RegistryTool, ...]
) -> None:
    """Two defects, two diagnostics: a mis-cased id is not a collision."""
    record = _operation()
    record["evidence"] = [_observation(), _observation()]

    errors = _maturity_report(tmp_path, registered_inventory, [record]).errors

    assert (
        "maturity evidence st1-hmc-get-console-info: id must be catalog-wide unique"
        in errors
    )


@pytest.mark.parametrize(
    ("field", "value", "diagnostic"),
    [
        ("id", [], "id must match [a-z0-9][a-z0-9-]*"),
        ("id", "Not-An-Id", "id must match [a-z0-9][a-z0-9-]*"),
        ("channel", [], "invalid channel or result"),
        ("channel", {}, "invalid channel or result"),
        ("result", [], "invalid channel or result"),
        ("result", "not-run", "invalid channel or result"),
        ("result", "skipped", "invalid channel or result"),
        ("cleanup", [], "invalid cleanup"),
        ("cleanup", {}, "invalid cleanup"),
        ("scenario", [], "scenario must match st<n>-<slug>"),
        ("scenario", "console-identity", "scenario must match st<n>-<slug>"),
    ],
)
def test_maturity_reports_invalid_observation_types(
    tmp_path: Path,
    registered_inventory: tuple[inventory.RegistryTool, ...],
    field: str,
    value: object,
    diagnostic: str,
) -> None:
    record = _operation()
    observation = _observation()
    record["evidence"] = [observation]
    assert not _maturity_report(tmp_path, registered_inventory, [record]).errors

    observation[field] = value
    errors = _maturity_report(tmp_path, registered_inventory, [record]).errors

    if field != "id":
        label = "maturity evidence st1-hmc-get-console-info"
    elif isinstance(value, str):
        label = f"maturity evidence {value}"
    else:
        label = "maturity operation system.list evidence"
    assert f"{label}: {diagnostic}" in errors


def test_maturity_format_one_is_rejected(
    tmp_path: Path, registered_inventory: tuple[inventory.RegistryTool, ...]
) -> None:
    """ADR 0127 supersedes 0126's evidence model; a format 1 catalog is not read."""
    _write_maturity(tmp_path, [])
    document = inventory.load_json(tmp_path / "maturity.json")
    document["format_version"] = 1
    _write_json(tmp_path / "maturity.json", document)

    report = inventory.validate_inventory(
        tmp_path, registered_inventory, repo_root=tmp_path
    )

    assert "maturity.json: format_version must be integer 2" in report.errors


@pytest.mark.parametrize(
    "mutation",
    [
        {"result": "not-run"},
        {"reason": "hardware unavailable"},
        {"prerequisites": ["a POWER10 frame"]},
        {"obligation": {"catalog": "console.info#st1"}},
    ],
)
def test_a_not_run_observation_is_rejected(
    tmp_path: Path,
    registered_inventory: tuple[inventory.RegistryTool, ...],
    mutation: dict[str, object],
) -> None:
    """Format 2 has one observation shape; the `not-run` placeholder is gone."""
    record = _operation()
    record["evidence"] = [{**_observation(), **mutation}]

    assert _maturity_report(tmp_path, registered_inventory, [record]).errors


@pytest.mark.parametrize(
    "mutation",
    [{"unexpected": True}, {"cleanup": None}],
)
def test_observation_key_sets_are_exact(
    tmp_path: Path,
    registered_inventory: tuple[inventory.RegistryTool, ...],
    mutation: dict[str, object],
) -> None:
    observation = {**_observation(), **mutation}
    if mutation.get("cleanup") is None:
        del observation["cleanup"]
    record = _operation()
    record["evidence"] = [observation]

    assert any(
        "expected exactly" in error
        for error in _maturity_report(tmp_path, registered_inventory, [record]).errors
    )


@pytest.mark.parametrize(
    ("field", "value", "accepted"),
    [
        ("hmc_release", "V10R3", True),
        ("hmc_release", "V10R3M1", True),
        ("hardware_family", "POWER10", True),
        # Every identifier class the repository's privacy rule names. A
        # `[A-Za-z0-9 ._-]{0,39}` class with an IPv4 rejection admits the first
        # four verbatim, which is why it was replaced rather than patched.
        ("hmc_release", "hmc01.lab.example.com", False),
        ("hmc_release", "0644C7T", False),
        ("hmc_release", "U78CB.001.WZS0044-P1-C2", False),
        ("hmc_release", "lab-hmc-3", False),
        ("hmc_release", "10.1.2.3", False),
        ("hardware_family", "hmc01.lab.example.com", False),
        ("hardware_family", "0644C7T", False),
        ("hardware_family", "U78CB.001.WZS0044-P1-C2", False),
        ("hardware_family", "lab-hmc-3", False),
        ("hardware_family", "10.1.2.3", False),
    ],
)
def test_environment_values_reject_private_identifiers(
    tmp_path: Path,
    registered_inventory: tuple[inventory.RegistryTool, ...],
    field: str,
    value: str,
    accepted: bool,
) -> None:
    record = _operation()
    record["evidence"] = [{**_observation(), field: value}]

    errors = _maturity_report(tmp_path, registered_inventory, [record]).errors

    assert (
        "maturity evidence st1-hmc-get-console-info: environment values do not "
        "match their grammar" in errors
    ) is not accepted


@pytest.mark.parametrize(
    ("mutation", "diagnostic"),
    [
        ({"tested_commit": "a" * 39}, "tested_commit must be a full SHA"),
        ({"closure_fingerprint": "b" * 63}, "closure_fingerprint must be a full SHA-256"),
        ({"assertions": ["Console UUID present"]}, "assertions must be closed-shape tokens"),
        ({"assertions": ["a-b", "a-b"]}, "assertions must be unique"),
        (
            {"result": "failed", "cleanup": "wiped"},
            "invalid cleanup",
        ),
        # What stops a hand-authored `passed` row that asserted nothing, or one
        # whose cleanup failed, from deriving `current`.
        ({"assertions": []}, "passed evidence requires assertions"),
        ({"cleanup": "failed"}, "a passed observation requires successful cleanup"),
    ],
)
def test_attempted_observation_fields_are_pattern_bound(
    tmp_path: Path,
    registered_inventory: tuple[inventory.RegistryTool, ...],
    mutation: dict[str, object],
    diagnostic: str,
) -> None:
    """`maturity.json` is hand-copied, so the validator is the only check it meets."""
    record = _operation()
    record["evidence"] = [{**_observation(), **mutation}]

    errors = _maturity_report(tmp_path, registered_inventory, [record]).errors

    assert f"maturity evidence st1-hmc-get-console-info: {diagnostic}" in errors


def _package(root: Path, modules: dict[str, str]) -> None:
    """Write a throwaway `src/hmc_mcp/` package for the closure walk to read."""
    package = root / "src" / "hmc_mcp"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    for name, source in modules.items():
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


def test_closure_fingerprint_changes_with_an_imported_module_only(
    tmp_path: Path,
) -> None:
    _package(
        tmp_path,
        {"a.py": "from .b import thing\n", "b.py": "thing = 1\n", "c.py": "other = 2\n"},
    )
    first = inventory.closure_fingerprint(tmp_path, "hmc_mcp.a")

    (tmp_path / "src" / "hmc_mcp" / "c.py").write_text("other = 3\n", encoding="utf-8")
    assert inventory.closure_fingerprint(tmp_path, "hmc_mcp.a") == first

    (tmp_path / "src" / "hmc_mcp" / "b.py").write_text("thing = 2\n", encoding="utf-8")
    assert inventory.closure_fingerprint(tmp_path, "hmc_mcp.a") != first


@pytest.mark.parametrize(
    "statement",
    [
        "from .b import thing",
        "from . import b",
        "import hmc_mcp.b",
        # A `TYPE_CHECKING` guard is a module-level `If`, so the import is absent
        # from `tree.body` itself; a `try:/except ImportError:` is the same shape.
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from .b import thing",
        "try:\n    from .b import thing\nexcept ImportError:\n    thing = None",
        "try:\n    thing = None\nexcept ImportError:\n    from .b import thing",
        "try:\n    pass\nfinally:\n    from .b import thing",
    ],
)
def test_closure_covers_each_import_form(tmp_path: Path, statement: str) -> None:
    """`from . import b` has `module is None`; a walk reading only `module` misses it.

    The guarded forms pin the traversal depth from the other side: reading only
    `tree.body` skips them, and an operation whose only path to a module runs
    through a `TYPE_CHECKING` guard would then never go stale when it changes.
    """
    _package(tmp_path, {"a.py": f"{statement}\n", "b.py": "thing = 1\n"})

    paths = inventory.closure_paths(tmp_path, "hmc_mcp.a")

    assert tmp_path / "src" / "hmc_mcp" / "b.py" in paths


def test_closure_stops_at_a_function_boundary_inside_a_guard(tmp_path: Path) -> None:
    """The `If` recursion must not become an excuse to read function bodies."""
    _package(
        tmp_path,
        {
            "a.py": "if True:\n    def later():\n        from .b import thing\n",
            "b.py": "thing = 1\n",
        },
    )

    assert tmp_path / "src" / "hmc_mcp" / "b.py" not in inventory.closure_paths(
        tmp_path, "hmc_mcp.a"
    )


def test_closure_excludes_function_body_imports() -> None:
    """A deferred import is not part of the module's import-time implementation.

    `src/hmc_mcp/__init__.py` imports `.cli` inside `main()` and sits on every
    resolution path, so an `ast.walk` implementation yields 179 of 180 files for
    every handler — ADR 0126's repository-wide fingerprint under another name.
    """
    paths = inventory.closure_paths(ROOT, "hmc_mcp.server_tools.permissions")

    assert ROOT / "src" / "hmc_mcp" / "cli.py" not in paths
    assert len(paths) < 20


def test_closure_resolves_packages() -> None:
    """`from ..jobs import JobOutcome` names a package and a class, not a module.

    A module-file-only walk finds neither file, so a change to
    `SUCCESSFUL_JOB_STATUSES` — the constant the job scenarios assert against —
    would leave their observations reading as current.
    """
    paths = inventory.closure_paths(ROOT, "hmc_mcp.server_tools.jobs")

    assert ROOT / "src" / "hmc_mcp" / "jobs" / "__init__.py" in paths
    assert ROOT / "src" / "hmc_mcp" / "jobs" / "core.py" in paths


def test_lifecycle_closure_includes_bare_relative_imports() -> None:
    """`lifecycle.py` imports both siblings as `from . import …` (ADR 0127)."""
    paths = inventory.closure_paths(ROOT, "hmc_mcp.server_tools.lpar.lifecycle")
    lpar = ROOT / "src" / "hmc_mcp" / "server_tools" / "lpar"

    assert lpar / "lifecycle_boot.py" in paths
    assert lpar / "lifecycle_create.py" in paths


def test_closure_containment(tmp_path: Path) -> None:
    """The walk never reads a symlink and never resolves above the package.

    A symlink pointing outside the repository would make the fingerprint
    machine-dependent, so the runner's recorded value and CI's recomputation
    could never agree and the observation would read stale forever.
    """
    _package(
        tmp_path,
        {
            "a.py": "from .b import thing\nfrom ....far import away\n",
            "outside.py": "thing = 1\n",
        },
    )
    package = tmp_path / "src" / "hmc_mcp"
    (package / "b.py").symlink_to(package / "outside.py")

    paths = inventory.closure_paths(tmp_path, "hmc_mcp.a")

    assert package / "b.py" not in paths
    assert paths == [package / "__init__.py", package / "a.py"]


_NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


def _closure_registry(
    tmp_path: Path, operation: str = "system.list"
) -> tuple[inventory.RegistryTool, ...]:
    _package(tmp_path, {"a.py": "thing = 1\n"})
    return (
        inventory.RegistryTool(
            tool="hmc_list_systems",
            operation=operation,
            handler="hmc_mcp.a.hmc_list_systems",
            signature="()",
            surfaces=("mcp",),
        ),
    )


@pytest.mark.parametrize(
    ("records", "expected", "reason"),
    [
        ([], "unrecorded", None),
        ([{"evidence": []}], "unevidenced", None),
        ([{"evidence": [{"closure_fingerprint": "c" * 64}]}], "stale", "closure-changed"),
        ([{"evidence": [{"observed_at": "2026-01-01T00:00:00Z"}]}], "stale", "age-exceeded"),
        ([{"evidence": [{"result": "failed"}]}], "failed", None),
        ([{"evidence": [{}]}], "current", None),
        # Only a live observation derives a state: the validator still admits
        # `contract-review` and `automated`, and promoting either as live
        # verification is exactly the claim ADR 0127 makes about `current`.
        ([{"evidence": [{"channel": "contract-review"}]}], "unevidenced", None),
        ([{"evidence": [{"channel": "automated"}]}], "unevidenced", None),
    ],
)
def test_derived_states(
    tmp_path: Path,
    records: list[dict[str, object]],
    expected: str,
    reason: str | None,
) -> None:
    """Staleness is derived when the catalog is read, never stored."""
    registry = _closure_registry(tmp_path)
    fingerprint = inventory.closure_fingerprint(tmp_path, "hmc_mcp.a")
    catalog = [
        {
            "operation": "system.list",
            "implementation": _operation()["implementation"],
            "evidence": [
                {**_observation(fingerprint=fingerprint), **override}
                for override in record["evidence"]
            ],
        }
        for record in records
    ]

    states = inventory.derive_states(catalog, registry, tmp_path, _NOW)

    assert states["system.list"].state == expected
    assert states["system.list"].reason == reason


def test_report_line_carries_implementation_state(capsys) -> None:
    """A bare `current` would read as "live-verified" for a partial operation."""
    states = {
        "sriov.set_mode": inventory.OperationState("current", None, "partial"),
        "vnic.add": inventory.OperationState("unrecorded"),
    }

    assert inventory.verification_report(states, fail_on_stale=False) == 0

    output = capsys.readouterr().out
    assert "verification: sriov.set_mode partial current" in output
    assert "verification: vnic.add unrecorded" in output


def test_verification_report_summary_and_fail_on_stale(capsys) -> None:
    states = {
        "system.list": inventory.OperationState("stale", "closure-changed", "implemented"),
        "vnic.add": inventory.OperationState("unrecorded"),
    }

    assert inventory.verification_report(states, fail_on_stale=False) == 0
    assert (
        "verification coverage: 1 unrecorded, 0 unevidenced, 1 stale, 0 failed, "
        "0 current (of 2)" in capsys.readouterr().out
    )
    assert inventory.verification_report(states, fail_on_stale=True) == 1


def test_a_stale_observation_is_not_an_error(
    tmp_path: Path, registered_inventory: tuple[inventory.RegistryTool, ...]
) -> None:
    """Recording evidence must never turn the build red (ADR 0127)."""
    record = _operation()
    record["evidence"] = [_observation(fingerprint="c" * 64)]

    assert not _maturity_report(tmp_path, registered_inventory, [record]).errors


def test_verification_report_annotates_a_github_actions_run(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """The CI job's whole purpose is the annotations, and nothing else covers them."""
    summary = tmp_path / "step-summary.md"
    monkeypatch.setenv("GITHUB_ACTIONS", "1")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    states = {
        "system.list": inventory.OperationState("stale", "age-exceeded", "implemented"),
        "sriov.set_mode": inventory.OperationState("current", None, "partial"),
    }

    assert inventory.verification_report(states, fail_on_stale=False) == 0

    output = capsys.readouterr().out
    assert "::warning::system.list is stale: age-exceeded" in output
    assert "::warning::sriov.set_mode" not in output
    table = summary.read_text(encoding="utf-8")
    assert "| Operation | Implementation | State | Reason |" in table
    assert "| system.list | implemented | stale | age-exceeded |" in table
    assert "| sriov.set_mode | partial | current |  |" in table


def test_verification_report_survives_an_unwritable_step_summary(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """An unwritable summary path must not redden the pull request job."""
    monkeypatch.setenv("GITHUB_ACTIONS", "1")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "missing" / "summary.md"))

    assert (
        inventory.verification_report(
            {"system.list": inventory.OperationState("unrecorded")}, fail_on_stale=False
        )
        == 0
    )
    assert "could not append the step summary" in capsys.readouterr().err


def test_verification_report_is_quiet_outside_github_actions(
    monkeypatch, capsys
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    states = {"system.list": inventory.OperationState("stale", "age-exceeded", "implemented")}

    inventory.verification_report(states, fail_on_stale=False)

    assert "::warning::" not in capsys.readouterr().out


def test_closure_refuses_a_relative_import_that_leaves_the_package(
    tmp_path: Path,
) -> None:
    """At package depth 1, `from ..x import y` resolves against `src/` itself."""
    _package(tmp_path, {"a.py": "from ..outside import thing\n"})
    (tmp_path / "src" / "outside.py").write_text("thing = 1\n", encoding="utf-8")

    paths = inventory.closure_paths(tmp_path, "hmc_mcp.a")

    assert tmp_path / "src" / "outside.py" not in paths
    assert paths == [
        tmp_path / "src" / "hmc_mcp" / "__init__.py",
        tmp_path / "src" / "hmc_mcp" / "a.py",
    ]


def test_fail_on_stale_requires_the_report(capsys) -> None:
    """Silently ignoring the flag would report success on a stale catalog."""
    with pytest.raises(SystemExit) as raised:
        inventory.main(["--fail-on-stale"])

    assert raised.value.code == 2
    assert "--fail-on-stale requires --verification-report" in capsys.readouterr().err
