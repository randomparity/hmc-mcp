from __future__ import annotations

import ast
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
    "power9-v10r3m1060-live-sriov.json",
    "power9-v10r3m1060-live-vnic.json",
}
P9_URL = (
    "https://www.ibm.com/docs/en/power9/0000-REF?topic=POWER9_REF%2Fp9edm%2Flshwres.htm"
)
EXPECTED_PROVENANCE = {
    "power8-profile.json": (
        "https://www.ibm.com/docs/en/power8/8284-22A?topic=commands-lssyscfg",
        "lssyscfg > -r prof > -F > sriov_eth_logical_ports",
    ),
    "power8-profile-contract.json": (
        "https://www.ibm.com/docs/en/power8/8284-22A?topic=commands-chsyscfg",
        "chsyscfg > -r prof > io_slots,sriov_eth_logical_ports",
    ),
    "power9-io-slot.json": (
        P9_URL,
        "lshwres > -r io > --rsubtype slot > -F > drc_index,description,lpar_name",
    ),
    "power9-sriov-adapter.json": (
        P9_URL,
        "lshwres > -r sriov > --rsubtype adapter > adapter_ids",
    ),
    "power9-sriov-physport.json": (
        P9_URL,
        "lshwres > -r sriov > --rsubtype physport > --level eth > adapter_ids,phys_port_ids",
    ),
    "power9-sriov-logport.json": (
        P9_URL,
        "lshwres > -r sriov > --rsubtype logport > --level eth > "
        "adapter_ids,logical_port_ids,phys_port_ids",
    ),
    "power10-sriov-contract.json": (
        "https://www.ibm.com/docs/en/power10/7063-CR1?topic=commands-chhwres",
        "chhwres > -r io > -o a/r > -l; chhwres > -r sriov > "
        "slot_id,adapter_id,logical_port_id,capacity,max_capacity,"
        "min_eth_capacity_granularity",
    ),
    "power11-sriov-contract.json": (
        "https://www.ibm.com/docs/en/power11/9824-42A?topic=commands-chhwres",
        "chhwres > -r io > -o a/r > -l; chhwres > -r sriov > "
        "slot_id,adapter_id,logical_port_id,capacity,max_capacity,"
        "min_eth_capacity_granularity",
    ),
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


@pytest.mark.parametrize(
    "text",
    ["", " \n\t", "a,c\n1,2", "a,b\n1", 'a,b\n1,"bad', 'a,b\n1,"x\ny"'],
)
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
    for name, record in zip(
        sorted(EXPECTED_FIXTURES), _evidence_records(), strict=True
    ):
        if record["record_kind"] == "live-capture":
            assert record["evidence_kind"] == "live-capture"
            assert record["hmc_release"] == "V10R3 M1060 build 2408210051"
            assert record["system_model"] == "8375-42A"
            assert record["support"] == "captured"
            assert str(record["source_url"]).startswith("https://github.com/")
            assert record["probes"]
            for probe in record["probes"]:
                assert set(probe) == {
                    "name",
                    "command",
                    "fields",
                    "exit_status",
                    "stdout",
                    "stderr",
                }
            continue
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
        assert (record["source_url"], record["source_locator"]) == EXPECTED_PROVENANCE[
            name
        ]
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
    vnic = records["power9-v10r3m1060-live-vnic"]
    assert vnic["probes"][0]["fields"][-2:] == [
        "backing_devices",
        "backing_device_states",
    ]
    assert vnic["probes"][1]["fields"] == [
        "lpar_name",
        "lpar_id",
        "type",
        "adapter_id",
        "physical_port_id",
        "logical_port_id",
        "capacity",
        "desired_capacity",
        "max_capacity",
        "desired_max_capacity",
        "failover_priority",
        "is_active",
        "status",
    ]
    slot = records["power9-io-slot"]
    rows = parse_hmc_delimited_rows(slot["parser_examples"]["stdout"], slot["fields"])
    assert [row["drc_index"] for row in rows] == ["21010003", "21010004"]
    assert rows[1]["lpar_name"] == ""
    assert records["power9-sriov-logport"]["admitted_claims"][0] == (
        "system + adapter_id + logical_port_id selectors"
    )
    assert records["power8-profile"]["command"] == (
        "lssyscfg -r prof -m sys1 -F sriov_eth_logical_ports --header"
    )
    assert records["power8-profile"]["fields"] == ["sriov_eth_logical_ports"]
    assert records["power9-io-slot"]["command"] == (
        "lshwres -r io --rsubtype slot -m sys1 "
        "-F drc_index,description,lpar_name --header"
    )
    assert records["power9-io-slot"]["fields"] == [
        "drc_index",
        "description",
        "lpar_name",
    ]
    assert records["power9-sriov-adapter"]["admitted_claims"] == [
        "system + adapter_id selector",
        "read fields remain unknown",
    ]
    assert records["power9-sriov-physport"]["admitted_claims"] == [
        "system + adapter_id + phys_port_id selectors",
        "physical-port selector grammar uses --level eth",
        "read fields remain unknown",
    ]
    expected_capacity_claims = [
        "dedicated-slot dynamic operations use -r io -o a/r -l",
        "adapter mode uses -o a/r with slot_id",
        "logical-port operations use adapter_id and logical_port_id",
        "capacity, max_capacity, and minimum granularity are percent with up to two decimals",
    ]
    for family in ("power10-sriov-contract", "power11-sriov-contract"):
        assert records[family]["capacity_unit"] == "percent"
        assert records[family]["admitted_claims"] == expected_capacity_claims
        assert "two decimals" in records[family]["admitted_claims"][-1]
        assert Decimal("10.25").as_tuple().exponent == -2


def test_operation_matrix_fails_closed_without_same_family_readback() -> None:
    spec = (
        ROOT
        / "docs"
        / "workflow"
        / "specs"
        / "2026-08-20-pcie-capability-contract-design.md"
    ).read_text()
    rows = {
        columns[0]: columns[1:]
        for line in spec.splitlines()
        if line.startswith("| Assign/unassign") or line.startswith("| Switch adapter")
        if len(columns := [part.strip() for part in line.strip("|").split("|")]) == 5
    }
    assert set(rows) == {
        "Assign/unassign dedicated slot",
        "Assign/unassign SR-IOV logical port",
        "Switch adapter shared/dedicated mode",
    }
    assert all(len(outcomes) == 4 for outcomes in rows.values())
    assert (
        "do not compose Power10/11 mutation evidence with Power9 read evidence"
        in rows["Assign/unassign dedicated slot"][2]
    )
    assert all(
        "do not mutate" in outcome
        for outcome in rows["Assign/unassign SR-IOV logical port"]
    )
    assert all(
        "do not mutate" in outcome
        for outcome in rows["Switch adapter shared/dedicated mode"][1:]
    )
    for token in (
        "lssyscfg -r lpar -m SYSTEM --filter lpar_ids=ID -F state,rmc_state",
        "RoCE profile grammar is unknown",
        "every non-success",
        "available-empty",
        "Issue #214 owns replacing or removing that path",
    ):
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


def _canonical_ast(value: object) -> object:
    if isinstance(value, ast.AST):
        fields = []
        for name in value._fields:
            item = _canonical_ast(getattr(value, name, None))
            if item not in (None, [], ()):
                fields.append((name, item))
        return (type(value).__name__, fields)
    if isinstance(value, list):
        return [_canonical_ast(item) for item in value]
    return value


def test_sriov_mutation_surface_replaces_legacy_mode_and_never_forces() -> None:
    source = (ROOT / "src" / "hmc_mcp" / "ssh_commands.py").read_text()
    current = ast.parse(source)
    functions = {
        node.name: node
        for node in current.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "set_sriov_adapter_mode" not in functions
    for name in (
        "assign_sriov_logical_port_dynamic",
        "unassign_sriov_logical_port_profile",
    ):
        rendered = ast.unparse(functions[name])
        assert "--force" not in rendered
    assert "-o s --id" not in source
