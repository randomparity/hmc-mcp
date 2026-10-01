"""Tests for the capture exporter (scripts/live_capture_export.py, issue #1202).

Each tokenizing rule has a test that fails when the rule is removed, including the four
regressions the 2026-09-30 export hit: a collected name corrupting the `name`
attribute, the www.ibm.com namespace rewritten as a lab host, the enum value `Unknown`
tokenized, and an SSH key comment leaking a host.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import pytest

MODULE_PATH = Path(__file__).parents[2] / "scripts" / "live_capture_export.py"
MODULE_SPEC = importlib.util.spec_from_file_location("live_capture_export", MODULE_PATH)
assert MODULE_SPEC is not None
assert MODULE_SPEC.loader is not None
export = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(export)

SYSTEM = "labsys42"
LPAR = "labsys42-lp3"
HMC = "hmcbox7.example.lab"
LPAR_UUID = "3A1B2C3D-0000-4000-8000-0000000000AA"

ENUMS_XSD = """<?xml version="1.0"?>
<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <xsd:simpleType name="LogicalPartitionState.Enum">
    <xsd:restriction base="xsd:string">
      <xsd:enumeration value="running"/>
      <xsd:enumeration value="not activated"/>
    </xsd:restriction>
  </xsd:simpleType>
  <xsd:simpleType name="BootMode.Enum">
    <xsd:restriction base="xsd:string">
      <xsd:enumeration value="Normal"/>
      <xsd:enumeration value="Unknown"/>
    </xsd:restriction>
  </xsd:simpleType>
  <xsd:element name="DesignatedBootMode">
    <xsd:complexType><xsd:simpleContent>
      <xsd:extension base="BootMode.Enum"/>
    </xsd:simpleContent></xsd:complexType>
  </xsd:element>
</xsd:schema>
"""


def _lpar_body(description: str = "Unknown", extra: str = "") -> str:
    return (
        '<entry xmlns="http://www.w3.org/2005/Atom">'
        f'<link rel="SELF" href="https://{HMC}:12443/rest/api/uom/LogicalPartition/{LPAR_UUID}"/>'
        "<LogicalPartition:LogicalPartition xmlns:LogicalPartition="
        '"http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/" '
        'xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">'
        f'<PartitionName kb="CUR">{LPAR}</PartitionName>'
        '<PartitionState kb="ROO">running</PartitionState>'
        f"<Description>{description}</Description>"
        "<BootMode>Unknown</BootMode>"
        f"{extra}"
        "</LogicalPartition:LogicalPartition></entry>"
    )


def _rest(
    path: str, body: str, *, status: int = 200, step: str = "s"
) -> dict[str, Any]:
    return {
        "kind": "rest",
        "step": step,
        "method": "GET",
        "path": path,
        "accept": "application/vnd.ibm.powervm.uom+xml; type=LogicalPartition",
        "status": status,
        "response_headers": {"Content-Type": "application/xml"},
        "body": body,
    }


def _ssh(command: str, stdout: str, exit_status: int = 0) -> dict[str, Any]:
    return {
        "kind": "ssh",
        "step": "c",
        "command": command,
        "exit_status": exit_status,
        "stdout": stdout,
        "stderr": "",
    }


def _records(*extra: dict[str, Any]) -> list[dict[str, Any]]:
    base = [
        _rest("/rest/api/web/schema/inc/Enumerations.xsd", ENUMS_XSD),
        _rest(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}", _lpar_body()),
        _rest(
            f"/rest/api/uom/ManagedSystem/search/(SystemName=={SYSTEM})",
            f"<SystemName>{SYSTEM}</SystemName>",
        ),
        _ssh(
            f"lssyscfg -r lpar -m {SYSTEM} -F name,state --header",
            f"name,state\n{LPAR},Running\n",
        ),
        *extra,
    ]
    for number, record in enumerate(base, 1):
        record["_src"] = f"cap.jsonl#{number}"
    return base


def _text(corpus: list[dict[str, Any]]) -> str:
    return json.dumps(corpus)


def test_collected_names_become_stable_tokens() -> None:
    corpus = export.tokenize_records(_records())
    text = _text(corpus)
    assert SYSTEM not in text
    assert LPAR not in text
    # A partition named after its system keeps the system token and its suffix.
    assert "sys-R1-lp3" in text
    assert corpus[3]["command"] == "lssyscfg -r lpar -m sys-R1 -F name,state --header"


def test_name_attribute_survives_a_value_spelled_name() -> None:
    """Regression: a value spelled `name` was collected and rewrote every `name=`."""
    corpus = export.tokenize_records(
        _records(_rest("/d", "<Description>name</Description>"))
    )
    xsd = corpus[0]["body"]
    assert '<xsd:element name="DesignatedBootMode">' in xsd
    assert 'simpleType name="BootMode.Enum"' in xsd


def test_structural_identifiers_are_never_names() -> None:
    record = _rest("/x", "<Description>PartitionState</Description>")
    corpus = export.tokenize_records(_records(record))
    assert "<PartitionState kb=" in corpus[1]["body"]


def test_enum_value_unknown_is_not_tokenized() -> None:
    """Regression: `Unknown` was collected from a Description and rewrote every enum."""
    corpus = export.tokenize_records(_records())
    assert "<BootMode>Unknown</BootMode>" in corpus[1]["body"]
    assert "<Description>Unknown</Description>" in corpus[1]["body"]
    assert '<xsd:enumeration value="Unknown"/>' in corpus[0]["body"]


def test_ibm_and_w3_namespaces_are_preserved() -> None:
    """Regression: www.ibm.com in an XML namespace was rewritten as a lab host."""
    body = _lpar_body(extra="<Note>see host build1.dev.ibm.com</Note>")
    corpus = export.tokenize_records(_records(_rest("/y", body)))
    out = corpus[-1]["body"]
    assert "http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/" in out
    assert 'xmlns="http://www.w3.org/2005/Atom"' in out
    assert "build1.dev.ibm.com" not in out
    assert "hmc.test" in out


def test_https_hosts_and_ports_become_the_test_host() -> None:
    corpus = export.tokenize_records(_records())
    assert HMC not in _text(corpus)
    assert "https://hmc.test:443/rest/api/uom/LogicalPartition/" in corpus[1]["body"]


def test_replacement_is_whole_word() -> None:
    body = (
        f"<PartitionName>{SYSTEM}</PartitionName><Other>{SYSTEM}x and x{SYSTEM}</Other>"
    )
    corpus = export.tokenize_records(_records(_rest("/z", body)))
    assert f"{SYSTEM}x and x{SYSTEM}" in corpus[-1]["body"]


def test_ssh_key_comment_does_not_leak_a_host() -> None:
    """Regression: the comment after an SSH key body carried user@lab-host."""
    key = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC7 admin@hmcbox7.example.lab"
    corpus = export.tokenize_records(_records(_ssh("lsusr", f"{key}\n")))
    out = corpus[-1]["stdout"]
    assert out == "ssh-rsa <REDACTED-SSHKEY>\n"


def test_ssh_key_element_is_redacted_and_escaped() -> None:
    body = (
        "<PublicSSHKeyValue>ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB</PublicSSHKeyValue>"
    )
    corpus = export.tokenize_records(_records(_rest("/k", body)))
    assert "&lt;REDACTED-SSHKEY&gt;" in corpus[-1]["body"]
    assert "AAAAC3" not in corpus[-1]["body"]


@pytest.mark.parametrize(
    ("raw", "gone", "token"),
    [
        (
            "{accept=x, x-api-session=AbC123xyz, host=h}",
            "AbC123xyz",
            "<REDACTED-SESSION>",
        ),
        ("cookie=JSESSIONID=s3cr3t; CCFWSESSION=t0k3n", "s3cr3t", "<REDACTED-COOKIE>"),
        ("mac 0a:1b:2c:3d:4e:5f here", "0a:1b:2c:3d:4e:5f", "00:00:00:00:00:00"),
        (
            "mac_addr=0A1B2C3D4E5F",  # pragma: allowlist secret
            "0A1B2C3D4E5F",  # pragma: allowlist secret
            "mac_addr=000000000000",
        ),
        (
            "wwpns=c0507609abcd0001,c0507609abcd0002",
            "c0507609abcd0001",
            "c050760000000000",
        ),
        ("8375-42A*1234ABC", "1234ABC", "<REDACTED-SERIAL>"),
        ("serial_num=1234ABC", "1234ABC", "serial_num=<REDACTED-DEVID>"),
        ("at U78D2.001.WZS01AB-P1-C2", "WZS01AB", "at <REDACTED-LOC>-P1-C2"),
        ("1eU8375.42A.ABCD123-V100-C3", "ABCD123", "1e<REDACTED-LOC>-V100-C3"),
        (
            "unique_id=3E21360050768,udid=AB12",
            "3E21360050768",  # pragma: allowlist secret
            "unique_id=<REDACTED-DEVID>",
        ),
        (
            "udid=AB12CD34EF,x=1",  # pragma: allowlist secret
            "AB12CD34EF",  # pragma: allowlist secret
            "udid=<REDACTED-DEVID>,x=1",
        ),
        ("v6 2001:0:0:0:0:0:0:78 end", "2001:0:0", "v6 2001:db8::1 end"),
        ("from 10.20.30.40 ok", "10.20.30.40", "192.0.2.1"),
        ("from fe80::1a2b:3c4d ok", "fe80::1a2b", "2001:db8::1"),
        ("mail ops@example.org", "ops@example.org", "user@example.test"),
    ],
)
def test_identifier_rules(raw: str, gone: str, token: str) -> None:
    corpus = export.tokenize_records(_records(_ssh("lshmc -V", raw)))
    out = corpus[-1]["stdout"]
    assert gone not in out
    assert token in out


@pytest.mark.parametrize(
    ("element", "value", "token"),
    [
        ("MACAddress", "0A1B2C3D4E5F", "000000000000"),  # pragma: allowlist secret
        ("SerialNumber", "1234ABC", "&lt;REDACTED-SERIAL&gt;"),
        ("WWPN", "c0507609abcd0001", "&lt;REDACTED-DEVID&gt;"),
        ("VolumeUniqueID", "3E213600507680", "&lt;REDACTED-DEVID&gt;"),
        ("MediaUDID", "lab7rh8", "&lt;REDACTED-DEVID&gt;"),
        ("IPAddress", "lab-console-7", "192.0.2.1"),
        ("IPv6Address", "fe80::1", "192.0.2.1"),
        ("NetworkAddress", "10.1.2.3", "192.0.2.1"),
        ("X-API-Session", "Zm9vYmFy", "&lt;REDACTED-SESSION&gt;"),
    ],
)
def test_identifier_elements(element: str, value: str, token: str) -> None:
    body = f"<{element}>{value}</{element}>"
    corpus = export.tokenize_records(_records(_rest("/e", body)))
    assert corpus[-1]["body"] == f"<{element}>{token}</{element}>"


def test_uuid_tokens_keep_case_and_are_consistent() -> None:
    corpus = export.tokenize_records(_records(_ssh("lssyscfg", LPAR_UUID.lower())))
    upper = corpus[1]["path"].rsplit("/", 1)[1]
    assert upper == "00000001-ABCD-4EF0-8ABC-000000000001"
    assert corpus[-1]["stdout"] == upper.lower()


def test_private_pattern_is_replaced() -> None:
    corpus = export.tokenize_records(
        _records(_ssh("lshmc -n", "lab ROOM-7 rack")), private=[r"ROOM-\d"]
    )
    assert "ROOM-7" not in _text(corpus)


def test_leak_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rule that misses is caught by the scan, and nothing is returned."""
    monkeypatch.setattr(export, "IDENTIFIER_RULES", ())
    with pytest.raises(export.LeakError):
        export.tokenize_records(_records(_ssh("lshmc -n", "ip 10.1.2.3")))


def test_surviving_name_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export.Tokenizer, "_word", lambda self, m: m.group(0))
    with pytest.raises(export.LeakError):
        export.tokenize_records(_records())


def test_tool_records_are_tokenized() -> None:
    tool = {
        "kind": "tool",
        "step": "hmc_get_lpar",
        "tool": "hmc_get_lpar",
        "args": {"lpar_name_or_uuid": LPAR},
        "ok": True,
        "data": {"PartitionName": LPAR, "UUID": LPAR_UUID},
    }
    corpus = export.tokenize_records(_records(tool))
    assert corpus[-1]["record"]["args"] == {"lpar_name_or_uuid": "sys-R1-lp3"}
    assert LPAR_UUID not in _text(corpus)


def test_cli_rows_parse_header_and_native_forms() -> None:
    assert export.cli_rows("ls -F a,b", "1,2\n") == [{"a": "1", "b": "2"}]
    assert export.cli_rows("ls -F a,b --header", 'a,b\n1,"x,y"\n') == [
        {"a": "1", "b": "x,y"}
    ]
    assert export.cli_rows("ls -F --header", "a,b\n1,2\n") == [{"a": "1", "b": "2"}]
    assert export.cli_rows("ls -r lpar", 'name=x,"io=1,2",state=Running\n') == [
        {"name": "x", "io": "1,2", "state": "Running"}
    ]


# --- vocabulary --------------------------------------------------------------


def _corpus() -> list[dict[str, Any]]:
    return export.tokenize_records(
        _records(
            _rest(
                "/rest/api/uom/LogicalPartition",
                "<PartitionState>not activated</PartitionState>",
            ),
            _ssh(f"lssyscfg -r lpar -m {SYSTEM} --filter lpar_names=zz -F name", "", 1),
            _rest(
                "/rest/api/uom/LogicalPartition/search/(PartitionState==x)",
                "<entry><title>HttpErrorResponse</title><ReasonCode>Unknown internal error."
                "</ReasonCode><Message>Unable to parse expression.</Message></entry>",
                status=500,
            ),
        )
    )


def test_enums_are_derived_from_the_captured_schema() -> None:
    enums = export.derive_enums(_corpus(), "vX")
    assert enums["types"]["BootMode.Enum"] == ["Normal", "Unknown"]
    assert enums["elements"] == {"DesignatedBootMode": "BootMode.Enum"}


def test_enums_refuse_a_corpus_without_the_schema() -> None:
    with pytest.raises(ValueError, match="Enumerations.xsd"):
        export.derive_enums(_corpus()[1:], "vX")


def test_vocabulary_keeps_values_shapes_and_bindings() -> None:
    corpus = _corpus()
    vocab = export.build_vocabulary(
        corpus, export.derive_enums(corpus, "vX"), "vX", "test"
    )
    values = vocab["rest"]["values"]
    assert values["PartitionState"] == ["not activated", "running"]
    assert values["PartitionName"] == ["<text>"]
    assert values["Description"] == ["<text>"]
    assert export.observed("LastActivatedProfile", "test") == "<text>"
    assert export.observed("AssociatedTaskRole", "hmcsuperadmin") == "<text>"
    assert export.observed("lpar_proc_compat_mode", "default") == "default"
    assert (
        vocab["rest"]["element_enums"]["PartitionState"] == "LogicalPartitionState.Enum"
    )
    paths = {e["path"] for e in vocab["rest"]["endpoints"]}
    assert "/rest/api/uom/LogicalPartition/{uuid}" in paths
    assert "/rest/api/uom/ManagedSystem/search/(SystemName=={value})" in paths
    assert vocab["rest"]["errors"][0]["message"] == "Unable to parse expression."
    assert vocab["cli"]["values"]["lssyscfg -r lpar :: state"] == ["Running"]
    assert vocab["cli"]["values"]["lssyscfg -r lpar :: name"] == ["<text>"]
    templates = {c["command"]: c for c in vocab["cli"]["commands"]}
    assert templates["lssyscfg -r lpar -m {} --filter lpar_names={} -F name"][
        "exit_status"
    ] == [1]
    assert "sys-R1" not in json.dumps(vocab)


def test_ambiguous_enum_binding_is_left_unbound() -> None:
    types = {"A.Enum": ["x", "y"], "B.Enum": ["x", "z"]}
    assert export._bound_enum("Thing", {"x"}, types) is None
    assert export._bound_enum("Thing", {"x", "y"}, types) == "A.Enum"
    assert export._bound_enum("B", {"x"}, types) == "B.Enum"


def test_one_literal_binds_only_a_same_named_enum() -> None:
    """`Status` seen once as OPERATIONAL must not bind to a VNIC status enum."""
    types = {"VirtualNICBackingDeviceStatus.Enum": ["OPERATIONAL", "DOWN"]}
    assert export._bound_enum("Status", {"OPERATIONAL"}, types) is None
    assert export._bound_enum("Status", {"OPERATIONAL", "DOWN"}, types) is not None


def test_sentinel_output_is_kept_only_from_nameless_commands() -> None:
    assert export._sentinel("lsviosbk -F name,type", "No results were found.\n") is None
    assert export._sentinel("lsviosbk -F type", "No results were found.\n") == (
        "No results were found."
    )
    assert export._sentinel("lshmc -V", "a\nb") is None


def test_path_template_keeps_structure() -> None:
    assert (
        export.path_template(
            "/rest/api/pcm/ManagedSystem/00000001-abcd-4ef0-8abc-000000000001/"
            "ProcessedMetrics?StartTS=2026-10-01T00%3A51%3A31Z"
        )
        == "/rest/api/pcm/ManagedSystem/{uuid}/ProcessedMetrics?StartTS={value}"
    )
    assert (
        export.path_template("/rest/api/uom/VirtualIOServer/x-1?group=ViosSCSIMapping")
        == "/rest/api/uom/VirtualIOServer/{value}?group=ViosSCSIMapping"
    )


def test_derived_output_is_scanned(tmp_path: Path) -> None:
    out = tmp_path / "v.json"
    with pytest.raises(export.LeakError):
        export._write_scanned({"x": "10.9.8.7"}, out, ())
    assert not out.exists()


def test_cli_round_trip(tmp_path: Path) -> None:
    raw = tmp_path / "sweep.capture.jsonl"
    raw.write_text(
        "\n".join(
            json.dumps({k: v for k, v in r.items() if k != "_src"}) for r in _records()
        ),
        encoding="utf-8",
    )
    corpus = tmp_path / "corpus.json"
    enums = tmp_path / "enums.json"
    vocab = tmp_path / "vocab.json"
    assert export.main(["tokenize", str(raw), "--out", str(corpus)]) == 0
    assert oct(corpus.stat().st_mode & 0o777) == "0o600"
    assert (
        export.main(["enums", str(corpus), "--firmware", "vX", "--out", str(enums)])
        == 0
    )
    args = ["vocabulary", str(corpus), "--enums", str(enums), "--firmware", "vX"]
    assert export.main([*args, "--source", "t", "--out", str(vocab)]) == 0
    assert json.loads(vocab.read_text())["firmware"] == "vX"


def test_cli_reports_a_leak_without_writing(tmp_path: Path, capsys) -> None:
    raw = tmp_path / "sweep.capture.jsonl"
    raw.write_text(json.dumps(_ssh("lshmc", "in ROOMX7")) + "\n", encoding="utf-8")
    out = tmp_path / "corpus.json"
    assert (
        export.main(["tokenize", str(raw), "--out", str(out), "--private", r"ROOMX\d"])
        == 0
    )
    assert "ROOMX7" not in out.read_text()
    raw.write_text(json.dumps(_ssh("lshmc", "10.1.1.1")) + "\n", encoding="utf-8")
    real = export.IDENTIFIER_RULES
    try:
        export.IDENTIFIER_RULES = ()
        assert (
            export.main(["tokenize", str(raw), "--out", str(tmp_path / "c2.json")]) == 1
        )
    finally:
        export.IDENTIFIER_RULES = real
    assert not (tmp_path / "c2.json").exists()
    assert "nothing written" in capsys.readouterr().err


def test_hmc_sentinels_are_never_names() -> None:
    """Regression: `No results were found.` was collected as a name and tokenized."""
    records = _records(
        _ssh("lsviosbk -F name,type", "No results were found.\n"),
        _ssh("lssyscfg -r lpar -F name", "HSCL8012 The partition was not found\n", 1),
        _rest(
            "/n",
            "<Description>N/A</Description><PartitionName>unavailable</PartitionName>",
        ),
    )
    corpus = export.tokenize_records(records)
    assert corpus[-3]["stdout"] == "No results were found.\n"
    assert corpus[-2]["stdout"] == "HSCL8012 The partition was not found\n"
    assert "<Description>N/A</Description>" in corpus[-1]["body"]
    assert "<PartitionName>unavailable</PartitionName>" in corpus[-1]["body"]


def test_colon_positional_records_are_not_ipv6() -> None:
    """SR-IOV and slot records are colon-separated short fields, not addresses."""
    corpus = export.tokenize_records(
        _records(_ssh("lshwres -r sriov", "1:0:1:3:0:5:1\n"))
    )
    assert corpus[-1]["stdout"] == "1:0:1:3:0:5:1\n"


def test_replacements_never_put_a_digit_after_a_group_reference() -> None:
    """Regression: `\\1000000000000` is an octal escape and ate the <MACAddress> tag."""
    for _, replacement in (*export.SECRET_RULES, *export.IDENTIFIER_RULES):
        if isinstance(replacement, str):
            assert not re.search(r"\\\d\d", replacement), replacement


def test_authorized_keys_element_is_redacted_whole() -> None:
    """Regression: the key-comment match ate `</AuthorizedKeysValue>`."""
    body = (
        "<ManagementConsole><AuthorizedKeysValue>ssh-rsa AAAAB3NzaC1yc2EAAAADAQAB "
        "root@hmcbox7</AuthorizedKeysValue></ManagementConsole>"
    )
    corpus = export.tokenize_records(_records(_rest("/mc", body)))
    out = corpus[-1]["body"]
    assert out == (
        "<ManagementConsole><AuthorizedKeysValue>&lt;REDACTED-SSHKEY&gt;"
        "</AuthorizedKeysValue></ManagementConsole>"
    )


def test_ssh_key_comment_stops_at_a_tag() -> None:
    body = "<Keys><Key>ssh-rsa AAAAB3NzaC1yc2EAAAADAQAB root@hmcbox7</Key></Keys>"
    corpus = export.tokenize_records(_records(_rest("/k2", body)))
    assert (
        corpus[-1]["body"] == "<Keys><Key>ssh-rsa &lt;REDACTED-SSHKEY&gt;</Key></Keys>"
    )


def test_broken_xml_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rule that corrupts a body that parsed is caught, and nothing is returned."""
    rules = ((re.compile(r"<MACAddress>"), ""), *export.IDENTIFIER_RULES)
    monkeypatch.setattr(export, "IDENTIFIER_RULES", rules)
    body = "<Adapter><MACAddress>0A1B2C3D4E5F</MACAddress></Adapter>"  # pragma: allowlist secret
    with pytest.raises(export.BrokenBodyError):
        export.tokenize_records(_records(_rest("/m", body)))


def test_surviving_location_code_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export, "IDENTIFIER_RULES", ())
    with pytest.raises(export.LeakError):
        export.tokenize_records(
            _records(_ssh("lshwres", "drc 1eU8375.42A.ABCD123-V100-C3"))
        )


def test_cli_error_streams_are_recorded() -> None:
    """An rc=1 HMC error carries its HSCL text on stdout, with stderr empty."""
    corpus = _corpus()
    vocab = export.build_vocabulary(corpus, {"types": {}}, "vX", ["t"])
    (failed,) = [c for c in vocab["cli"]["commands"] if c["exit_status"] == [1]]
    assert failed["error_streams"] == []
    corpus.append(
        {
            "kind": "ssh",
            "command": "lssyscfg -r lpar -F uuid,name",
            "exit_status": 1,
            "stdout": "HSCL1234 An invalid parameter was entered.",
            "stderr": "",
        }
    )
    vocab = export.build_vocabulary(corpus, {"types": {}}, "vX", ["t"])
    entry = {c["command"]: c for c in vocab["cli"]["commands"]}[
        "lssyscfg -r lpar -F uuid,name"
    ]
    assert entry["error_streams"] == ["stdout"]
    assert entry["codes"] == ["HSCL1234"]


def test_derived_vocabulary_is_folded_in() -> None:
    derived = {
        "rest_values": {
            "Status": {"COMPLETED_OK": 3, "<text>": 1},
            "JobName": {"x1": 2},
        },
        "rest_endpoints": [["GET", "/rest/api/uom/jobs/{n}", 200, 4]],
    }
    vocab = export.build_vocabulary(
        _corpus(), {"types": {}}, "vX", ["a", "b"], [derived]
    )
    assert vocab["sources"] == ["a", "b"]
    assert vocab["rest"]["values"]["Status"] == ["<text>", "COMPLETED_OK"]
    assert vocab["rest"]["values"]["JobName"] == ["<text>"]
    paths = {e["path"] for e in vocab["rest"]["endpoints"]}
    assert "/rest/api/uom/jobs/{value}" in paths


def test_fold_needs_a_source_each(tmp_path: Path, capsys) -> None:
    derived = tmp_path / "d.json"
    derived.write_text("{}")
    corpus = tmp_path / "c.json"
    corpus.write_text("[]")
    enums = tmp_path / "e.json"
    enums.write_text('{"types": {}}')
    args = ["vocabulary", str(corpus), "--enums", str(enums), "--firmware", "vX"]
    args += ["--source", "s", "--fold", str(derived), "--out", str(tmp_path / "v.json")]
    assert export.main(args) == 1
    assert "--fold-source" in capsys.readouterr().err


def test_location_suffix_wwn_is_redacted_and_lun_kept() -> None:
    """A `-L<hex>` suffix segment carries a disk WWN or RAID array id; `-L0` stays."""
    line = "U78D2.001.ABCD123-P1-C49-L5000C50098A124EF-L0\n"  # pragma: allowlist secret
    corpus = export.tokenize_records(_records(_ssh("lshwres -r io", line)))
    assert corpus[-1]["stdout"] == "<REDACTED-LOC>-P1-C49-L<REDACTED-DEVID>-L0\n"


def test_surviving_location_suffix_wwn_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rules = tuple(r for r in export.IDENTIFIER_RULES if "L[0-9A-F]" not in r[0].pattern)
    monkeypatch.setattr(export, "IDENTIFIER_RULES", rules)
    with pytest.raises(export.LeakError):
        export.tokenize_records(_records(_ssh("lshwres", "x-P1-L5000C50098A124EF-L0")))


@pytest.mark.parametrize(
    "element", ["UserID", "BMCConnectionUserName", "UserDescription"]
)
def test_user_identity_elements_are_names(element: str) -> None:
    """Regression: a real HMC user id survived in UserProfile bodies."""
    body = f'<UserProfile><{element} ksv="V1_0">opsadmin7</{element}></UserProfile>'
    tool = {
        "kind": "tool",
        "step": "u",
        "tool": "hmc_list_users",
        "ok": True,
        "data": [{element: "opsadmin7"}],
    }
    corpus = export.tokenize_records(_records(_rest("/u", body), tool))
    assert "opsadmin7" not in _text(corpus)


def test_built_in_accounts_are_not_names() -> None:
    """Regression: UserID `root` was tokenized and rewrote an HMC error message."""
    body = "<UserProfile><UserID>root</UserID></UserProfile>"
    message = "<Message>REST000E Unrecognized root REST type of Job.</Message>"
    corpus = export.tokenize_records(_records(_rest("/u", body), _rest("/j", message)))
    assert corpus[-1]["body"] == message
    for account in ("hscroot", "hscpe", "admin"):
        body = f"<UserProfile><UserID>{account}</UserID></UserProfile>"
        corpus = export.tokenize_records(_records(_rest("/u", body)))
        assert corpus[-1]["body"] == body
