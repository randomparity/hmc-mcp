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
    operation: str = "alpha.read", state: str = "implemented"
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
    page.write_text(
        "---\nsource: https://example.test/alpha\ncaptured: now\n---\n",
        encoding="utf-8",
    )
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
    state: str,
    implemented: list[dict[str, object]],
    missing: list[dict[str, object]],
    expected: bool,
) -> None:
    _minimal_inventory(tmp_path)
    record = _operation("unknown.operation", state)
    record["implementation"] = {
        "state": state,
        "implemented_scope": implemented,
        "missing_scope": missing,
    }
    _write_maturity(tmp_path, [record])

    report = inventory.validate_inventory(tmp_path, (), repo_root=tmp_path)

    assert (
        any("contradictory implementation scope" in error for error in report.errors)
        is expected
    )


def test_maturity_accepts_all_evidence_results(tmp_path: Path) -> None:
    _minimal_inventory(tmp_path)
    record = _operation("unknown.operation")
    record["evidence"] = [
        _evidence(
            tmp_path, identity=f"evidence-{result}", channel="automated", result=result
        )
        for result in ("not-run", "skipped", "failed", "passed")
    ]
    _write_maturity(tmp_path, [record])

    report = inventory.validate_inventory(tmp_path, (), repo_root=tmp_path)

    assert any("unknown operation" in error for error in report.errors)
    assert not any("evidence-" in error for error in report.errors)


def test_live_pass_requires_scoped_postconditions_and_cleanup(tmp_path: Path) -> None:
    _minimal_inventory(tmp_path)
    record = _operation("unknown.operation")
    observation = _evidence(tmp_path, environment=_environment())
    observation["assertions"] = []
    observation["cleanup"] = "failed"
    observation["observed_at"] = "20260906T120000Z"
    record["evidence"] = [observation]
    _write_maturity(tmp_path, [record])

    report = inventory.validate_inventory(tmp_path, ())

    assert any("evidence evidence-alpha" in error for error in report.errors)


def test_live_not_run_requires_prerequisites_and_obligation(tmp_path: Path) -> None:
    _minimal_inventory(tmp_path)
    record = _operation("unknown.operation")
    observation = _evidence(tmp_path, result="not-run")
    observation["environment"] = _environment()
    observation["scenario"] = None
    record["evidence"] = [observation]
    _write_maturity(tmp_path, [record])

    report = inventory.validate_inventory(tmp_path, ())

    assert any("evidence evidence-alpha" in error for error in report.errors)


def test_format_one_rejects_promotion_eligible(tmp_path: Path) -> None:
    _minimal_inventory(tmp_path)
    record = _operation("unknown.operation")
    observation = _evidence(tmp_path, environment=_environment())
    observation["promotion"] = {"eligible": True, "reason": "mock success"}
    record["evidence"] = [observation]
    _write_maturity(tmp_path, [record])

    assert any(
        "promotion" in error
        for error in inventory.validate_inventory(
            tmp_path, (), repo_root=tmp_path
        ).errors
    )


def test_implementation_fingerprint_changes_with_shared_source(tmp_path: Path) -> None:
    source = tmp_path / "src" / "shared.py"
    source.parent.mkdir()
    source.write_text("first\n", encoding="utf-8")
    first = inventory.implementation_fingerprint(tmp_path)
    source.write_text("second\n", encoding="utf-8")

    assert inventory.implementation_fingerprint(tmp_path) != first


def test_maturity_preserves_stale_pass_before_current_regression(
    tmp_path: Path,
) -> None:
    _minimal_inventory(tmp_path)
    record = _operation("unknown.operation")
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
    record["evidence"] = [stale, current]
    _write_maturity(tmp_path, [record])

    assert not any(
        "current" in error
        for error in inventory.validate_inventory(
            tmp_path, (), repo_root=tmp_path
        ).errors
    )


def test_maturity_keys_live_currency_by_environment(tmp_path: Path) -> None:
    _minimal_inventory(tmp_path)
    record = _operation("unknown.operation")
    record["evidence"] = [
        _evidence(tmp_path, identity="environment-a", environment=_environment("a")),
        _evidence(tmp_path, identity="environment-b", environment=_environment("b")),
    ]
    _write_maturity(tmp_path, [record])

    assert not any(
        "current" in error
        for error in inventory.validate_inventory(
            tmp_path, (), repo_root=tmp_path
        ).errors
    )


def test_maturity_identity_normalizes_scope_and_environment(tmp_path: Path) -> None:
    _minimal_inventory(tmp_path)
    record = _operation("unknown.operation")
    first = _evidence(tmp_path, identity="first", environment=_environment())
    second = _evidence(
        tmp_path,
        identity="second",
        scope={"parameters": [], "variant": "default"},
        environment=dict(reversed(list(_environment().items()))),
    )
    second["scenario"] = first["scenario"]
    record["evidence"] = [first, second]
    _write_maturity(tmp_path, [record])

    assert any(
        "current" in error
        for error in inventory.validate_inventory(
            tmp_path, (), repo_root=tmp_path
        ).errors
    )
