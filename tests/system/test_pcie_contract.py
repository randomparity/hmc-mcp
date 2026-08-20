from __future__ import annotations

import ast
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from hmc_mcp.ssh_commands import parse_hmc_delimited_rows

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "pcie"
EXPECTED_FIXTURES = {
    "power8-profile-contract.json",
    "power8-profile.json",
    "power9-io-slot.json",
    "power9-sriov-adapter.json",
    "power9-sriov-logport.json",
    "power9-sriov-physport.json",
    "power10-sriov-contract.json",
    "power11-sriov-contract.json",
}


@pytest.mark.parametrize(
    ("text", "fields", "delimiter"),
    [
        ("a,b\n1,2\n", (), ","),
        ("a,b\n1,2\n", ("a", "a"), ","),
        ("a,b\n1,2\n", ("a", " b"), ","),
        ("a,b\n1,2\n", ("a", "b"), ""),
        ("a,b\n1,2\n", ("a", "b"), "\n"),
    ],
)
def test_parser_rejects_invalid_contract(
    text: str, fields: tuple[str, ...], delimiter: str
) -> None:
    with pytest.raises(ValueError):
        parse_hmc_delimited_rows(text, fields, delimiter)


@pytest.mark.parametrize("text", ["", " \n\t", "a,c\n1,2", "a,b\n1", 'a,b\n1,"bad'])
def test_parser_rejects_malformed_output(text: str) -> None:
    with pytest.raises(ValueError):
        parse_hmc_delimited_rows(text, ("a", "b"))


def test_parser_preserves_csv_values_and_empty_rows() -> None:
    text = '\na,b\n\n" x,y ",\n,\n'
    assert parse_hmc_delimited_rows(text, ("a", "b")) == [
        {"a": " x,y ", "b": ""},
        {"a": "", "b": ""},
    ]


def test_parser_accepts_header_only_and_final_line_without_newline() -> None:
    assert parse_hmc_delimited_rows("a,b", ("a", "b")) == []
    assert parse_hmc_delimited_rows("a,b\n1,2", ("a", "b")) == [{"a": "1", "b": "2"}]


def _evidence_records() -> list[dict[str, object]]:
    assert {path.name for path in FIXTURES.glob("*.json")} == EXPECTED_FIXTURES
    return [
        json.loads((FIXTURES / name).read_text()) for name in sorted(EXPECTED_FIXTURES)
    ]


def test_evidence_records_have_closed_versioned_shapes() -> None:
    common = {
        "record_kind",
        "evidence_kind",
        "documentation_family",
        "hmc_release",
        "source_url",
        "source_locator",
        "claim_summary",
        "support",
    }
    for record in _evidence_records():
        assert record["evidence_kind"] == "documentation"
        assert record["documentation_family"] in {
            "Power8",
            "Power9",
            "Power10",
            "Power11",
        }
        assert record["hmc_release"] == "not-established"
        assert str(record["source_url"]).startswith("https://www.ibm.com/")
        assert record["source_locator"] and record["claim_summary"]
        if record["record_kind"] == "read-fixture":
            assert set(record) == common | {"command", "fields", "parser_examples"}
            assert record["support"] == "documented"
            assert str(record["command"]).startswith(("lssyscfg ", "lshwres "))
            example = record["parser_examples"]
            assert isinstance(example, dict) and example["kind"] == "synthetic"
            assert parse_hmc_delimited_rows(example["stdout"], record["fields"])
        else:
            optional_unit = {"capacity_unit"} if "capacity_unit" in record else set()
            assert set(record) == common | {"admitted_claims"} | optional_unit
            assert record["support"] == "unknown"
            assert record["admitted_claims"]


def test_evidence_pins_identity_and_capacity_semantics() -> None:
    records = {
        path.stem: json.loads(path.read_text()) for path in FIXTURES.glob("*.json")
    }
    slot = records["power9-io-slot"]
    rows = parse_hmc_delimited_rows(slot["parser_examples"]["stdout"], slot["fields"])
    assert [row["drc_index"] for row in rows] == ["21010003", "21010004"]
    assert rows[1]["lpar_name"] == ""
    assert records["power9-sriov-logport"]["admitted_claims"][0] == (
        "system + adapter_id + logical_port_id selectors"
    )
    for family in ("power10-sriov-contract", "power11-sriov-contract"):
        assert records[family]["capacity_unit"] == "percent"
        assert Decimal("10.25").as_tuple().exponent == -2


def test_operation_matrix_fails_closed_without_same_family_readback() -> None:
    spec = (
        ROOT
        / "docs"
        / "workflow"
        / "specs"
        / "2026-08-20-pcie-capability-contract-design.md"
    ).read_text()
    required = (
        "Create time",
        "Inactive/shut down",
        "Running",
        "Capability unavailable",
        "do not compose Power10/11 mutation evidence with Power9 read evidence",
        "RoCE profile grammar is unknown",
        "every non-success",
        "available-empty",
        "Issue #214 owns replacing or removing that path",
    )
    for token in required:
        assert token in spec


def _call_name(node: ast.Call) -> str:
    parts: list[str] = []
    value: ast.expr = node.func
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if isinstance(value, ast.Name):
        parts.append(value.id)
    return ".".join(reversed(parts))


def test_only_strict_parser_changes_production_module() -> None:
    current = ast.parse((ROOT / "src" / "hmc_mcp" / "ssh_commands.py").read_text())
    parser = next(
        node
        for node in current.body
        if isinstance(node, ast.FunctionDef) and node.name == "parse_hmc_delimited_rows"
    )
    current.body.remove(parser)
    baseline_digest = hashlib.sha256(ast.dump(current).encode()).hexdigest()
    assert (
        baseline_digest
        == "feb6c7347133ca5da9dabb973326b95fb180c459c6e98c56b5ce68a298ee4f14"  # pragma: allowlist secret
    )
    calls = {
        _call_name(node) for node in ast.walk(parser) if isinstance(node, ast.Call)
    }
    assert calls == {
        "any",
        "csv.reader",
        "dict",
        "enumerate",
        "field.strip",
        "len",
        "line.strip",
        "list",
        "rows.append",
        "set",
        "text.splitlines",
        "tuple",
        "ValueError",
        "zip",
    }
