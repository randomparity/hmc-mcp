"""Contract tests for the capability-inventory validator."""

from __future__ import annotations

import importlib.util
import json
import sys
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
            "format_version": 1,
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


def _evidence(
    root: Path,
    *,
    identity: str = "evidence-alpha",
    channel: str = "live",
    result: str = "passed",
    currency: str = "current",
    scope: dict[str, object] | None = None,
    environment: dict[str, str] | None = None,
) -> dict[str, object]:
    attempted = result != "not-run"
    live = channel == "live"
    return {
        "id": identity,
        "channel": channel,
        "scope": scope or _scope(),
        "scenario": {"id": f"scenario-{identity}", "description": "anonymous scenario"},
        "result": result,
        "currency": currency,
        "observed_at": "2026-09-06T12:00:00Z" if attempted else None,
        "implementation_revision": "a" * 40 if attempted else None,
        "deployed_revision": "b" * 40 if attempted and live else None,
        "environment": environment if live else None,
        "assertions": ["anonymous postcondition"] if result == "passed" else [],
        "cleanup": "passed" if result == "passed" else "not-run",
        "provenance": {"kind": "unverified", "reference": "fixture"}
        if attempted
        else None,
        "promotion": {"eligible": False, "reason": "format one is non-promoting"},
        "reason": None if result == "passed" else "not attempted",
        "prerequisites": [],
        "obligation": None,
        "implementation_fingerprint": inventory.implementation_fingerprint(root)
        if attempted
        else None,
        "invalidated_by": None,
    }


def _environment(build: str = "build-a") -> dict[str, str]:
    return {
        "hmc_release": "release-a",
        "hmc_build": build,
        "hardware_family": "family-a",
        "firmware": "firmware-a",
        "licensing": "licensed",
        "topology": "topology-a",
    }


def _write_maturity(root: Path, records: list[dict[str, object]]) -> None:
    _write_json(
        root / "maturity.json",
        {
            "format_version": 1,
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

    assert "maturity.json: format_version must be integer 1" in report.errors


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


def test_maturity_accepts_all_evidence_results(
    tmp_path: Path, registered_inventory: tuple[inventory.RegistryTool, ...]
) -> None:
    record = _operation()
    record["evidence"] = [
        _evidence(
            tmp_path, identity=f"evidence-{result}", channel="automated", result=result
        )
        for result in ("not-run", "skipped", "failed", "passed")
    ]
    assert not _maturity_report(tmp_path, registered_inventory, [record]).errors


@pytest.mark.parametrize(
    ("field", "value", "diagnostic"),
    [
        ("assertions", [], "passed evidence requires assertions"),
        ("cleanup", "failed", "live pass requires successful cleanup"),
        ("observed_at", "20260906T120000Z", "observed_at must be canonical UTC"),
        ("observed_at", "2026-09-06 12:00:00Z", "observed_at must be canonical UTC"),
        (
            "observed_at",
            "2026-09-06T12:00:00+00:00",
            "observed_at must be canonical UTC",
        ),
        ("observed_at", "2026-09-06T12:00:00.0Z", "observed_at must be canonical UTC"),
        (
            "observed_at",
            "2026-02-30T12:00:00Z",
            "observed_at is not a calendar timestamp",
        ),
    ],
)
def test_live_pass_requires_scoped_postconditions_and_cleanup(
    tmp_path: Path,
    registered_inventory: tuple[inventory.RegistryTool, ...],
    field: str,
    value: object,
    diagnostic: str,
) -> None:
    record = _operation()
    observation = _evidence(tmp_path, environment=_environment())
    record["evidence"] = [observation]
    assert not _maturity_report(tmp_path, registered_inventory, [record]).errors

    observation[field] = value
    report = _maturity_report(tmp_path, registered_inventory, [record])

    assert report.errors == (f"maturity evidence evidence-alpha: {diagnostic}",)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scenario", None),
        ("prerequisites", []),
        ("obligation", None),
        ("obligation", {"catalog": "system.list#missing"}),
        (
            "obligation",
            {"catalog": "system.list#evidence-alpha", "issue": True},
        ),
    ],
)
def test_live_not_run_requires_prerequisites_and_obligation(
    tmp_path: Path,
    registered_inventory: tuple[inventory.RegistryTool, ...],
    field: str,
    value: object,
) -> None:
    record = _operation()
    observation = _evidence(tmp_path, result="not-run", environment=_environment())
    observation["prerequisites"] = ["admitted hardware available"]
    observation["obligation"] = {"catalog": "system.list#evidence-alpha"}
    record["evidence"] = [observation]
    assert not _maturity_report(tmp_path, registered_inventory, [record]).errors

    observation[field] = value
    report = _maturity_report(tmp_path, registered_inventory, [record])

    assert report.errors == (
        (
            "maturity evidence evidence-alpha: "
            "live not-run requires scenario, prerequisites, and catalog obligation"
        ),
    )


def test_format_one_rejects_promotion_eligible(
    tmp_path: Path, registered_inventory: tuple[inventory.RegistryTool, ...]
) -> None:
    record = _operation()
    observation = _evidence(tmp_path, environment=_environment())
    record["evidence"] = [observation]
    assert not _maturity_report(tmp_path, registered_inventory, [record]).errors

    observation["promotion"] = {"eligible": True, "reason": "mock success"}

    assert _maturity_report(tmp_path, registered_inventory, [record]).errors == (
        "maturity evidence evidence-alpha: format 1 promotion must be ineligible",
    )


def test_implementation_fingerprint_changes_with_shared_source(tmp_path: Path) -> None:
    source = tmp_path / "src" / "shared.py"
    source.parent.mkdir()
    source.write_text("first\n", encoding="utf-8")
    first = inventory.implementation_fingerprint(tmp_path)
    source.write_text("second\n", encoding="utf-8")

    assert inventory.implementation_fingerprint(tmp_path) != first


def test_maturity_preserves_stale_pass_before_current_regression(
    tmp_path: Path, registered_inventory: tuple[inventory.RegistryTool, ...]
) -> None:
    record = _operation()
    stale = _evidence(
        tmp_path, identity="stale", currency="stale", environment=_environment()
    )
    stale["invalidated_by"] = {
        "implementation_fingerprint": inventory.implementation_fingerprint(tmp_path),
        "reason": "source changed",
    }
    current = _evidence(
        tmp_path, identity="current", result="failed", environment=_environment()
    )
    current["scenario"] = stale["scenario"]
    stale["implementation_fingerprint"] = "c" * 64
    record["evidence"] = [stale, current]

    assert not _maturity_report(tmp_path, registered_inventory, [record]).errors


def test_stale_evidence_survives_removed_implementation_scope(
    tmp_path: Path, registered_inventory: tuple[inventory.RegistryTool, ...]
) -> None:
    record = _operation(state="absent")
    stale = _evidence(tmp_path, currency="stale", environment=_environment())
    stale["implementation_fingerprint"] = "c" * 64
    stale["invalidated_by"] = {
        "implementation_fingerprint": inventory.implementation_fingerprint(tmp_path),
        "reason": "implementation scope removed",
    }
    record["evidence"] = [stale]

    assert not _maturity_report(tmp_path, registered_inventory, [record]).errors


def test_stale_evidence_survives_parameter_scope_narrowing(
    tmp_path: Path, registered_inventory: tuple[inventory.RegistryTool, ...]
) -> None:
    old_scope = _scope(parameters=[{"name": "kind", "constraint": "all"}])
    new_scope = _scope(parameters=[{"name": "kind", "constraint": "managed"}])
    record = _operation()
    record["implementation"]["implemented_scope"] = [new_scope]
    stale = _evidence(
        tmp_path, currency="stale", scope=old_scope, environment=_environment()
    )
    stale["implementation_fingerprint"] = "c" * 64
    stale["invalidated_by"] = {
        "implementation_fingerprint": inventory.implementation_fingerprint(tmp_path),
        "reason": "implementation scope narrowed",
    }
    record["evidence"] = [stale]

    assert not _maturity_report(tmp_path, registered_inventory, [record]).errors


def test_maturity_keys_live_currency_by_environment(
    tmp_path: Path, registered_inventory: tuple[inventory.RegistryTool, ...]
) -> None:
    record = _operation()
    first = _evidence(tmp_path, identity="environment-a", environment=_environment("a"))
    second = _evidence(
        tmp_path,
        identity="environment-b",
        result="failed",
        environment=_environment("b"),
    )
    second["scenario"] = first["scenario"]
    record["evidence"] = [first, second]
    assert not _maturity_report(tmp_path, registered_inventory, [record]).errors

    second["environment"] = first["environment"]

    assert _maturity_report(tmp_path, registered_inventory, [record]).errors == (
        "maturity evidence environment-b: duplicate current evidence",
    )


def test_maturity_identity_normalizes_scope_and_environment(
    tmp_path: Path, registered_inventory: tuple[inventory.RegistryTool, ...]
) -> None:
    record = _operation()
    first = _evidence(tmp_path, identity="first", environment=_environment())
    record["evidence"] = [first]
    assert not _maturity_report(tmp_path, registered_inventory, [record]).errors
    second = _evidence(
        tmp_path,
        identity="second",
        scope={"parameters": [], "variant": "default"},
        environment=dict(reversed(list(_environment().items()))),
    )
    second["scenario"] = first["scenario"]
    record["evidence"] = [first, second]

    assert _maturity_report(tmp_path, registered_inventory, [record]).errors == (
        "maturity evidence second: duplicate current evidence",
    )


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


@pytest.mark.parametrize("channel", ["contract-review", "automated", "live"])
@pytest.mark.parametrize("result", ["skipped", "failed", "passed"])
def test_attempted_evidence_requires_scenario(
    tmp_path: Path,
    registered_inventory: tuple[inventory.RegistryTool, ...],
    channel: str,
    result: str,
) -> None:
    record = _operation()
    observation = _evidence(
        tmp_path, channel=channel, result=result, environment=_environment()
    )
    record["evidence"] = [observation]
    assert not _maturity_report(tmp_path, registered_inventory, [record]).errors

    observation["scenario"] = None

    assert _maturity_report(tmp_path, registered_inventory, [record]).errors == (
        "maturity evidence evidence-alpha: attempted evidence requires scenario",
    )


@pytest.mark.parametrize("channel", ["contract-review", "automated", "live"])
@pytest.mark.parametrize("result", ["not-run", "skipped", "failed", "passed"])
def test_obligation_is_only_valid_for_live_not_run(
    tmp_path: Path,
    registered_inventory: tuple[inventory.RegistryTool, ...],
    channel: str,
    result: str,
) -> None:
    record = _operation()
    observation = _evidence(
        tmp_path, channel=channel, result=result, environment=_environment()
    )
    live_gap = channel == "live" and result == "not-run"
    if live_gap:
        observation["prerequisites"] = ["admitted hardware available"]
        observation["obligation"] = {"catalog": "system.list#evidence-alpha"}
    record["evidence"] = [observation]
    assert not _maturity_report(tmp_path, registered_inventory, [record]).errors

    observation["obligation"] = {"catalog": "system.list#evidence-alpha", "issue": 623}
    errors = _maturity_report(tmp_path, registered_inventory, [record]).errors

    assert errors == (
        ()
        if live_gap
        else (
            "maturity evidence evidence-alpha: obligation is only valid for live not-run",
        )
    )


@pytest.mark.parametrize(
    ("field", "value", "diagnostic"),
    [
        (
            "promotion",
            {"eligible": False, "reason": None},
            "promotion reason is required",
        ),
        (
            "promotion",
            {"eligible": False, "reason": " "},
            "promotion reason is required",
        ),
        (
            "implementation_revision",
            int("1" * 40),
            "implementation_revision must be a full SHA",
        ),
        (
            "deployed_revision",
            int("1" * 40),
            "live evidence requires deployed_revision",
        ),
    ],
)
def test_attempted_evidence_rejects_invalid_conditional_fields(
    tmp_path: Path,
    registered_inventory: tuple[inventory.RegistryTool, ...],
    field: str,
    value: object,
    diagnostic: str,
) -> None:
    record = _operation()
    observation = _evidence(tmp_path, environment=_environment())
    record["evidence"] = [observation]
    assert not _maturity_report(tmp_path, registered_inventory, [record]).errors

    observation[field] = value

    assert _maturity_report(tmp_path, registered_inventory, [record]).errors == (
        f"maturity evidence evidence-alpha: {diagnostic}",
    )


@pytest.mark.parametrize("field", ["implementation_fingerprint", "invalidated_by"])
def test_fingerprints_require_strings(
    tmp_path: Path,
    registered_inventory: tuple[inventory.RegistryTool, ...],
    field: str,
) -> None:
    record = _operation()
    observation = _evidence(tmp_path, currency="stale", environment=_environment())
    observation["invalidated_by"] = {
        "implementation_fingerprint": inventory.implementation_fingerprint(tmp_path),
        "reason": "source changed",
    }
    record["evidence"] = [observation]
    assert not _maturity_report(tmp_path, registered_inventory, [record]).errors

    if field == "invalidated_by":
        observation[field]["implementation_fingerprint"] = int("1" * 64)
        diagnostic = "stale evidence requires an invalidator"
    else:
        observation[field] = int("1" * 64)
        diagnostic = "implementation_fingerprint must be a full SHA-256"

    assert _maturity_report(tmp_path, registered_inventory, [record]).errors == (
        f"maturity evidence evidence-alpha: {diagnostic}",
    )


def test_current_evidence_rejects_stored_fingerprint_after_source_change(
    tmp_path: Path, registered_inventory: tuple[inventory.RegistryTool, ...]
) -> None:
    source = tmp_path / "src" / "shared.py"
    source.parent.mkdir()
    source.write_text("first\n", encoding="utf-8")
    record = _operation()
    record["evidence"] = [_evidence(tmp_path, environment=_environment())]
    assert not _maturity_report(tmp_path, registered_inventory, [record]).errors

    source.write_text("second\n", encoding="utf-8")

    assert _maturity_report(tmp_path, registered_inventory, [record]).errors == (
        "maturity evidence evidence-alpha: stale implementation fingerprint",
    )


@pytest.mark.parametrize("value", [[], {}, 1, None])
@pytest.mark.parametrize("field", ["operation", "state"])
def test_maturity_reports_invalid_identity_and_state_types(
    tmp_path: Path,
    registered_inventory: tuple[inventory.RegistryTool, ...],
    field: str,
    value: object,
) -> None:
    record = _operation()
    record["evidence"] = [_evidence(tmp_path, environment=_environment())]
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


@pytest.mark.parametrize(
    ("field", "value", "diagnostic"),
    [
        ("id", [], "id must be catalog-wide unique"),
        ("channel", [], "invalid channel, result, or currency"),
        ("channel", {}, "invalid channel, result, or currency"),
        ("result", [], "invalid channel, result, or currency"),
        ("currency", [], "invalid channel, result, or currency"),
        ("cleanup", [], "invalid cleanup"),
        ("cleanup", {}, "invalid cleanup"),
        ("scenario", [], "invalid scenario"),
        (
            "scenario",
            {"id": [], "description": "anonymous scenario"},
            "invalid scenario",
        ),
        (
            "scenario",
            {"id": {}, "description": "anonymous scenario"},
            "invalid scenario",
        ),
        ("scope", {"variant": [], "parameters": []}, "invalid variant or parameters"),
        (
            "environment",
            {**_environment(), "hmc_build": []},
            "environment fields are required",
        ),
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
    observation = _evidence(tmp_path, environment=_environment())
    record["evidence"] = [observation]
    assert not _maturity_report(tmp_path, registered_inventory, [record]).errors

    observation[field] = value
    errors = _maturity_report(tmp_path, registered_inventory, [record]).errors

    label = (
        "maturity operation system.list evidence"
        if field == "id"
        else ("maturity evidence evidence-alpha")
    )
    assert f"{label}: {diagnostic}" in errors
