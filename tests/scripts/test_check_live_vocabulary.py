"""Tests for the live-vocabulary guard (scripts/check_live_vocabulary.py, issue #1202)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parents[2] / "scripts" / "check_live_vocabulary.py"
MODULE_SPEC = importlib.util.spec_from_file_location(
    "check_live_vocabulary", MODULE_PATH
)
assert MODULE_SPEC is not None
assert MODULE_SPEC.loader is not None
guard = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules["check_live_vocabulary"] = guard
MODULE_SPEC.loader.exec_module(guard)

VOCABULARY = {
    "firmware": "vX",
    "source": "test",
    "rest": {
        "endpoints": [
            {
                "method": "GET",
                "path": "/rest/api/uom/ManagedSystem/{uuid}",
                "status": 200,
            },
            {
                "method": "GET",
                "path": "/rest/api/uom/LogicalPartition/search/(PartitionName=={value})",
                "status": 200,
            },
            {
                "method": "GET",
                "path": "/rest/api/pcm/ManagedSystem/{uuid}/ProcessedMetrics?StartTS={value}",
                "status": 403,
            },
            {"method": "PUT", "path": "/rest/api/uom/Written", "status": 200},
        ],
        "errors": [],
        "values": {"PartitionState": ["<text>", "open firmware"], "Other": ["x"]},
        "element_enums": {"PartitionState": "LogicalPartitionState.Enum"},
    },
    "cli": {
        "commands": [
            {"command": "lssyscfg -r lpar -m {} -F name"},
            {"command": "lshwres -r sriov --rsubtype physport -m {} --level eth"},
        ],
        "values": {},
    },
}
ENUMS = {
    "firmware": "vX",
    "types": {"LogicalPartitionState.Enum": ["running", "error"]},
}


def _evidence() -> guard.Evidence:
    return guard.Evidence(
        allowed={"PartitionState": {"running", "error", "open firmware"}},
        bindings={"PartitionState": "LogicalPartitionState.Enum"},
        endpoints=[
            e["path"] for e in VOCABULARY["rest"]["endpoints"] if e["method"] == "GET"
        ],
        commands=[c["command"] for c in VOCABULARY["cli"]["commands"]],
    )


def _values(file: str, text: str) -> list[tuple[str, str]]:
    return [
        (v.subject, v.value) for v in guard.literal_violations(file, text, _evidence())
    ]


# --- evidence ------------------------------------------------------------------


def test_load_evidence_unions_enum_and_observed_literals(tmp_path: Path) -> None:
    directory = tmp_path / guard.VOCABULARY_DIR
    directory.mkdir(parents=True)
    (directory / "vx-p9.json").write_text(json.dumps(VOCABULARY))
    (directory / "enums-vx.json").write_text(json.dumps(ENUMS))
    evidence = guard.load_evidence(tmp_path)
    assert evidence.allowed == {"PartitionState": {"running", "error", "open firmware"}}
    assert "/rest/api/uom/Written" not in evidence.endpoints
    assert "Other" not in evidence.allowed  # unbound elements are not checked


def test_load_evidence_refuses_an_empty_directory(tmp_path: Path) -> None:
    (tmp_path / guard.VOCABULARY_DIR).mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        guard.load_evidence(tmp_path)


# --- literals ----------------------------------------------------------------------


def test_xml_leaf_in_a_python_string_is_checked() -> None:
    text = 'BODY = "<PartitionState kb=\\"ROO\\">Running</PartitionState>"\n'
    assert _values("t.py", text) == [("PartitionState", "Running")]


def test_captured_and_enum_values_pass() -> None:
    text = (
        'A = "<PartitionState>open firmware</PartitionState>"\n'
        'B = "<PartitionState>error</PartitionState>"\n'
    )
    assert _values("t.py", text) == []


def test_fstring_placeholder_values_are_skipped() -> None:
    assert _values("t.py", 'A = f"<PartitionState>{state}</PartitionState>"\n') == []


def test_xml_file_leaves_are_checked_with_lines() -> None:
    found = guard.literal_violations(
        "t.xml", "<a>\n<PartitionState>Started</PartitionState>\n</a>", _evidence()
    )
    assert [(v.value, v.line) for v in found] == [("Started", 2)]


@pytest.mark.parametrize(
    "expression",
    [
        'lpar.get("PartitionState") == "Running"',
        'lpar["PartitionState"] != "Running"',
        'lpar.get("PartitionState") in ("running", "Running")',
        '"Running" == lpar.get("PartitionState")',
        'str(lpar.get("PartitionState", "")).strip() == "Running"',
    ],
)
def test_comparisons_keyed_by_an_element_are_checked(expression: str) -> None:
    assert _values("t.py", f"ok = {expression}\n") == [("PartitionState", "Running")]


def test_lowered_comparison_uses_lowered_values() -> None:
    assert (
        _values("t.py", 'ok = lpar.get("PartitionState").lower() == "running"\n') == []
    )
    assert (
        _values("t.py", 'ok = lpar.get("PartitionState").upper() == "RUNNING"\n') == []
    )
    found = _values("t.py", 'ok = lpar.get("PartitionState").lower() == "Running"\n')
    assert found == [("PartitionState", "Running")]


def test_dict_entries_keyed_by_an_element_are_checked() -> None:
    assert _values("t.py", 'd = {"PartitionState": "Not Activated"}\n') == [
        ("PartitionState", "Not Activated")
    ]


def test_dict_value_holding_xml_is_not_a_value() -> None:
    text = 'd = {"PartitionState": "<PartitionState>running</PartitionState>"}\n'
    assert _values("t.py", text) == []


def test_unbound_elements_are_not_checked() -> None:
    assert _values("t.py", 'd = {"Status": "COMPLETED"}\n') == []


# --- REST paths --------------------------------------------------------------------


def _paths(text: str) -> list[str]:
    return [p for p, _ in guard.rest_templates("src/hmcpctl/x.py", text)]


def test_read_paths_come_from_functions_that_get() -> None:
    text = '''
async def read(self, uuid):
    """Docstring /rest/api/uom/Docs is ignored."""
    path = f"/rest/api/uom/ManagedSystem/{uuid}"
    return await self._get(path, "ManagedSystem")

async def write(self, uuid, body):
    await self._put(f"/rest/api/uom/Written/{uuid}", body)

async def power(self, uuid):
    return await self._get(f"/rest/api/uom/LogicalPartition/{uuid}/do/PowerOn")
'''
    assert _paths(text) == ["/rest/api/uom/ManagedSystem/{}"]


def test_returned_path_builders_count_as_reads() -> None:
    text = """
def job_path(job_id):
    path = f"/rest/api/uom/jobs/{job_id}"
    return path
"""
    assert _paths(text) == ["/rest/api/uom/jobs/{}"]


def test_request_get_counts_as_a_read() -> None:
    text = (
        'async def f(self):\n    return await self._request("GET", "/rest/api/uom/X")\n'
    )
    assert _paths(text) == ["/rest/api/uom/X"]


def test_helper_calls_with_literal_types_become_paths() -> None:
    text = """
async def f(self, uuid, name):
    await self.list_uom("Cluster")
    await self.get_uom("SharedStoragePool", uuid)
    await self.search_uom("VirtualIOServer", "PartitionName", name)
    await self.get_quick_property("LogicalPartition", uuid, "PartitionState")
    await self.list_child(parent_type="ManagedSystem", parent_uuid=uuid, child_type="X")
    await self.get_uom(kind, uuid)
"""
    assert sorted(_paths(text)) == [
        "/rest/api/uom/Cluster",
        "/rest/api/uom/LogicalPartition/{}/quick/PartitionState",
        "/rest/api/uom/ManagedSystem/{}/X",
        "/rest/api/uom/SharedStoragePool/{}",
        "/rest/api/uom/VirtualIOServer/search/(PartitionName=={})",
    ]


@pytest.mark.parametrize(
    ("template", "captured"),
    [
        ("/rest/api/uom/ManagedSystem/{}", True),
        ("/rest/api/uom/ManagedSystem/{}?group=Advanced", False),
        ("/rest/api/uom/LogicalPartition/search/(PartitionName=={})", True),
        ("/rest/api/uom/LogicalPartition/search/(PartitionState=={})", False),
        ("/rest/api/pcm/{}/{}?{}", True),
        ("/rest/api/uom/Written", False),
        ("/rest/api/uom/", True),
    ],
)
def test_path_is_captured(template: str, captured: bool) -> None:
    assert guard.path_is_captured(template, _evidence().endpoints) is captured


# --- CLI commands --------------------------------------------------------------------


def test_cli_templates_skip_messages() -> None:
    text = """
cmd = f"lssyscfg -r lpar -m {shlex.quote(s)} -F name"
msg = f"lsmemopt row {i} is missing"
"""
    assert [c for c, _ in guard.cli_templates("x.py", text)] == [
        "lssyscfg -r lpar -m {} -F name"
    ]


@pytest.mark.parametrize(
    ("template", "captured"),
    [
        ("lssyscfg -r lpar -m {} -F lpar_env", True),
        ("lssyscfg -r lpar -F uuid,name", False),  # no -m: a different, failing form
        ("lssyscfg -r prof -m {}", False),
        ("lshwres -r sriov --rsubtype physport -m {} --level {}", True),
        ("lshwres -r sriov --rsubtype physport -m {} --level roce", False),
        ("lshwres -r sriov --rsubtype physport -m {}", False),
        ("lsfoo -x", False),
    ],
)
def test_command_is_captured(template: str, captured: bool) -> None:
    assert guard.command_is_captured(template, _evidence().commands) is captured


# --- allowlist and driver ---------------------------------------------------------


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    directory = tmp_path / guard.VOCABULARY_DIR
    directory.mkdir(parents=True)
    (directory / "vx-p9.json").write_text(json.dumps(VOCABULARY))
    (directory / "enums-vx.json").write_text(json.dumps(ENUMS))
    (tmp_path / "src" / "hmcpctl").mkdir(parents=True)
    (tmp_path / "src" / "hmcpctl" / "m.py").write_text(
        "async def f(self, u):\n"
        '    return await self._get(f"/rest/api/uom/Missing/{u}")\n'
    )
    (tmp_path / "tests" / "t.py").write_text('d = {"PartitionState": "Running"}\n')
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    return tmp_path


def test_violations_fail_and_are_named(repo: Path, capsys) -> None:
    assert guard.main(["--root", str(repo)]) == 1
    err = capsys.readouterr().err
    assert "tests/t.py:1: PartitionState='Running'" in err
    assert (
        "src/hmcpctl/m.py:2: GET /rest/api/uom/Missing/{} has no captured endpoint"
        in err
    )


def test_generated_allowlist_passes_and_keeps_reasons(repo: Path, capsys) -> None:
    assert guard.main(["--root", str(repo), "--write-allowlist"]) == 0
    allowlist = repo / guard.ALLOWLIST
    entries = json.loads(allowlist.read_text())
    assert all("#1202" in e["reason"] for e in entries)
    entries[0]["reason"] = "hand-written reason #1202"
    allowlist.write_text(json.dumps(entries))
    assert guard.main(["--root", str(repo)]) == 0
    assert guard.main(["--root", str(repo), "--write-allowlist"]) == 0
    assert json.loads(allowlist.read_text())[0]["reason"] == "hand-written reason #1202"


def test_stale_entry_fails(repo: Path, capsys) -> None:
    guard.main(["--root", str(repo), "--write-allowlist"])
    (repo / "tests" / "t.py").write_text('d = {"PartitionState": "running"}\n')
    assert guard.main(["--root", str(repo)]) == 1
    assert "stale entry literal tests/t.py 'PartitionState'" in capsys.readouterr().err


def test_entry_without_issue_reason_fails(repo: Path, capsys) -> None:
    guard.main(["--root", str(repo), "--write-allowlist"])
    allowlist = repo / guard.ALLOWLIST
    entries = json.loads(allowlist.read_text())
    entries[0]["reason"] = "later"
    allowlist.write_text(json.dumps(entries))
    assert guard.main(["--root", str(repo)]) == 1
    assert "has no reason citing #1202" in capsys.readouterr().err


def test_live_fixtures_are_not_scanned(repo: Path) -> None:
    live = repo / "tests" / "fixtures" / "live" / "rest-x.json"
    live.write_text(json.dumps({"body": "<PartitionState>Weird</PartitionState>"}))
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    files = guard.tracked_files(repo)
    found = guard.collect_violations(repo, files, guard.load_evidence(repo))
    assert all(not v.file.startswith("tests/fixtures/live/") for v in found)


def test_passes_on_the_committed_tree(capsys) -> None:
    """The committed vocabularies and allowlist agree with the tree; exit 0."""
    assert guard.main([]) == 0
