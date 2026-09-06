"""Contract tests for the capability-inventory validator."""

from __future__ import annotations

import hashlib
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
                    "archive_sha256": "a" * 64,
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
                    "sha256": "b" * 64,
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
                    "sha256": "c" * 64,
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

    report = inventory.validate_inventory(tmp_path, ())

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

    report = inventory.validate_inventory(tmp_path, ())

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
    digest = hashlib.sha256(page.read_bytes()).hexdigest()
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
