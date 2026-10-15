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
import time
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
            "c0507609abcd0002",
            "wwpns=<REDACTED-DEVID>,<REDACTED-DEVID>",
        ),
        (
            'x,"wwpns=c0507609abcd0001,c0507609abcd0002",y',
            "c0507609abcd0002",
            'x,"wwpns=<REDACTED-DEVID>",y',
        ),
        ("wwnn=c0507600000000ff", "c0507600000000ff", "wwnn=<REDACTED-DEVID>"),
        (
            "/vdevice/vfc-client@30000002/disk@5005076800000001,0",
            "5005076800000001",
            "disk@<REDACTED-DEVID>,0",
        ),
        (
            "AIX-VDASD-00C1000000000007 disk",
            "00C1000000000007",
            "AIX-VDASD-<REDACTED-DEVID>",
        ),
        ("8375-42A*1234ABC", "1234ABC", "<REDACTED-SERIAL>"),
        ("serial_num=1234ABC", "1234ABC", "serial_num=<REDACTED-DEVID>"),
        ("at U78D2.001.SYNTH01-P1-C2", "SYNTH01", "at <REDACTED-LOC>-P1-C2"),
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
        ("VirtualSerialNumber", "SYN0001", "&lt;REDACTED-SERIAL&gt;"),
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


def test_vios_mac_address_prefix_is_scrubbed() -> None:
    """Regression #1497: `MACAddress\\b` let a VIOS's `<MACAddressPrefix>` through."""
    body = (
        '<VirtualIOServer><MACAddressPrefix kb="ROR" kxe="false">'
        "0A1B2C3D4E5F</MACAddressPrefix></VirtualIOServer>"  # pragma: allowlist secret
    )
    corpus = export.tokenize_records(_records(_rest("/vios", body)))
    assert corpus[-1]["body"] == body.replace("0A1B2C3D4E5F", "000000000000")


@pytest.mark.parametrize(
    ("element", "value", "token"),
    [
        ("VirtualEthernetAdapterMACAddressPrefix", "0A1B2C", "000000"),
        ("VirtualEthernetAdapterMACAddressPrefix", "6765100", "0000000"),
        ("MacAddressValue", "0a1b2c3d4e5f", "000000000000"),  # pragma: allowlist secret
        ("AllowedMACAddresses", "0A1B2C3D4E5F,1A2B3C", "000000000000,000000"),
        ("AllowedMACAddresses", "ALL", "ALL"),
        ("AllowedMACAddresses", "NONE", "NONE"),
        ("AllowedOperatingSystemMACAddresses", "ALL", "ALL"),
        ("VirtualEthernetCustomMACAddressCapable", "true", "true"),
    ],
)
def test_mac_elements_scrub_only_hex(element: str, value: str, token: str) -> None:
    """Every MAC-bearing element is scrubbed; a keyword or boolean value survives."""
    body = f"<{element}>{value}</{element}>"
    corpus = export.tokenize_records(_records(_rest("/ms", body)))
    assert corpus[-1]["body"] == f"<{element}>{token}</{element}>"


SERIAL_ELEMENTS = ["SerialNumber", "LogicalSerialNumber", "ns:VirtualSerialNumber"]


@pytest.mark.parametrize("encode", [str, json.dumps], ids=["raw", "json"])
@pytest.mark.parametrize("element", SERIAL_ELEMENTS)
@pytest.mark.parametrize("keyword", ["none", "auto"])
def test_serial_keyword_is_kept_as_evidence(
    element: str, keyword: str, encode: Any
) -> None:
    """#1593: `none` and `auto` are what a partition has instead of a virtual serial."""
    tok = export.Tokenizer({}, set())
    body = encode(f'<Root><{element} kb="CUD">{keyword}</{element}></Root>')
    assert tok.tokenize(body) == body
    assert tok.leaks(body) == []


@pytest.mark.parametrize("encode", [str, json.dumps], ids=["raw", "json"])
@pytest.mark.parametrize("element", SERIAL_ELEMENTS)
@pytest.mark.parametrize(
    "value", ["None", "nonesuch", "none ", "autoSYN0001", "SYN0001"]
)
def test_serial_near_keyword_is_scrubbed(element: str, value: str, encode: Any) -> None:
    """Only the exact keyword is kept: anything else in a serial element is a serial."""
    tok = export.Tokenizer({}, set())
    body = encode(f"<Root><{element}>{value}</{element}></Root>")
    scrubbed = tok.tokenize(body)
    assert scrubbed == body.replace(
        value, _escaped(SERIAL) if encode is str else SERIAL
    )
    assert tok.leaks(scrubbed) == []
    assert tok.leaks(body) == [value]


@pytest.mark.parametrize("encode", [str, json.dumps], ids=["raw", "json"])
@pytest.mark.parametrize("attribute", ['kb="a>b"', "kb='a>b'"])
def test_serial_keyword_after_a_quoted_gt_is_kept(attribute: str, encode: Any) -> None:
    """A `>` inside an attribute does not end the tag the keyword is checked after."""
    tok = export.Tokenizer({}, set())
    body = encode(
        f"<Root><VirtualSerialNumber {attribute}>none</VirtualSerialNumber></Root>"
    )
    assert tok.tokenize(body) == body
    assert tok.leaks(body) == []


@pytest.mark.parametrize(
    "body",
    [
        "<VirtualSerialNumber>SYN<x>none</VirtualSerialNumber>",
        "<VirtualSerialNumber>SYN</x>auto</VirtualSerialNumber>",
        "<Root>SYN0001>none</VirtualSerialNumber></Root>",
        "<VirtualSerialNumber SYN</VirtualSerialNumber>none</VirtualSerialNumber>",
        '<VirtualSerialNumber kb="x<SYN>none</VirtualSerialNumber>',
        "<VirtualSerialNumber SYN0001/>none</VirtualSerialNumber>",
        '<VirtualSerialNumber kb="SYN0001>none</VirtualSerialNumber>',
    ],
)
def test_serial_keyword_not_after_its_opening_tag_is_a_leak(body: str) -> None:
    """A keyword is evidence only as the element's whole text, in the backstop too."""
    with pytest.raises(export.LeakError):
        export.tokenize_records(_records(_rest("/e", body)))


#: CLI serial fields that hold `none` or `auto` until a serial is assigned (#1618, #1637).
KEYWORD_SERIAL_KEYS = [
    "virtual_serial_num",
    "logical_serial_num",
    "cluster_serial_num",
    "peer_collective_serial_num",
]
#: `lssyscfg` fields around such a serial, unquoted and as a quoted CSV field.
KEYWORD_SERIAL_FIELDS = [
    "name=lpar-A1,{key}={value},serial_num=<REDACTED-DEVID>",
    'name=lpar-A1,"{key}={value}",state=Running',
    "name=lpar-A1,{key}={value}\nname=lpar-A2",
    "name=lpar-A1,{KEY}={value}",
]


def _serial_fields(fields: str, key: str, value: str) -> str:
    return fields.format(key=key, KEY=key.upper(), value=value)


@pytest.mark.parametrize("key", KEYWORD_SERIAL_KEYS)
@pytest.mark.parametrize("encode", [str, json.dumps], ids=["raw", "json"])
@pytest.mark.parametrize("fields", KEYWORD_SERIAL_FIELDS)
@pytest.mark.parametrize("value", ["SYN0001", "None", "nonesuch", "autoSYN0001"])
def test_keyword_serial_field_is_scrubbed(
    value: str, fields: str, encode: Any, key: str
) -> None:
    """#1618, #1637: `\\bserial_num` has no boundary inside these keys to match at."""
    tok = export.Tokenizer({}, set())
    body = encode(_serial_fields(fields, key, value))
    scrubbed = tok.tokenize(body)
    if encode is str:
        assert scrubbed == body.replace(value, DEVID)
    assert value not in scrubbed
    assert tok.leaks(scrubbed) == []
    assert tok.leaks(body)


@pytest.mark.parametrize("key", KEYWORD_SERIAL_KEYS)
@pytest.mark.parametrize("encode", [str, json.dumps], ids=["raw", "json"])
@pytest.mark.parametrize(
    "fields",
    [
        *KEYWORD_SERIAL_FIELDS,
        "{key}={value}",
        "x=1,{key}={value}\r\nx=1",
        "hdr\n{key}={value}",
    ],
)
@pytest.mark.parametrize("keyword", ["none", "auto"])
def test_keyword_serial_keyword_is_kept_as_evidence(
    keyword: str, fields: str, encode: Any, key: str
) -> None:
    """`none` and `auto` stand in for a serial, as in `VirtualSerialNumber`."""
    tok = export.Tokenizer({}, set())
    body = encode(_serial_fields(fields, key, keyword))
    assert tok.tokenize(body) == body
    assert tok.leaks(body) == []


@pytest.mark.parametrize("key", KEYWORD_SERIAL_KEYS)
@pytest.mark.parametrize(
    "body",
    [
        '"{key}=none,SYN0001"',
        "{key}=none SYN0001",
        "{key}=auto\\SYN0001",
        "{key}=none\rSYN0001",
    ],
)
def test_keyword_serial_keyword_is_kept_only_as_the_whole_value(
    body: str, key: str
) -> None:
    """A quoted field runs to its closing quote; anything after a keyword is a serial."""
    body = body.format(key=key)
    corpus = export.tokenize_records(_records(_ssh("lssyscfg -r lpar", body)))
    assert "SYN0001" not in corpus[-1]["stdout"]


@pytest.mark.parametrize("key", KEYWORD_SERIAL_KEYS)
@pytest.mark.parametrize(
    "body",
    [
        'name=lpar-A1,{key}=none"SYN0001",state=Running',
        '"{key}=auto"SYN0001"',
    ],
)
def test_keyword_serial_keyword_before_a_quote_in_the_value_fails_closed(
    body: str, key: str
) -> None:
    """A quote ends a value only where a field ends; the backstop sees what follows."""
    body = body.format(key=key)
    with pytest.raises(export.LeakError):
        export.tokenize_records(_records(_ssh("lssyscfg -r lpar", body)))


@pytest.mark.parametrize("key", KEYWORD_SERIAL_KEYS)
@pytest.mark.parametrize(
    "wrap", [lambda v: {"raw": v}, lambda v: [v]], ids=["{}", "[]"]
)
@pytest.mark.parametrize(
    ("value", "written"),
    [
        ("auto", "auto"),
        ("SYN0001", "<REDACTED-DEVID>"),
    ],
)
def test_keyword_serial_last_in_a_json_object_or_array_exports(
    value: str, written: str, wrap: Any, key: str
) -> None:
    """The scanned corpus closes such a string with `"}` or `"]`, not with a separator."""
    tool = {"kind": "tool", "step": "t", "tool": "t", "ok": True}
    tool["data"] = wrap(f"name=lpar-A1,{key}={value}")
    corpus = export.tokenize_records(_records(tool))
    assert corpus[-1]["record"]["data"] == wrap(f"name=lpar-A1,{key}={written}")


@pytest.mark.parametrize("key", KEYWORD_SERIAL_KEYS)
@pytest.mark.parametrize(
    "text",
    [
        "x {key}=SYN0001",
        json.dumps("x\n{key}=SYN0001"),
        "ns_{key}=SYN0001",
        "{key}=none,{key}=SYN0001",
        "{key}=<REDACTED-DEVID>SYN0001",
        '{key}="SYN0001"',
        "{key}=none\rSYN0001",
        json.dumps("x\n{key}=auto\rSYN0001"),
    ],
)
def test_keyword_serial_backstop_reports_a_value_the_rules_missed(
    text: str, key: str
) -> None:
    """The backstop needs no word boundary, which `json.dumps` text hides behind `\\n`."""
    text = text.format(key=key)
    leak = text[text.rindex(f"{key}=") :].removesuffix('"')
    assert export.Tokenizer({}, set()).leaks(text) == [leak]


@pytest.mark.parametrize("key", KEYWORD_SERIAL_KEYS)
@pytest.mark.parametrize(
    "text",
    [
        "{key}=<REDACTED-DEVID>,x=1",
        "{key}=&lt;REDACTED-DEVID&gt;",
        "{key}=,x=1",
        'x,"{key}=",y',
    ],
)
def test_keyword_serial_backstop_passes_the_token(text: str, key: str) -> None:
    """What the rules write, in a body or an XML body, and an empty value, are no leak."""
    assert export.Tokenizer({}, set()).leaks(text.format(key=key)) == []


#: The other keys the key=value rules scrub, which keep no keyword (#1638).
DEVICE_ID_KEYS = ["unique_id", "udid", "wwpn", "wwpns", "wwnn", "serial_num"]


@pytest.mark.parametrize("key", [*DEVICE_ID_KEYS, *KEYWORD_SERIAL_KEYS])
@pytest.mark.parametrize("encode", [str, json.dumps], ids=["raw", "json"])
@pytest.mark.parametrize(
    "separator",
    ["\n", "\r", "\t", "\f", "\b", "\x00", "\u2028"],
    ids=["lf", "cr", "tab", "ff", "bs", "nul", "ls"],
)
def test_key_value_field_at_a_line_start_is_scrubbed(
    separator: str, encode: Any, key: str
) -> None:
    """#1638: `\\b` reads the letter of a `json.dumps` `\\n` escape as part of the key,
    and the digits of a `\\u2028` one."""
    tok = export.Tokenizer({}, set())
    body = encode(f"hdr{separator}{key}=SYN0001")
    scrubbed = tok.tokenize(body)
    assert scrubbed == body.replace("SYN0001", DEVID)
    assert tok.leaks(scrubbed) == []
    assert tok.leaks(body)


@pytest.mark.parametrize("key", DEVICE_ID_KEYS)
@pytest.mark.parametrize("encode", [str, json.dumps], ids=["raw", "json"])
@pytest.mark.parametrize("separator", ["\n", "\r", "\t"], ids=["lf", "cr", "tab"])
@pytest.mark.parametrize("prefix", ["x", "ns_", "virtual_", "port", "lun"])
def test_key_value_key_inside_a_longer_key_is_left(
    prefix: str, separator: str, encode: Any, key: str
) -> None:
    """A key is matched after an escape, never inside a longer key such as `ns_udid`."""
    tok = export.Tokenizer({}, set())
    body = encode(f"hdr{separator}{prefix}{key}=none")
    assert tok.tokenize(body) == body
    assert tok.leaks(body) == []


@pytest.mark.parametrize("key", DEVICE_ID_KEYS)
@pytest.mark.parametrize(
    "text",
    [
        "x {key}=SYN0001",
        json.dumps("x\n{key}=SYN0001"),
        json.dumps("x\r{key}=SYN0001"),
        json.dumps("x\t{key}=SYN0001"),
        "{KEY}=SYN0001",
        "{key}=none",
        "{key}=<REDACTED-DEVID>SYN0001",
        '{key}="SYN0001"',
    ],
)
def test_device_id_backstop_reports_a_value_the_rules_missed(
    text: str, key: str
) -> None:
    """Every key the rules scrub has a backstop, at the same boundary (#1638)."""
    text = text.format(key=key, KEY=key.upper())
    start = text.lower().rindex(f"{key}=")
    assert export.Tokenizer({}, set()).leaks(text) == [text[start:].removesuffix('"')]


@pytest.mark.parametrize("key", DEVICE_ID_KEYS)
@pytest.mark.parametrize(
    "text",
    [
        "{key}=<REDACTED-DEVID>,x=1",
        "{key}=&lt;REDACTED-DEVID&gt;",
        json.dumps("x\n{key}=<REDACTED-DEVID>\n"),
        'x,"{key}=<REDACTED-DEVID>",y',
        "{key}=,x=1",
        "ns_{key}=SYN0001",
    ],
)
def test_device_id_backstop_passes_the_token(text: str, key: str) -> None:
    """What the rules write, an empty value and a longer key are no leak."""
    assert export.Tokenizer({}, set()).leaks(text.format(key=key)) == []


@pytest.mark.parametrize("key", [*DEVICE_ID_KEYS, *KEYWORD_SERIAL_KEYS])
@pytest.mark.parametrize(
    ("raw", "scrubbed"),
    [
        ('"{key}=SYN0001",x=1', '"{key}=<REDACTED-DEVID>",x=1'),
        ('x=1,"{key}=SYN0001"', 'x=1,"{key}=<REDACTED-DEVID>"'),
        ('x=1,"{key}=SYN0001\\",y', 'x=1,"{key}=<REDACTED-DEVID>\\",y'),
        ('x=1,"{key}=SYN0001\\\\"', 'x=1,"{key}=<REDACTED-DEVID>\\\\"'),
    ],
)
def test_quoted_key_value_field_stays_valid_json(
    raw: str, scrubbed: str, key: str
) -> None:
    """#1641: the rule keeps the backslashes before a closing quote, so the `\\` of
    `json.dumps`'s `\\"` stays and an escaped backslash keeps its pair."""
    tok = export.Tokenizer({}, set())
    raw, scrubbed = raw.format(key=key), scrubbed.format(key=key)
    assert tok.tokenize(raw) == scrubbed
    encoded = tok.tokenize(json.dumps(raw))
    assert encoded is not None
    assert json.loads(encoded) == scrubbed
    assert tok.leaks(json.dumps(scrubbed)) == []


@pytest.mark.parametrize("key", [*DEVICE_ID_KEYS, *KEYWORD_SERIAL_KEYS])
def test_quoted_key_value_field_in_a_tool_record_exports(key: str) -> None:
    """A tool record is scrubbed as `json.dumps` text and parsed back (#1641)."""
    tool = {"kind": "tool", "step": "t", "tool": "t", "ok": True}
    tool["data"] = f'name=lpar-A1,"{key}=SYN0001",state=Running'
    corpus = export.tokenize_records(_records(tool))
    assert corpus[-1]["record"]["data"] == (
        f'name=lpar-A1,"{key}=<REDACTED-DEVID>",state=Running'
    )


@pytest.mark.parametrize("key", [*DEVICE_ID_KEYS, *KEYWORD_SERIAL_KEYS])
@pytest.mark.parametrize("encode", [str, json.dumps], ids=["raw", "json"])
@pytest.mark.parametrize(
    "fields",
    ["x=1,{key}={value}\r\nx=2", "<a>{key}={value}</a>"],
    ids=["crlf", "close-tag"],
)
def test_unquoted_value_stops_at_a_line_end_cr_and_a_tag(
    fields: str, encode: Any, key: str
) -> None:
    """#1641: the value no longer swallows the CR of a CRLF or the tag after it."""
    tok = export.Tokenizer({}, set())
    body = encode(fields.format(key=key, value="SYN0001"))
    scrubbed = tok.tokenize(body)
    if encode is str or "\r" not in fields:
        token = "&lt;REDACTED-DEVID&gt;" if body.startswith("<") else DEVID
        assert scrubbed == body.replace("SYN0001", token)
    assert "SYN0001" not in scrubbed
    assert tok.leaks(json.dumps(scrubbed) if encode is str else scrubbed) == []


@pytest.mark.parametrize("key", [*DEVICE_ID_KEYS, *KEYWORD_SERIAL_KEYS])
@pytest.mark.parametrize(
    "body",
    [
        '<a b="{key}=SYN0001"/>',
        '<a b="{key}=SYN0001" c="d">t</a>',
        "<a>{key}=SYN0001</a>",
        '<?pi b="{key}=SYN0001"?><a/>',
    ],
)
def test_key_value_in_an_xml_body_exports(body: str, key: str) -> None:
    """#1641: the backstop passes a scrubbed value an attribute's quote or a tag ends."""
    body = body.format(key=key)
    corpus = export.tokenize_records(_records(_rest("/e", body)))
    assert corpus[-1]["body"] == body.replace("SYN0001", "&lt;REDACTED-DEVID&gt;")


@pytest.mark.parametrize("key", [*DEVICE_ID_KEYS, *KEYWORD_SERIAL_KEYS])
@pytest.mark.parametrize(
    "stdout", ['"{key}=SYN0001" x=1', '"{key}=SYN0001"\tx=1', 'a "{key}=SYN0001" b']
)
def test_quoted_field_before_a_space_or_tab_exports(stdout: str, key: str) -> None:
    """#1641: a closing quote, then a space or tab, ends a field the rule scrubbed."""
    stdout = stdout.format(key=key)
    corpus = export.tokenize_records(_records(_ssh("lssyscfg -r lpar", stdout)))
    assert corpus[-1]["stdout"] == stdout.replace("SYN0001", "<REDACTED-DEVID>")


@pytest.mark.parametrize("key", [*DEVICE_ID_KEYS, *KEYWORD_SERIAL_KEYS])
@pytest.mark.parametrize(
    "text",
    [
        '"{key}=<REDACTED-DEVID>"SYN0001',
        '"{key}=<REDACTED-DEVID>"/SYN0001',
        '"{key}=<REDACTED-DEVID>"?SYN0001',
        json.dumps('"{key}=<REDACTED-DEVID>"SYN0001'),
        "{key}=<REDACTED-DEVID><SYN0001",
        "{key}=<REDACTED-DEVID></SYN0001",
        'x=1,{key}=<REDACTED-DEVID>" SYN0001',
        json.dumps('x=1,{key}=<REDACTED-DEVID>"\tSYN0001'),
        json.dumps('x "{key}=<REDACTED-DEVID>\\" SYN0001'),
        '<a>{key}=&lt;REDACTED-DEVID&gt;">SYN0001</a>',
    ],
)
def test_backstop_reports_text_joined_after_a_closing_quote(
    text: str, key: str
) -> None:
    """Only a space, a tab or a tag's end after the quote ends the field, and only when a
    quote opened it (#1641)."""
    text = text.format(key=key)
    assert export.Tokenizer({}, set()).leaks(text)


@pytest.mark.parametrize("key", [*DEVICE_ID_KEYS, *KEYWORD_SERIAL_KEYS])
@pytest.mark.parametrize(
    "value", ['SYN0001" SYN0002', 'SYN0001"\tSYN0002', '" SYN0002"']
)
@pytest.mark.parametrize("kind", ["ssh", "tool"])
def test_quote_in_a_field_at_the_start_of_a_string_fails_closed(
    kind: str, value: str, key: str
) -> None:
    """The quote that opens a JSON string opens no field, so the text after a quote in
    the value is still read as part of it (#1641)."""
    text = f"{key}={value}"
    record = (
        _ssh("lssyscfg -r lpar", text)
        if kind == "ssh"
        else {"kind": "tool", "step": "t", "tool": "t", "ok": True, "data": text}
    )
    with pytest.raises(export.LeakError):
        export.tokenize_records(_records(record))


@pytest.mark.parametrize("key", [*DEVICE_ID_KEYS, *KEYWORD_SERIAL_KEYS])
def test_key_value_after_text_in_an_xml_attribute_fails_closed(key: str) -> None:
    """The backstop cannot tell an attribute's closing quote from one in the value, so a
    field that no quote opened fails the export rather than pass the text after it."""
    body = f'<a b="x {key}=SYN0001" c="d"/>'
    with pytest.raises(export.LeakError):
        export.tokenize_records(_records(_rest("/e", body)))


@pytest.mark.parametrize(
    "element",
    [
        "PublicSSHKeyValue",
        "AuthorizedKeysValue",
        "X-API-Session",
        "IPAddress",
        "WWPN",
        "SerialNumber",
        "LogicalSerialNumber",
        "MACAddress",
    ],
)
@pytest.mark.parametrize("closer", ["/>", " />", ' kb="a/b"/>'])
def test_self_closing_element_keeps_the_text_after_it(
    element: str, closer: str
) -> None:
    """Regression #1525: `[^>]*>` took the `/` and the rule rewrote the next text node."""
    body = f"<Root><{element}{closer}\n  <Next>x</Next></Root>"
    corpus = export.tokenize_records(_records(_rest("/e", body)))
    assert corpus[-1]["body"] == body


@pytest.mark.parametrize(
    "attribute", ['href="a/"', 'kb="x/>"', "kb='x/>'", 'kb="x', 'kb="a<b"']
)
def test_valued_element_with_a_slash_attribute_is_still_scrubbed(
    attribute: str,
) -> None:
    """A `/>` inside a quoted attribute value does not make the tag self-closing."""
    body = f"<SerialNumber {attribute}>1234ABC</SerialNumber>"
    corpus = export.tokenize_records(_records(_rest("/e", body)))
    assert corpus[-1]["body"] == body.replace("1234ABC", "&lt;REDACTED-SERIAL&gt;")


def test_self_closing_element_with_a_quoted_gt_fails_closed() -> None:
    """The malformed-tag fallback ends `kb=">"/>` at its first `>`: the export stops."""
    body = '<Root><SerialNumber kb=">"/>\n  <Next>x</Next></Root>'
    with pytest.raises(export.BrokenBodyError):
        export.tokenize_records(_records(_rest("/e", body)))


MALFORMED_SLASH_TAGS = [
    '<SerialNumber kb="x/>1234ABC</SerialNumber>',
    '<SerialNumber kb="a<b/>c">1234ABC</SerialNumber>',
]


@pytest.mark.parametrize("body", MALFORMED_SLASH_TAGS)
def test_malformed_tag_with_a_slash_before_its_first_gt_is_scrubbed(body: str) -> None:
    """Regression #1548: the fallback refused a first `>` after a `/` and kept the value."""
    corpus = export.tokenize_records(_records(_rest("/e", body)))
    assert "1234ABC" not in corpus[-1]["body"]


@pytest.mark.parametrize("raw", MALFORMED_SLASH_TAGS)
def test_malformed_slash_tag_is_scrubbed_in_json_encoded_text(raw: str) -> None:
    """Tool records are scrubbed as `json.dumps` text, where every quote is `\\"`."""
    tool = {"kind": "tool", "step": "t", "tool": "t", "ok": True, "data": {"raw": raw}}
    corpus = export.tokenize_records(_records(tool))
    assert "1234ABC" not in corpus[-1]["record"]["data"]["raw"]


@pytest.mark.parametrize("attribute", ['kb="a>b"', 'kb="x/>"', "kb='a>b/>'"])
def test_quote_aware_branch_is_tried_before_the_fallback(attribute: str) -> None:
    """A `>` inside a quoted value: only the quote-aware branch ends the tag after it."""
    body = f"<Root><SerialNumber {attribute}>1234ABC</SerialNumber></Root>"
    corpus = export.tokenize_records(_records(_rest("/e", body)))
    assert corpus[-1]["body"] == body.replace("1234ABC", "&lt;REDACTED-SERIAL&gt;")


SESSION = "<REDACTED-SESSION>"
SSH_KEY = "<REDACTED-SSHKEY>"
SERIAL = "<REDACTED-SERIAL>"
DEVID = "<REDACTED-DEVID>"
ADDRESS = export.PLACEHOLDER_IP
#: One element of each scrubbed set, with the text its rule writes (#1562, #1588).
SCRUBBED_ELEMENTS = [
    ("X-API-Session", SESSION),
    ("x-api-session", SESSION),
    ("PublicSSHKeyValue", SSH_KEY),
    ("ns:AuthorizedKeysValue", SSH_KEY),
    ("IPAddress", ADDRESS),
    ("ns:PrimaryIPAddress", ADDRESS),
    ("SerialNumber", SERIAL),
    ("ns:LogicalSerialNumber", SERIAL),
    ("VirtualSerialNumber", SERIAL),
    ("UniqueDeviceID", DEVID),
    ("ns:WWPN", DEVID),
    ("WWPNPrefix", DEVID),
]
#: Names the scrub rules matched only in their declared case and after a `\w+:` prefix
#: (#1572, #1588), or after no prefix at all: neither may hide the value.
VARIANT_ELEMENTS = [
    ("ns-1:x-api-session", SESSION),
    ("ns.v1:X-API-SESSION", SESSION),
    ("publicsshkeyvalue", SSH_KEY),
    ("ns-1:AuthorizedKeysValue", SSH_KEY),
    ("ipaddress", ADDRESS),
    ("ns-1:NetworkAddress", ADDRESS),
    ("ns.v1:IPV6ADDRESS", ADDRESS),
    ("serialnumber", SERIAL),
    ("ns-1:SerialNumber", SERIAL),
    ("ns.v1:LOGICALSERIALNUMBER", SERIAL),
    ("ns-1:virtualserialnumber", SERIAL),
    ("uniquedeviceid", DEVID),
    ("ns-1:UniqueDeviceID", DEVID),
    ("ns.v1:wwpn", DEVID),
    ("wwpnprefix", DEVID),
    ("ns-1:WWPNPrefix", DEVID),
    # #1588: a prefix `json.dumps` writes with a `\u` escape, a non-ASCII name character
    # that is not a word character, and more than one prefix.
    *(
        (f"{prefix}{element}", written)
        for prefix in ("ns\u00e9:", "a\u00b7b:", "ns:v1:")
        for element, written in [
            ("X-API-Session", SESSION),
            ("PublicSSHKeyValue", SSH_KEY),
            ("IPAddress", ADDRESS),
            ("SerialNumber", SERIAL),
            ("WWPN", DEVID),
        ]
    ),
]
SCRUBBED_ELEMENTS += VARIANT_ELEMENTS


def _escaped(written: str) -> str:
    """*written* as an XML body holds it: a token there would open an element."""
    return written.replace("<", "&lt;").replace(">", "&gt;")


@pytest.mark.parametrize("encode", [str, json.dumps], ids=["raw", "json"])
@pytest.mark.parametrize(("element", "written"), VARIANT_ELEMENTS)
def test_element_name_case_and_prefix_are_scrubbed(
    element: str, written: str, encode: Any
) -> None:
    """Regression #1572, #1588: `<serialnumber>` and `<ns-1:SerialNumber>` were exported."""
    tok = export.Tokenizer({}, set())
    body = encode(f'<Root><{element} kb="CUR">SYN-77Q</{element}></Root>')
    scrubbed = tok.tokenize(body)
    assert scrubbed == body.replace(
        "SYN-77Q", _escaped(written) if encode is str else written
    )
    assert tok.leaks(scrubbed) == []


#: What ends a prefix: XML's spaces, a tag end, a quote, `/` and `=`, and in
#: `json.dumps` text a `\"` and an escape of any of those (#1588, #1623).
PREFIX_GAPS = [" ", "\t", "\n", "\r", ">", '"', "'", "/", "="]
PREFIX_GAPS += ['\\"', "\\u0020", "\\u0009", "\\u003e", "\\u002F", "\\u003d"]


@pytest.mark.parametrize("gap", PREFIX_GAPS)
def test_namespace_prefix_does_not_span_a_tag(gap: str) -> None:
    """A prefix ends at a tag delimiter: `x:SerialNumber` in another tag is not one."""
    tok = export.Tokenizer({}, set())
    body = f"<Note{gap}x:SerialNumber>SYN-77Q</Note>"
    assert tok.tokenize(body) == body


@pytest.mark.parametrize("encode", [str, json.dumps], ids=["raw", "json"])
@pytest.mark.parametrize("element", ["SerialNumber", "MACAddress"])
@pytest.mark.parametrize(
    "markup",
    ["<!--x:{}-->", "<![CDATA[x:{}]]>", "<?x:{}?>"],
    ids=["comment", "cdata", "pi"],
)
def test_markup_that_opens_no_element_is_not_scrubbed(
    markup: str, element: str, encode: Any
) -> None:
    """A prefix never starts with `!` or `?`, so the text after this markup is kept."""
    tok = export.Tokenizer({}, set())
    body = encode(f"<Note>{markup.format(element)}FEED</Note>")
    assert tok.tokenize(body) == body


#: What a prefix holds: every ASCII character but a gap, `:` or `<`, a backslash
#: included, every non-ASCII character Python's `\s` matches, and zero-width ones it
#: does not. None is an XML name character, but taking one into the prefix scrubs a
#: malformed tag where ending the prefix at it exported the value (#1623).
PREFIX_CHARS = [
    *(chr(c) for c in range(0x80) if chr(c) not in " \t\n\r>\"'/=:<"),
    *(chr(c) for c in range(0x80, 0x110000) if re.fullmatch(r"\s", chr(c))),
    "\u180e",
    "\u200b",
    "\ufeff",
]


@pytest.mark.parametrize("encode", [str, json.dumps], ids=["raw", "json"])
@pytest.mark.parametrize(
    "prefix",
    [*(f"a{c}b:" for c in PREFIX_CHARS), ":", "ns::", "\\:"],
    ids=lambda prefix: "-".join(f"{ord(c):02X}" for c in prefix),
)
@pytest.mark.parametrize(
    ("element", "value", "written"),
    [
        ("X-API-Session", "SYN-77Q", SESSION),
        ("PublicSSHKeyValue", "SYN-77Q", SSH_KEY),
        ("IPAddress", "SYN-77Q", ADDRESS),
        ("SerialNumber", "SYN-77Q", SERIAL),
        ("WWPN", "SYN-77Q", DEVID),
        ("MACAddress", "0A1B2C3D4E5F", "000000000000"),  # pragma: allowlist secret
    ],
)
def test_namespace_prefix_with_any_other_character_is_scrubbed(
    element: str, value: str, written: str, prefix: str, encode: Any
) -> None:
    """Regression #1623: `a`, U+1680, `b` or `a!b` as a prefix hid a value from both checks."""
    tok = export.Tokenizer({}, set())
    name = f"{prefix}{element}"
    body = encode(f'<Root><{name} kb="CUR">{value}</{name}></Root>')
    assert tok.leaks(body) == [value]
    scrubbed = tok.tokenize(body)
    assert scrubbed == body.replace(
        value, _escaped(written) if encode is str else written
    )
    assert tok.leaks(scrubbed) == []


@pytest.mark.parametrize("encode", [str, json.dumps], ids=["raw", "json"])
@pytest.mark.parametrize(
    "element",
    ["SerialNumber:x", "a:IPv6Address:x", "X-API-Session:x", "WWPN:x", "MACAddress:x"],
)
def test_element_name_before_a_colon_is_still_scrubbed(
    element: str, encode: Any
) -> None:
    """A prefix is given back when the element name does not follow it (#1588)."""
    tok = export.Tokenizer({}, set())
    scrubbed = tok.tokenize(encode(f"<Root><{element}>{MAC}</{element}></Root>"))
    assert scrubbed is not None
    assert MAC not in scrubbed
    assert tok.leaks(scrubbed) == []


@pytest.mark.parametrize("encode", [str, json.dumps], ids=["raw", "json"])
@pytest.mark.parametrize(("element", "written"), SCRUBBED_ELEMENTS)
def test_unscrubbed_element_value_is_a_leak(
    element: str, written: str, encode: Any
) -> None:
    """Regression #1562: only the scrub rule stood between these values and the export."""
    tok = export.Tokenizer({}, set())
    body = f'<Root><{element} kb="CUR">SYN-77Q</{element}></Root>'
    assert tok.leaks(encode(body)) == ["SYN-77Q"]


@pytest.mark.parametrize("encode", [str, json.dumps], ids=["raw", "json"])
@pytest.mark.parametrize(("element", "written"), SCRUBBED_ELEMENTS)
@pytest.mark.parametrize("escape", [_escaped, str], ids=["escaped", "bare"])
def test_scrubbed_element_value_is_not_a_leak(
    element: str, written: str, encode: Any, escape: Any
) -> None:
    """What the rule writes, escaped in an XML body or bare elsewhere, is the one value."""
    tok = export.Tokenizer({}, set())
    value = escape(written)
    assert (
        tok.leaks(encode(f'<Root><{element} kb="CUR">{value}</{element}></Root>')) == []
    )


#: A `WWPNPrefix` as a `ManagedSystem` entry carries it, in a 12-hex-digit shape that no
#: WWN or MAC rule reads.
WWPN_PREFIX = "c050760abcde"  # pragma: allowlist secret
WWPN_PREFIX_BODY = (
    f'<IOAdapter><WWPNPrefix kb="CUD">{WWPN_PREFIX}</WWPNPrefix></IOAdapter>'
)


def test_wwpn_prefix_is_scrubbed_from_a_rest_body() -> None:
    """Regression #1639: a system's WWPN prefix was exported as captured."""
    corpus = export.tokenize_records(_records(_rest("/e", WWPN_PREFIX_BODY)))
    assert corpus[-1]["body"] == WWPN_PREFIX_BODY.replace(WWPN_PREFIX, _escaped(DEVID))


def test_wwpn_prefix_is_scrubbed_from_tool_json() -> None:
    """Regression #1639: the same prefix inside a tool's `json.dumps` output."""
    tool = {"kind": "tool", "step": "t", "tool": "t", "ok": True}
    corpus = export.tokenize_records(
        _records({**tool, "data": {"raw": WWPN_PREFIX_BODY}})
    )
    assert corpus[-1]["record"]["data"] == {
        "raw": WWPN_PREFIX_BODY.replace(WWPN_PREFIX, DEVID)
    }


#: Input the scrub rules leave unscrubbed: a `/>` ends the opening tag, a `<` inside the
#: value stops the rule's match, text follows a placeholder, and a CDATA section.
UNSCRUBBED_MALFORMED = [
    '<SerialNumber kb="x"/>SYN77Q</SerialNumber>',
    "<UniqueDeviceID>SYN<77Q</UniqueDeviceID>",
    '<SerialNumber kb="x"/>&lt;REDACTED-SERIAL&gt;SYN77Q</SerialNumber>',
    "<X-API-Session><![CDATA[SYN77Q]]></X-API-Session>",
]


@pytest.mark.parametrize("body", UNSCRUBBED_MALFORMED)
def test_malformed_element_the_rules_miss_fails_closed(body: str) -> None:
    with pytest.raises(export.LeakError):
        export.tokenize_records(_records(_rest("/e", body)))


@pytest.mark.parametrize("raw", UNSCRUBBED_MALFORMED)
def test_malformed_element_the_rules_miss_fails_closed_in_json(raw: str) -> None:
    tool = {"kind": "tool", "step": "t", "tool": "t", "ok": True, "data": {"raw": raw}}
    with pytest.raises(export.LeakError):
        export.tokenize_records(_records(tool))


def _json_twice(text: str) -> str:
    return json.dumps(json.dumps(text))


@pytest.mark.parametrize(
    "encode", [str, json.dumps, _json_twice], ids=["raw", "json", "json-twice"]
)
@pytest.mark.parametrize(("element", "written"), SCRUBBED_ELEMENTS)
@pytest.mark.parametrize("gap", [" ", "\n", "\r\n", "\t", " \r\n\t "])
def test_cdata_before_whitespace_and_closing_tag_is_a_leak(
    element: str, written: str, encode: Any, gap: str
) -> None:
    """#1590: whitespace after `]]>` does not hide the CDATA from the backstop.

    The text run between `]]>` and the closing tag is what reports it; which
    substring is reported is not pinned.
    """
    tok = export.Tokenizer({}, set())
    scrubbed = tok.tokenize(
        encode(f"<Root><{element}><![CDATA[SYN77Q]]>{gap}</{element}></Root>")
    )
    assert scrubbed is not None
    assert tok.leaks(scrubbed) != []


MAC = "0A1B2C3D4E5F"  # pragma: allowlist secret
#: Names the MAC element rule scrubs: a prefix, a suffix, any case, a namespace.
MAC_ELEMENTS = [
    "MACAddress",
    "ns:MACAddressPrefix",
    "macaddress",
    "ns1:MacAddressValue",
    "AllowedOperatingSystemMACAddresses",
    # Regression #1589: the rule took only a `\w+:` prefix.
    "ns-1:MACAddress",
    "ns.v1:macaddressprefix",
    # Regression #1588: a `\u`-escaped prefix in `json.dumps` text, and two prefixes.
    "ns\u00e9:MACAddress",
    "a\u00b7b:MACAddress",
    "ns:v1:MACAddress",
]


@pytest.mark.parametrize("encode", [str, json.dumps], ids=["raw", "json"])
@pytest.mark.parametrize("element", MAC_ELEMENTS[-5:])
def test_mac_element_namespace_prefix_is_zeroed(element: str, encode: Any) -> None:
    """Regression #1589, #1588: `<ns-1:MACAddress>` was left whole; the export stopped."""
    tok = export.Tokenizer({}, set())
    body = encode(f'<Root><{element} kb="CUR">{MAC}</{element}></Root>')
    scrubbed = tok.tokenize(body)
    assert scrubbed == body.replace(MAC, "000000000000")
    assert tok.leaks(scrubbed) == []


@pytest.mark.parametrize("gap", PREFIX_GAPS)
def test_mac_namespace_prefix_does_not_span_a_tag(gap: str) -> None:
    """A prefix is name characters only: `x:MACAddress` in another tag is not one."""
    tok = export.Tokenizer({}, set())
    body = f"<Note{gap}x:MACAddress>FEED</Note>"
    assert tok.tokenize(body) == body


def test_mac_element_opening_tag_skips_a_quoted_value() -> None:
    """A `>` inside a quoted attribute value does not end the opening tag (#1589)."""
    body = f'<Adapter><MACAddress kb="a/>b">{MAC}</MACAddress></Adapter>'
    corpus = export.tokenize_records(_records(_rest("/e", body)))
    assert corpus[-1]["body"] == body.replace(MAC, "000000000000")


@pytest.mark.parametrize("encode", [str, json.dumps], ids=["raw", "json"])
@pytest.mark.parametrize("element", MAC_ELEMENTS)
def test_unscrubbed_mac_element_value_is_a_leak(element: str, encode: Any) -> None:
    """Regression #1573: only the scrub rule stood between a MAC value and the export."""
    tok = export.Tokenizer({}, set())
    body = f'<Root><{element} kb="CUR">{MAC}</{element}></Root>'
    assert tok.leaks(encode(body)) == [MAC]


@pytest.mark.parametrize("encode", [str, json.dumps], ids=["raw", "json"])
@pytest.mark.parametrize("element", MAC_ELEMENTS)
@pytest.mark.parametrize(
    "value",
    [MAC, f"\n  {MAC}\n", "0A:1B:2C:3D:4E:5F", f"{MAC},1A2B3C", "ALL", "NONE", "true"],
)
def test_scrubbed_mac_element_value_is_not_a_leak(
    element: str, encode: Any, value: str
) -> None:
    """A zeroed value and a keyword are the evidence the scrub rule keeps."""
    tok = export.Tokenizer({}, set())
    scrubbed = tok.tokenize(
        encode(f'<Root><{element} kb="CUR">{value}</{element}></Root>')
    )
    assert scrubbed is not None
    assert tok.leaks(scrubbed) == []
    assert tok.leaks(json.dumps(scrubbed)) == []


#: MAC element text the scrub rule leaves: the opening tag is gone (before a value and
#: before a backslash, which the JSON scan sees doubled), never closes, or closes
#: itself as `/>`, as in the other element rules, a `<` inside the value stops the
#: rule, and a CDATA section.
UNSCRUBBED_MAC = [
    f"<Adapter>{MAC}</MACAddress></Adapter>",
    f"<Adapter>\\{MAC}</MACAddress></Adapter>",
    f'<Adapter><MACAddress kb="CUR"{MAC}</MACAddress></Adapter>',
    f'<Adapter><MACAddress kb="x"/>{MAC}</MACAddress></Adapter>',
    "<Adapter><MACAddress>0A1B<2C3D4E5F</MACAddress></Adapter>",
    f"<Adapter><MACAddress><![CDATA[{MAC}]]></MACAddress></Adapter>",
    f"<Adapter><MACAddress><![CDATA[{MAC}]]>\n  </MACAddress></Adapter>",
]


@pytest.mark.parametrize("body", UNSCRUBBED_MAC)
def test_mac_element_the_rule_misses_fails_closed(body: str) -> None:
    with pytest.raises(export.LeakError):
        export.tokenize_records(_records(_rest("/e", body)))


#: Values joined to a word the rule does not zero: a `0x`, a suffix, and a suffix after
#: a JSON escape.
JOINED_MAC = [
    f"0x{MAC}",
    f"{MAC}h",
    f"\f{MAC}h",
    f"\u00e9{MAC}h",
]


@pytest.mark.parametrize("value", [f"0x{MAC}", f"{MAC}h", f"\u00e9{MAC}"])
def test_mac_joined_to_a_word_fails_closed(value: str) -> None:
    body = f"<Adapter><MACAddress>{value}</MACAddress></Adapter>"
    with pytest.raises(export.LeakError):
        export.tokenize_records(_records(_rest("/e", body)))


@pytest.mark.parametrize("before", ["\x0b", "\b", "\f", "\\"])
def test_zeroed_mac_after_a_json_escape_is_not_a_leak(before: str) -> None:
    """The corpus is scanned as JSON, where these become `\\u000b`, `\\b` or `\\\\`."""
    body = f"<Adapter><MACAddress>{before}{MAC}</MACAddress></Adapter>"
    corpus = export.tokenize_records(_records(_rest("/e", body)))
    assert corpus[-1]["body"] == body.replace(MAC, "000000000000")


@pytest.mark.parametrize(
    "raw",
    [*UNSCRUBBED_MAC, *(f"<MACAddress>{value}</MACAddress>" for value in JOINED_MAC)],
)
def test_mac_element_the_rule_misses_fails_closed_in_json(raw: str) -> None:
    """A tool record is scanned as JSON text, where each of these leaves a live run."""
    tool = {"kind": "tool", "step": "t", "tool": "t", "ok": True, "data": {"raw": raw}}
    with pytest.raises(export.LeakError):
        export.tokenize_records(_records(tool))


@pytest.mark.parametrize(
    ("before", "kept"),
    [
        ("\b", "\b"),
        ("\f", "\f"),
        ("\n", "\n"),
        ("\r", "\r"),
        ("\t", "\t"),
        ("\\", "\\"),
        # The rule cannot tell `\u000b` from a literal `\u` before a MAC, so the
        # escape becomes `\u0030`, the digit `0`, rather than a NUL XML cannot hold.
        ("\x0b", "0"),
        ("\u00e9", "0"),
        # A surrogate pair is replaced whole: a lone high half cannot be written as UTF-8.
        ("\U0001f600", "00"),
    ],
)
@pytest.mark.parametrize("value", [MAC, MAC[:4]])
def test_mac_after_a_json_escape_in_a_tool_record_is_zeroed(
    before: str, kept: str, value: str
) -> None:
    """Regression #1591: the `f` of `\\f` was zeroed with the value, into `\\0`."""
    raw = f"<MACAddress>{before}{value}</MACAddress>"
    tool = {"kind": "tool", "step": "t", "tool": "t", "ok": True, "data": {"raw": raw}}
    corpus = export.tokenize_records(_records(tool))
    zeroed = f"<MACAddress>{kept}{'0' * len(value)}</MACAddress>"
    assert corpus[-1]["record"]["data"]["raw"] == zeroed
    assert "\x00" not in corpus[-1]["record"]["data"]["raw"]


def test_mac_after_a_literal_backslash_u_is_zeroed_whole() -> None:
    """Raw XML has no escapes: keeping `\\uA1B2` as one would export four MAC digits."""
    body = f"<Adapter><MACAddress>\\u{MAC}</MACAddress></Adapter>"
    corpus = export.tokenize_records(_records(_rest("/e", body)))
    assert corpus[-1]["body"] == body.replace(MAC, "003000000000")


#: A separated MAC after a literal `\\u`, and what a REST body and a tool record export.
#: Its first octets are fewer digits than an escape holds, or exactly four, which raw
#: XML reads as the MAC's and JSON text as an escape's. Two backslashes are an escaped
#: one in JSON text, so the four digits after them are the MAC's in both (#1622).
SEPARATED_AFTER_U = [
    ("\\u0A:1B:2C:3D:4E:5F", "\\u00:00:00:00:00:00", "\\u00:00:00:00:00:00"),
    ("\\u0A-1B-2C-3D-4E-5F", "\\u00-00-00-00-00-00", "\\u00-00-00-00-00-00"),
    ("\\u0A1B:2C:3D:4E:5F", "\\u0030:00:00:00:00", "\\u0000:00:00:00:00"),
    ("\\u0a1b.2c3d.4e5f", "\\u0030.0000.0000", "\\u0000.0000.0000"),
    ("\\u0A1B-2C3D-4E5F", "\\u0030-0000-0000", "\\u0000-0000-0000"),
    ("\\\\u0a1b.2c3d.4e5f", "\\\\u0000.0000.0000", "\\\\u0000.0000.0000"),
]


@pytest.mark.parametrize(("value", "rest", "tool"), SEPARATED_AFTER_U)
def test_separated_mac_after_a_literal_backslash_u_is_zeroed(
    value: str, rest: str, tool: str
) -> None:
    """Regression #1622: `\\u0A:1B:...` exported `\\u0A`, and `\\u0a1b.2c3d...` `\\u0a1b`."""
    raw = f"<MACAddress>{value}</MACAddress>"
    record = {
        "kind": "tool",
        "step": "t",
        "tool": "t",
        "ok": True,
        "data": {"raw": raw},
    }
    corpus = export.tokenize_records(_records(_rest("/e", raw), record))
    assert corpus[-2]["body"] == f"<MACAddress>{rest}</MACAddress>"
    assert corpus[-1]["record"]["data"]["raw"] == f"<MACAddress>{tool}</MACAddress>"


@pytest.mark.parametrize("before", ["ਛ", "é", "\n", "\\"])
def test_separated_mac_after_a_json_escape_in_a_tool_record_is_zeroed(
    before: str,
) -> None:
    """A real escape before a separator is zeroed as one before a joined MAC is."""
    raw = f"<MACAddress>{before}.2c3d.4e5f</MACAddress>"
    tool = {"kind": "tool", "step": "t", "tool": "t", "ok": True, "data": {"raw": raw}}
    corpus = export.tokenize_records(_records(tool))
    kept = before if before in "\n\\" else "0"
    assert (
        corpus[-1]["record"]["data"]["raw"]
        == f"<MACAddress>{kept}.0000.0000</MACAddress>"
    )


def test_separated_mac_after_a_non_ascii_character_in_a_rest_body_fails_closed() -> (
    None
):
    """The export is JSON, where `ਛ` is written as the escape `\\u0a1b`: four digits."""
    body = "<Adapter><MACAddress>ਛ.2c3d.4e5f</MACAddress></Adapter>"
    with pytest.raises(export.LeakError):
        export.tokenize_records(_records(_rest("/e", body)))


@pytest.mark.parametrize("encode", [str, json.dumps], ids=["raw", "json"])
@pytest.mark.parametrize(
    "value",
    [
        "\\u0A:00:00:00:00:00",
        "\\u0a1b.0000.0000",
        "\\u0A1B:00:00:00:00",
        "\\\\u0a1b.0000.0000",
        "\\u000b:00:00:00:00",
        "\\u0A1B-0000-0000",
    ],
)
def test_octets_surviving_a_literal_backslash_u_are_a_leak(
    encode: Any, value: str
) -> None:
    """What main exported for #1622's values; the scrub rule's `0030` and `0000` are not."""
    tok = export.Tokenizer({}, set())
    body = encode(f"<MACAddress>{value}</MACAddress>")
    assert tok.leaks(body) == [body[body.index(">") + 1 : body.index("</")]]


#: A literal `\\u` before four MAC digits and a space, comma, slash or tab, or at the end
#: of the value, and before five or more, with what a REST body and a tool record
#: export. JSON text writes the `\\u` as `\\\\u`, so a tool record zeroes the digits; a
#: REST body keeps four, read as an escape, so it fails closed (#1642). Five or more are
#: zeroed in both, and so are digits after two literal backslashes.
LITERAL_U_DIGITS = [
    ("\\u0A1B 2C3D 4E5F", None, "\\u0000 0000 0000"),
    ("\\u0a1b,2c3d,4e5f", None, "\\u0000,0000,0000"),
    ("\\u0A1B/2C3D/4E5F", None, "\\u0000/0000/0000"),
    ("\\u0A1B\t2C3D\t4E5F", None, "\\u0000\t0000\t0000"),
    ("2C3D 4E5F \\u0A1B", None, "0000 0000 \\u0000"),
    ("\\u0A1B2 C3D4E5F", "\\u00300 0000000", "\\u00000 0000000"),
    ("\\u0A1B2C 3D4E5F", "\\u003000 000000", "\\u000000 000000"),
    (f"\\u{MAC}", "\\u003000000000", "\\u000000000000"),
    ("\\\\u0A1B2C 3D4E5F", "\\\\u000000 000000", "\\\\u000000 000000"),
    (f"\\\\u{MAC}", "\\\\u000000000000", "\\\\u000000000000"),
]


@pytest.mark.parametrize(("value", "rest", "tool"), LITERAL_U_DIGITS)
def test_mac_digits_after_a_literal_backslash_u_are_zeroed_or_fail_closed(
    value: str, rest: str | None, tool: str
) -> None:
    """Regression #1642: a REST body exported `\\u0A1B 0000 0000` with no leak reported,
    and a tool record refused five or more digits that a REST body zeroed."""
    raw = f"<MACAddress>{value}</MACAddress>"
    record = {
        "kind": "tool",
        "step": "t",
        "tool": "t",
        "ok": True,
        "data": {"raw": raw},
    }
    corpus = export.tokenize_records(_records(record))
    assert corpus[-1]["record"]["data"]["raw"] == f"<MACAddress>{tool}</MACAddress>"
    if rest is None:
        with pytest.raises(export.LeakError):
            export.tokenize_records(_records(_rest("/e", raw)))
    else:
        corpus = export.tokenize_records(_records(_rest("/e", raw)))
        assert corpus[-1]["body"] == f"<MACAddress>{rest}</MACAddress>"


#: What main exported for #1642's REST values, as JSON text holds them: the literal `\\u`
#: is `\\\\u`. Raw, and again in `json.dumps` form, an even count of backslashes is
#: before the `u`, so it is literal.
LITERAL_U_LEAKS = [
    "\\\\u0A1B 0000 0000",
    "\\\\u0a1b,0000,0000",
    "\\\\u0A1B/0000/0000",
    "\\\\u0A1B\\t0000\\t0000",
    "0000 0000 \\\\u0A1B",
    "\\\\u000b",
    "\\\\u0A1B2Cx",
]


@pytest.mark.parametrize("encode", [str, json.dumps], ids=["raw", "json"])
@pytest.mark.parametrize("value", LITERAL_U_LEAKS)
def test_digits_after_an_escaped_backslash_u_are_a_leak(
    encode: Any, value: str
) -> None:
    tok = export.Tokenizer({}, set())
    body = encode(f"<MACAddress>{value}</MACAddress>")
    assert tok.leaks(body) == [body[body.index(">") + 1 : body.index("</")]]


@pytest.mark.parametrize("encode", [str, json.dumps], ids=["raw", "json"])
@pytest.mark.parametrize(
    "value", ["\\\\u0030 0000", "\\\\u003000 00", "\\\\u0000 0000", "\\\\u0A1Bx"]
)
def test_escaped_backslash_u_the_rule_writes_is_not_a_leak(
    encode: Any, value: str
) -> None:
    """The `0030` and zeros the rule writes, and digits joined to a word, as elsewhere."""
    tok = export.Tokenizer({}, set())
    body = encode(f"<MACAddress>{value}</MACAddress>")
    assert tok.leaks(body) == []


@pytest.mark.parametrize(
    ("value", "tool"),
    [
        ("\\fa1b.2c3d.4e5f", "\\0000.0000.0000"),
        ("\\ba-1b-2c-3d-4e-5f", "\\00-00-00-00-00-00"),
        ("\\fA1B2C3D4E5F", "\\000000000000"),
    ],
)
def test_mac_digit_read_as_a_literal_escape_letter_fails_closed(
    value: str, tool: str
) -> None:
    """A REST body keeps the `f` of a literal `\\fa1b` as an escape's letter; JSON text
    writes it after `\\\\`, where `_AFTER_ESCAPE` starts a run. A tool record zeroes it."""
    raw = f"<MACAddress>{value}</MACAddress>"
    record = {
        "kind": "tool",
        "step": "t",
        "tool": "t",
        "ok": True,
        "data": {"raw": raw},
    }
    corpus = export.tokenize_records(_records(record))
    assert corpus[-1]["record"]["data"]["raw"] == f"<MACAddress>{tool}</MACAddress>"
    with pytest.raises(export.LeakError):
        export.tokenize_records(_records(_rest("/e", raw)))


@pytest.mark.parametrize("value", ["ਛ 0000", "\\ਛ 0000", "\\\\ਛ 0000"])
def test_real_escape_after_escaped_backslashes_is_not_a_leak(value: str) -> None:
    """JSON text writes `ਛ` as the escape `\\u0a1b`, after any escaped backslashes: an odd
    count before the `u`, so it keeps today's handling."""
    tok = export.Tokenizer({}, set())
    assert tok.leaks(json.dumps(f"<MACAddress>{value}</MACAddress>")) == []


def test_mac_after_a_non_ascii_character_and_a_space_is_kept() -> None:
    """`ਛ` is written as the escape `\\u0a1b` in the export, one backslash: not a MAC."""
    body = "<Adapter><MACAddress>\u0a1b 2C3D 4E5F</MACAddress></Adapter>"
    corpus = export.tokenize_records(_records(_rest("/e", body)))
    assert corpus[-1]["body"] == body.replace("2C3D 4E5F", "0000 0000")


#: Separated MACs with an octet joined to a word character, and the export: a run that
#: a MAC separator and more hex follow, or that follows a hex digit and a separator, is
#: zeroed whatever adjoins it (#1640).
JOINED_OCTET = [
    ("G0A:1B:2C:3D:4E:5F", "G00:00:00:00:00:00"),
    ("\\U0A:1B:2C:3D:4E:5F", "\\U00:00:00:00:00:00"),
    ("G0A-1B-2C-3D-4E-5F", "G00-00-00-00-00-00"),
    ("x0a1b.2c3d.4e5f", "x0000.0000.0000"),
    ("0A:1B:2C:3D:4E:5FG", "00:00:00:00:00:00G"),
    ("0A:1BG:2C:3D:4E:5F", "00:00G:00:00:00:00"),
]


@pytest.mark.parametrize("encode", [str, json.dumps], ids=["raw", "json"])
@pytest.mark.parametrize(("value", "zeroed"), JOINED_OCTET)
def test_mac_octet_joined_to_a_word_is_zeroed(
    encode: Any, value: str, zeroed: str
) -> None:
    """Regression #1640: `G0A:1B:...` exported `G0A:00:...`, and no leak was reported."""
    tok = export.Tokenizer({}, set())
    scrubbed = tok.tokenize(encode(f"<MACAddress>{value}</MACAddress>"))
    assert scrubbed == encode(f"<MACAddress>{zeroed}</MACAddress>")
    assert tok.leaks(scrubbed) == []


@pytest.mark.parametrize("encode", [str, json.dumps], ids=["raw", "json"])
@pytest.mark.parametrize(
    "value",
    [
        "G0A:00:00:00:00:00",
        "\\U0A:00:00:00:00:00",
        "x0a1b.0000.0000",
        "00:00:00:00:00:5FG",
        "00:1BG:00:00:00:00",
    ],
)
def test_octet_joined_to_a_word_is_a_leak(encode: Any, value: str) -> None:
    """What main exported for #1640's values: the leak check saw no live run."""
    tok = export.Tokenizer({}, set())
    body = encode(f"<MACAddress>{value}</MACAddress>")
    assert tok.leaks(body) == [body[body.index(">") + 1 : body.index("</")]]


@pytest.mark.parametrize("encode", [str, json.dumps], ids=["raw", "json"])
@pytest.mark.parametrize(
    "value",
    ["\f0A:00:00:00:00:00", "\bA1:00:00:00:00:00", "é0A:00:00:00:00:00"],
)
def test_octet_after_an_escape_ending_in_hex_is_a_leak(encode: Any, value: str) -> None:
    """A run after an escape whose last character is a hex digit, as in `\\f` or `\\u00e9`
    in JSON text, is reported by `_LIVE_HEX_RUN`'s escape start, not the joined octet."""
    tok = export.Tokenizer({}, set())
    body = encode(f"<MACAddress>{value}</MACAddress>")
    assert tok.leaks(body) == [body[body.index(">") + 1 : body.index("</")]]


@pytest.mark.parametrize("encode", [str, json.dumps], ids=["raw", "json"])
@pytest.mark.parametrize(
    "value", ["none", "auto", "auto-negotiate", "ALL", "G0A", "G0A:x"]
)
def test_mac_element_text_without_a_separated_octet_is_kept(
    encode: Any, value: str
) -> None:
    """A keyword, and a run no separator and hex follow, keep today's handling."""
    tok = export.Tokenizer({}, set())
    body = encode(f"<MACAddress>{value}</MACAddress>")
    assert tok.tokenize(body) == body
    assert tok.leaks(body) == []


def test_mac_after_an_escape_that_follows_a_backslash_stays_valid_json() -> None:
    """`\\\\\\f` is a backslash then a form feed: the `f` is still an escape's letter."""
    tok = export.Tokenizer({}, set())
    scrubbed = tok.tokenize(json.dumps({"raw": f"<MACAddress>\\\f{MAC}</MACAddress>"}))
    assert scrubbed is not None
    assert json.loads(scrubbed) == {"raw": "<MACAddress>\\\f000000000000</MACAddress>"}


LIVE_FIXTURES = sorted(
    (Path(__file__).parents[1] / "fixtures" / "live").rglob("*.json")
)


@pytest.mark.parametrize("path", LIVE_FIXTURES, ids=lambda path: path.name)
def test_committed_fixture_tokenizes_without_leaks(path: Path) -> None:
    """A capture is re-tokenized as its record kind; a derived file is scanned as is.

    Names are not collected: a fixture's names are already tokens, and re-tokenizing
    them maps one token onto another, which the name check reads as a leak.
    """
    tok = export.Tokenizer({}, set())
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and ("body" in data or "command" in data):
        kind = "rest" if "body" in data else "ssh"
        record = {**data, "_src": path.name, "kind": kind}
        text = json.dumps([export._tokenize_record(tok, record)])
    else:
        text = path.read_text(encoding="utf-8")
    assert tok.leaks(text) == []


def _cost_ratio(
    rule: re.Pattern[str], baseline: re.Pattern[str], replacement: Any, body: str
) -> float:
    """The fastest CPU time of *rule* over *body*, as a multiple of *baseline*'s.

    The two alternate so load hits both alike, and CPU time leaves out preemption.
    """
    timings: dict[re.Pattern[str], list[float]] = {rule: [], baseline: []}
    for _ in range(3):
        for pattern, runs in timings.items():
            started = time.process_time()
            assert pattern.sub(replacement, body) == body
            runs.append(time.process_time() - started)
    return min(timings[rule]) / min(timings[baseline])


@pytest.mark.parametrize(
    "element",
    [
        "SerialNumber",
        "ns-1:MACAddress",
        "ns:v1:X-API-Session",
        "ns-1:PublicSSHKeyValue",
        r"ns\u00e9:IPAddress",
        "a\u3000b:SerialNumber",
        "a!b:WWPN",
        r"ns\\x::X-API-Session",
        r"a\u1680b:MACAddress",
    ],
)
def test_many_unclosed_openings_do_not_backtrack(element: str) -> None:
    """Regression #1548: the fallback backtracked over the rest of the body per opening.

    Each opening still scans to the end of the body, so the fixed tail is quadratic too:
    input size cannot tell the two apart, only the cost per scanned character can. So
    each `_TAG_TAIL` rule this body reaches is timed against itself with the tail
    swapped for one possessive scan to the next `>`, on the same body in the same run,
    which cancels host speed. The fixed tail costs about 2x that baseline on Python
    3.11-3.14, and under 3x on an oversubscribed host; the backtracking one cost 40x+.
    """
    body = f"<{element} " * 5_000
    rules = (*export.SECRET_RULES, *export.IDENTIFIER_RULES)
    for rule, replacement in rules:
        assert rule.sub(replacement, body) == body
    tail_rules = [
        (rule, replacement)
        for rule, replacement in rules
        if export._TAG_TAIL in rule.pattern and rule.search(f"<{element} >x<")
    ]
    assert tail_rules
    for rule, replacement in tail_rules:
        scan = re.compile(rule.pattern.replace(export._TAG_TAIL, "[^>]*+>"), rule.flags)
        assert _cost_ratio(rule, scan, replacement, body) < 10


def test_mac_leak_check_after_many_backslash_u_does_not_backtrack() -> None:
    """The `\\u` check runs at each position of a MAC element's text; it reads ahead a
    bounded distance, so it costs about what the check without it does (#1622)."""
    body = "<MACAddress>" + "\\u0A1Bx\\u0Ax\\u0A1B.x\\u0030." * 5_000 + "</MACAddress>"
    without = re.compile(
        export._MAC_LEAK.pattern.replace(export._AFTER_LITERAL_U, "(?!)")
    )
    assert without.pattern != export._MAC_LEAK.pattern
    assert _cost_ratio(export._MAC_LEAK, without, "", body) < 10


def test_mac_leak_check_after_long_backslash_runs_does_not_backtrack() -> None:
    """The literal `\\u` check matches a backslash run from its first backslash only, so
    a long run costs about what the check without it does (#1642)."""
    body = "<MACAddress>" + ("\\" * 5_000 + "u0A1Bx ") * 20 + "</MACAddress>"
    without = re.compile(export._MAC_LEAK.pattern.replace(export._LITERAL_U, "(?!)"))
    assert without.pattern != export._MAC_LEAK.pattern
    assert _cost_ratio(export._MAC_LEAK, without, "", body) < 10


def test_joined_octet_scrub_scans_each_run_once() -> None:
    """A run joined to a word that no separator follows is not rescanned from each of its
    positions, so the #1640 branches cost about what the rule without them does. Without
    the lookbehind that starts it on a run's first digit, the rule cost 100x."""
    body = ("G" + "A" * 5_000 + " ") * 20
    rule = export._MAC_ELEMENT_RUN
    without = re.compile(rule.pattern.replace(export._JOINED_OCTET, "(?!)"))
    assert without.pattern != rule.pattern
    assert _cost_ratio(rule, without, export._zero_run, body) < 10


def test_joined_octet_leak_check_scans_each_run_once() -> None:
    """The leak check over zeroed runs joined to words costs about what it does without
    the #1640 branches. A run of zeros is skipped fast, so only a long one shows a rescan:
    without the lookbehind that starts it on a run's first digit, the check cost 30x."""
    zeros = "0" * 50_000
    body = f"<MACAddress>G{zeros}G:{zeros} </MACAddress>"
    without = re.compile(
        export._MAC_LEAK.pattern.replace(export._LIVE_JOINED_OCTET, "(?!)")
    )
    assert without.pattern != export._MAC_LEAK.pattern
    assert _cost_ratio(export._MAC_LEAK, without, "", body) < 10


def test_key_start_after_many_escapes_does_not_backtrack() -> None:
    """Each escape lookbehind reads at most six characters, so a key=value rule and its
    backstop cost about what they do with `\\b` alone (#1638)."""
    body = "\\nserial_nu\\tudi\\u00e9wwpn\\\\unique_id\\fwwnn " * 5_000
    rules = [
        (rule, replacement)
        for rule, replacement in export.IDENTIFIER_RULES
        if export._KEY_START in rule.pattern
    ]
    rules += [
        (rule, "") for rule in export.ALWAYS_LEAKS if export._KEY_START in rule.pattern
    ]
    assert len(rules) == 2
    for rule, replacement in rules:
        bounded = re.compile(rule.pattern.replace(export._KEY_START, r"\b"), rule.flags)
        assert _cost_ratio(rule, bounded, replacement, body) < 10


#: The quoted key=value rule's value and closing quote, and a scan of it with no
#: backslash handling.
_QUOTED_VALUE = r'(?:[^"\n\\]++|\\++(?!"))*+(\\*+)"'
_QUOTED_VALUE_SCAN = r'[^"\n]*+()"'


def test_quoted_key_value_rule_reads_each_backslash_run_once() -> None:
    """The run kept before the closing quote is read once, so the #1641 rule costs about
    what one scan to the quote does. A lazy value before the run costs 1000x+."""
    body = ('x "serial_num=' + "\\" * 20_000 + "\n") * 5
    rule, replacement = next(
        r for r in export.IDENTIFIER_RULES if _QUOTED_VALUE in r[0].pattern
    )
    scan = re.compile(
        rule.pattern.replace(_QUOTED_VALUE, _QUOTED_VALUE_SCAN), rule.flags
    )
    assert _cost_ratio(rule, scan, replacement, body) < 10


@pytest.mark.parametrize(
    ("raw", "scrubbed"),
    [
        ("--.ops@example.org", "--.user@example.test"),
        ("a@example.org+b@example.com", "user@example.testuser@example.test"),
        ("x@y@example.com", "x@user@example.test"),
        ("a.b-c@example no-at-sign", "a.b-c@example no-at-sign"),
    ],
)
def test_email_rule_starts_where_it_always_did(raw: str, scrubbed: str) -> None:
    """#1592 takes a whole run at once; an address inside or after one is still found."""
    rule, replacement = next(r for r in export.IDENTIFIER_RULES if "@" in r[0].pattern)
    assert rule.sub(replacement, raw) == scrubbed


def test_a_long_run_without_an_at_sign_is_scanned_once() -> None:
    """Regression #1592: the e-mail rule rescanned the rest of the run from each position.

    Timed against a rule that scans each run once, on the same body in the same run.
    The fixed rule costs under that baseline; the rescanning one cost 1000x+.
    """
    body = "<" + "a-" * 5_000
    rule, replacement = next(r for r in export.IDENTIFIER_RULES if "@" in r[0].pattern)
    once = re.compile(r"(?<![\w.+-])[\w.+-]++@")
    assert _cost_ratio(rule, once, replacement, body) < 10


def _cpu_time(rule: re.Pattern[str], replacement: Any, body: str) -> float:
    """The fastest of three CPU times of *rule* over *body*, which it leaves unchanged."""
    runs = []
    for _ in range(3):
        started = time.process_time()
        assert rule.sub(replacement, body) == body
        runs.append(time.process_time() - started)
    return min(runs)


@pytest.mark.parametrize(
    "element",
    [
        "SerialNumber",
        "IPAddress",
        "MACAddress",
        "SerialNumber\u3000",
        "SerialNumber\\!",
    ],
)
def test_one_opening_with_many_prefixes_does_not_backtrack(element: str) -> None:
    """Regression #1588: giving back each prefix re-ran the tag tail once per segment.

    A body of one unclosed opening whose prefixes all spell the element costs about
    what one unclosed opening of the same length does; retrying each split cost 100x+.
    """
    body = "<" + f"{element}:" * 3_000
    flat = f"<{element} " + "x" * (len(body) - len(element) - 2)
    tail_rules = [
        (rule, replacement)
        for rule, replacement in (*export.SECRET_RULES, *export.IDENTIFIER_RULES)
        if export._TAG_TAIL in rule.pattern and rule.search(f"<{element}>x<")
    ]
    assert tail_rules
    for rule, replacement in tail_rules:
        assert _cpu_time(rule, replacement, body) < 10 * max(
            _cpu_time(rule, replacement, flat), 1e-4
        )


def test_uuid_tokens_keep_case_and_are_consistent() -> None:
    corpus = export.tokenize_records(_records(_ssh("lssyscfg", LPAR_UUID.lower())))
    upper = corpus[1]["path"].rsplit("/", 1)[1]
    assert upper == "00000001-ABCD-4EF0-8ABC-000000000001"
    assert corpus[-1]["stdout"] == upper.lower()


def test_private_pattern_with_a_kept_group_is_still_a_leak() -> None:
    """Only the serial backstop's own `kept` group is exempt, never an operator's."""
    tok = export.Tokenizer({}, set(), [r"(?P<kept>ROOM-\d)"])
    assert tok.leaks("lab ROOM-7 rack") == ["ROOM-7"]


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


def test_several_name_related_enums_holding_the_literals_bind_none() -> None:
    """Ambiguity among enums named for the element leaves it unbound, not first-match.

    The controls show the same enums still bind when one alone holds the literals, or
    when the element names one exactly. Every enum here is name-related to `Thing`, so
    this never reaches an unrelated enum; `test_literals_alone_never_bind_an_unrelated_enum`
    guards that (#1498).
    """
    types = {"AThing.Enum": ["x", "y"], "BThing.Enum": ["x", "y", "z"]}
    assert export._bound_enum("Thing", {"x", "y"}, types) is None
    assert export._bound_enum("Thing", {"x", "z"}, types) == "BThing.Enum"
    assert export._bound_enum("AThing", {"x"}, types) == "AThing.Enum"


def test_one_literal_binds_only_a_same_named_enum() -> None:
    """`Status` seen once as OPERATIONAL must not bind to a VNIC status enum."""
    types = {"VirtualNICBackingDeviceStatus.Enum": ["OPERATIONAL", "DOWN"]}
    assert export._bound_enum("Status", {"OPERATIONAL"}, types) is None
    assert export._bound_enum("Status", {"OPERATIONAL", "DOWN"}, types) is not None


def test_literals_alone_never_bind_an_unrelated_enum() -> None:
    """An enum the element's name does not relate to is not bound by its literals (#1498)."""
    types = {"SRIOVPhysicalPortOptions.Enum": ["Veb", "fullDuplex", "linkUp"]}
    assert export._bound_enum("ConfiguredOptions", {"Veb", "fullDuplex"}, types) is None


PENDING = "pending authentication - password updates required"
STATE_ENUMS = {
    "types": {
        "SystemState.Enum": ["operating", PENDING],
        "PowerState.Enum": [PENDING],
        "BootMode.Enum": ["Normal"],
        "HostState.Enum": [PENDING],
        "MultiCoreScalingValue.Enum": ["1", "16"],
        "ConnectionSpeed.Enum": ["E10Gbps"],
    },
    "elements": {"PowerState": "BootMode.Enum"},
}


def _values(*leaves: tuple[str, str]) -> dict[str, list[str]]:
    body = "".join(f"<{e}>{v}</{e}>" for e, v in leaves)
    corpus = [_rest("/rest/api/uom/ManagedSystem", body)]
    return export.build_vocabulary(corpus, STATE_ENUMS, "vX", ["t"])["rest"]["values"]


def test_long_enum_member_is_kept_for_a_name_related_element() -> None:
    """#1292: `State` binds to no enum, yet `SystemState.Enum` lists the 50-char value."""
    assert _values(("State", f" {PENDING}\n"))["State"] == [PENDING]
    speed = _values(("CurrentConnectionSpeed", "E10Gbps"))["CurrentConnectionSpeed"]
    assert speed == ["E10Gbps"]
    assert _values(("State", PENDING.replace("pending", "pausing")))["State"] == [
        "<text>"
    ]
    assert _values(("State", PENDING.upper()))["State"] == ["<text>"]


def test_a_schema_bound_element_keeps_only_its_own_enum_members() -> None:
    assert _values(("PowerState", PENDING))["PowerState"] == ["<text>"]


def test_enum_members_do_not_reopen_name_bearing_or_classed_values() -> None:
    values = _values(("HostState", PENDING), ("MultiCoreScalingValue", "16"))
    assert values["HostState"] == ["<text>"]
    assert values["MultiCoreScalingValue"] == ["<int>"]


def test_committed_enums_keep_both_spellings_of_pending_authentication() -> None:
    enums = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures/live/vocabulary/enums-v11r2-p10-9080-hex.json"
        ).read_text(encoding="utf-8")
    )
    title = PENDING.title()
    corpus = [_rest("/rest/api/uom/ManagedSystem", f"<State>{PENDING}</State>")]
    corpus.append(_rest("/rest/api/uom/ManagedFrame", f"<State>{title}</State>"))
    vocab = export.build_vocabulary(corpus, enums, "vX", ["t"])
    assert vocab["rest"]["values"]["State"] == sorted([PENDING, title])
    assert "State" not in vocab["rest"]["element_enums"]


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


def test_path_template_reduces_an_absolute_href_to_its_path() -> None:
    # A link the HMC returned is fetched as given, so its record carries the host.
    assert (
        export.path_template(
            "https://hmc.test:443/rest/api/pcm/ProcessedMetrics/"
            "ManagedSystem_00000001-abcd-4ef0-8abc-000000000001_30.json"
        )
        == "/rest/api/pcm/ProcessedMetrics/{value}"
    )
    assert (
        export.path_template("https://hmc.test/rest/api/uom/jobs/7?group=Advanced")
        == "/rest/api/uom/jobs/{value}?group=Advanced"
    )
    assert (
        export.path_template("//rest/api/uom/jobs/7") == "//rest/api/uom/jobs/{value}"
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


#: Colon-separated hex after an escape whose letter is a hex digit, and what the export
#: writes in its place, after the escape. The MAC rules zero a MAC element; the IPv6
#: rule redacts the rest, an HMC record too, as the letter may open an address.
AFTER_HEX_ESCAPE = [
    ("<MACAddress>{}</MACAddress>", "0A:1B:2C:3D:4E:5F", "00:00:00:00:00:00"),
    ("mac {}", "0A:1B:2C:3D:4E:5F", "2001:db8::1"),
    ("addr {}", "fe80::1:2", "2001:db8::1"),
    ("slot {}", "1:0:1:3", "2001:db8::1"),
]


@pytest.mark.parametrize("before", ["\b", "\f"])
@pytest.mark.parametrize(("template", "value", "exported"), AFTER_HEX_ESCAPE)
def test_ipv6_rule_keeps_a_json_escape_letter_in_a_tool_record(
    before: str, template: str, value: str, exported: str
) -> None:
    """Regression #1643: `\\b0A:1B:...` became the invalid escape `\\2001:db8::1`, and
    `json.loads` raised. A tool record is tokenized as `json.dumps` text."""
    raw = template.format(before + value)
    tool = {"kind": "tool", "step": "t", "tool": "t", "ok": True, "data": {"raw": raw}}
    corpus = export.tokenize_records(_records(tool))
    assert corpus[-1]["record"]["data"]["raw"] == template.format(before + exported)


@pytest.mark.parametrize("before", ["\b", "\f"])
@pytest.mark.parametrize(("template", "value", "exported"), AFTER_HEX_ESCAPE[1:])
def test_ipv6_rule_keeps_an_escape_letter_in_json_text(
    before: str, template: str, value: str, exported: str
) -> None:
    """A REST body that is JSON holds the escape `\\b`; a tool record value that is JSON
    is written again by the export, as `\\\\b`. A MAC element in either fails closed,
    as the leak scan reads the letter as a digit."""
    raw = json.dumps(template.format(before + value))
    tool = {"kind": "tool", "step": "t", "tool": "t", "ok": True, "data": {"raw": raw}}
    corpus = export.tokenize_records(_records(_rest("/e", raw), tool))
    expected = json.dumps(template.format(before + exported))
    assert corpus[-2]["body"] == expected
    assert corpus[-1]["record"]["data"]["raw"] == expected


@pytest.mark.parametrize("before", ["\n", "\t", "\\\b"])
def test_ipv6_address_after_a_json_escape_is_redacted(before: str) -> None:
    """No word boundary follows the `n` of `\\n`, so the address after it was kept."""
    raw = f"addr {before}fe80::1:2"
    tool = {"kind": "tool", "step": "t", "tool": "t", "ok": True, "data": {"raw": raw}}
    corpus = export.tokenize_records(_records(_rest("/e", json.dumps(raw)), tool))
    assert corpus[-2]["body"] == json.dumps(f"addr {before}2001:db8::1")
    assert corpus[-1]["record"]["data"]["raw"] == f"addr {before}2001:db8::1"


@pytest.mark.parametrize(
    ("value", "exported"),
    [("C:\\b1:2:3:4:5:6:7:8", "C:\\b2001:db8::1"), ("C:\\f::abc", "C:\\f2001:db8::1")],
)
def test_address_after_a_literal_backslash_and_hex_letter_is_redacted(
    value: str, exported: str
) -> None:
    """In raw XML, and as `\\\\b` in JSON text, the letter opens the address."""
    tool = {
        "kind": "tool",
        "step": "t",
        "tool": "t",
        "ok": True,
        "data": {"raw": value},
    }
    corpus = export.tokenize_records(_records(_rest("/e", f"<a>{value}</a>"), tool))
    assert corpus[-2]["body"] == f"<a>{exported}</a>"
    assert corpus[-1]["record"]["data"]["raw"] == exported


def test_ipv6_rule_after_many_escapes_does_not_backtrack() -> None:
    """Each escape is matched once, with at most one bounded candidate after it."""
    body = "\\n1:0:1:3:0:5:1:2:0:1 \\t0:1 \\\\1:2:3 \\u00e9x " * 5_000
    pattern = export._IPV6_CANDIDATE
    without = re.compile(rf"(?i)(?P<escape>(?!))(?P<address>{pattern})?|\b{pattern}")
    assert _cost_ratio(export._IPV6, without, export._ipv6, body) < 10


def test_tool_record_the_rules_turn_into_invalid_json_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rule that breaks a JSON escape fails with the record named, not a traceback."""
    rules = ((re.compile(r"\\n"), r"\\q"), *export.IDENTIFIER_RULES)
    monkeypatch.setattr(export, "IDENTIFIER_RULES", rules)
    tool = {
        "kind": "tool",
        "step": "t",
        "tool": "t",
        "ok": True,
        "data": {"raw": "a\n"},
    }
    with pytest.raises(export.BrokenBodyError, match=r"tool record cap\.jsonl#5"):
        export.tokenize_records(_records(tool))


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
    # A keyword value: a hex one would now fail as a leak first (#1573).
    body = "<Adapter><MACAddress>ALL</MACAddress></Adapter>"
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


def test_each_source_is_its_own_entry(tmp_path: Path) -> None:
    corpus = tmp_path / "c.json"
    corpus.write_text("[]")
    enums = tmp_path / "e.json"
    enums.write_text('{"types": {}}')
    out = tmp_path / "v.json"
    args = ["vocabulary", str(corpus), "--enums", str(enums), "--firmware", "vX"]
    args += ["--source", "a, b", "--source", "c", "--out", str(out)]
    assert export.main(args) == 0
    assert json.loads(out.read_text())["sources"] == ["a, b", "c"]


def test_a_source_is_required(tmp_path: Path, capsys) -> None:
    args = ["vocabulary", "c.json", "--enums", "e.json", "--firmware", "vX"]
    with pytest.raises(SystemExit):
        export.main([*args, "--out", str(tmp_path / "v.json")])
    assert "--source" in capsys.readouterr().err


def test_location_suffix_wwn_is_redacted_and_lun_kept() -> None:
    """A `-L<hex>` suffix segment carries a disk WWN or RAID array id; `-L0` stays."""
    line = "U78D2.001.ABCD123-P1-C49-L5000C5000000ABCD-L0\n"
    corpus = export.tokenize_records(_records(_ssh("lshwres -r io", line)))
    assert corpus[-1]["stdout"] == "<REDACTED-LOC>-P1-C49-L<REDACTED-DEVID>-L0\n"


def test_surviving_location_suffix_wwn_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rules = tuple(r for r in export.IDENTIFIER_RULES if "L[0-9A-F]" not in r[0].pattern)
    monkeypatch.setattr(export, "IDENTIFIER_RULES", rules)
    with pytest.raises(export.LeakError):
        export.tokenize_records(_records(_ssh("lshwres", "x-P1-L5000C5000000ABCD-L0")))


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


@pytest.mark.parametrize(
    ("key", "token"),
    [
        ("VolumeUniqueID", "<REDACTED-DEVID>"),
        ("UniqueDeviceID", "<REDACTED-DEVID>"),
        ("volume_unique_id", "<REDACTED-DEVID>"),
        ("udid", "<REDACTED-DEVID>"),
        ("WWPNs", "<REDACTED-DEVID>"),
        ("WWPNPrefix", "<REDACTED-DEVID>"),
        ("wwpn_prefix", "<REDACTED-DEVID>"),
        ("LogicalUnitUDID", "<REDACTED-DEVID>"),
        ("SerialNumber", "<REDACTED-SERIAL>"),
        ("LogicalSerialNumber", "<REDACTED-SERIAL>"),
        ("serial_number", "<REDACTED-SERIAL>"),
        ("VirtualSerialNumber", "<REDACTED-SERIAL>"),
        ("virtual_serial_num", "<REDACTED-SERIAL>"),
    ],
)
def test_tool_output_identifiers_are_redacted(key: str, token: str) -> None:
    """Regression: tool output carried full device ids as JSON, which no text rule saw."""
    data = {
        "disks": [
            {
                key: "01MUlCvxMDE7Zq",
                "nested": {key: {"@attrs": {"ksv": "V1"}, "#text": "x9q"}},
            }
        ]
    }
    tool = {
        "kind": "tool",
        "step": "t",
        "tool": "hmc_list_storage",
        "ok": True,
        "data": data,
    }
    corpus = export.tokenize_records(_records(tool))
    disk = corpus[-1]["record"]["data"]["disks"][0]
    assert disk[key] == token
    assert disk["nested"][key] == {"@attrs": {"ksv": "V1"}, "#text": token}


@pytest.mark.parametrize(
    "key", ["VirtualSerialNumber", "virtual_serial_num", "SerialNumber"]
)
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("none", "none"),
        ("auto", "auto"),
        ("None", "<REDACTED-SERIAL>"),
        ("AUTO", "<REDACTED-SERIAL>"),
        ("auto ", "<REDACTED-SERIAL>"),
        ("noneSYN0001", "<REDACTED-SERIAL>"),
        ("SYN0001", "<REDACTED-SERIAL>"),
    ],
)
def test_tool_output_serial_keeps_only_keywords(
    key: str, value: str, expected: str
) -> None:
    """A serial's `none` and `auto` are evidence, as in a serial element (#1593).

    Only their exact text is kept.
    """
    data = {key: value, "nested": {key: {"@attrs": {"ksv": "V1"}, "#text": value}}}
    tool = {"kind": "tool", "step": "t", "tool": "x", "ok": True, "data": data}
    corpus = export.tokenize_records(_records(tool))
    out = corpus[-1]["record"]["data"]
    assert out[key] == expected
    assert out["nested"][key] == {"@attrs": {"ksv": "V1"}, "#text": expected}


def test_long_uppercase_hex_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A volume id that slips past every rule is caught by the scan."""
    monkeypatch.setattr(export, "redact_identifier_keys", lambda value: value)
    tool = {
        "kind": "tool",
        "step": "t",
        "tool": "x",
        "ok": True,
        "data": {"VolumeUniqueID": "3E2136005076800000000000000000AB03IBMfcp"},
    }
    with pytest.raises(export.LeakError):
        export.tokenize_records(_records(tool))


def test_lun_address_is_not_a_wwn() -> None:
    corpus = export.tokenize_records(_records(_ssh("lsmap", "lun 8100000000000000\n")))
    assert corpus[-1]["stdout"] == "lun 8100000000000000\n"


def test_surviving_16_hex_wwn_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    rules = tuple(r for r in export.IDENTIFIER_RULES if r[0].pattern != export._WWN_16)
    monkeypatch.setattr(export, "IDENTIFIER_RULES", rules)
    with pytest.raises(export.LeakError):
        export.tokenize_records(_records(_ssh("lsmap", "disk@5005076800000001,0")))


def test_wrapped_wwnn_text_is_redacted_in_tool_output() -> None:
    wrapped = {"@attrs": {"ksv": "V1_0"}, "text": "c05076000001"}
    tool = {
        "kind": "tool",
        "step": "t",
        "tool": "hmc_list_fc_ports",
        "ok": True,
        "data": [{"WWNN": wrapped, "WorldWideNodeName": "c05076000002"}],
    }
    corpus = export.tokenize_records(_records(tool))
    port = corpus[-1]["record"]["data"][0]
    assert port["WWNN"] == {"@attrs": {"ksv": "V1_0"}, "text": "<REDACTED-DEVID>"}
    assert port["WorldWideNodeName"] == "<REDACTED-DEVID>"


@pytest.mark.parametrize(
    ("name", "task"), [("HMC Logs", "View HMC Logs"), ("HMC User", "View HMC Users")]
)
def test_names_inside_built_in_task_names_are_not_collected(
    name: str, task: str
) -> None:
    """Regression: a name inside `View HMC …` task names failed four real exports."""
    body = (
        f"<TaskRole><Description>{name}</Description>"
        f'<ManagedTaskName kb="ROR">{task}</ManagedTaskName></TaskRole>'
    )
    corpus = export.tokenize_records(_records(_rest("/roles", body)))
    assert corpus[-1]["body"] == body


def test_available_wwpn_count_is_not_an_identifier() -> None:
    """AvailableWWPNs is how many WWPNs remain, a count the vocabulary keeps as <int>."""
    tool = {
        "kind": "tool",
        "step": "t",
        "tool": "hmc_get_system",
        "ok": True,
        "data": {"AvailableWWPNs": "65536"},
    }
    corpus = export.tokenize_records(_records(tool))
    assert corpus[-1]["record"]["data"] == {"AvailableWWPNs": "65536"}


def _lab_records() -> list[dict[str, Any]]:
    """The first tracked run's shape: lab-prefixed system, LPAR and VIOS names."""
    body = (
        "<ManagedSystem><SystemName>acmesys9</SystemName>"
        "<PartitionName>acmesys9-lp3</PartitionName>"
        "<PartitionName>acmesys9-vios1</PartitionName></ManagedSystem>"
    )
    tool = {
        "kind": "tool",
        "step": "hmc_list_lpars",
        "tool": "hmc_list_lpars",
        "ok": True,
        "data": [
            {"lpar_name": "acmesys9-lp3", "system": "acmesys9"},
            {"name": {"@attrs": {"ksv": "V1_0"}, "text": "acmesys9-vios1"}},
        ],
    }
    cli = _ssh(
        "lssyscfg -r lpar -m acmesys9 -F name,lpar_env --header",
        "name,lpar_env\nacmesys9-lp3,aixlinux\nacmesys9-vios1,vioserver\n",
    )
    records = [_rest("/rest/api/uom/ManagedSystem", body), tool, cli]
    for number, record in enumerate(records, 1):
        record["_src"] = f"lab.jsonl#{number}"
    return records


def test_lab_prefixed_names_leave_no_suffix() -> None:
    """Regression: `--private acme` cut the prefix first and `sys9-lp3` leaked."""
    corpus = export.tokenize_records(_lab_records(), private=["acme"])
    text = _text(corpus)
    assert "sys9" not in text
    assert "REDACTED-PRIVATE" not in text
    assert "sys-R1-lp3" in text
    assert "sys-R1-vios1" in text
    assert (
        corpus[2]["command"] == "lssyscfg -r lpar -m sys-R1 -F name,lpar_env --header"
    )


def test_private_match_replaces_its_whole_word() -> None:
    corpus = export.tokenize_records(
        _records(_ssh("lshmc -n", "via acmebox9.example ok")), private=["acme"]
    )
    assert corpus[-1]["stdout"] == "via <REDACTED-PRIVATE> ok"


def test_private_fragment_left_joined_to_a_word_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A private match that leaves the rest of its word behind is a leak."""
    monkeypatch.setattr(
        export.Tokenizer,
        "__init__",
        _fragment_only(export.Tokenizer.__init__),
    )
    with pytest.raises(export.LeakError):
        export.tokenize_records(
            _records(_ssh("lshmc -n", "via acmebox9 ok")), private=["acme"]
        )


def _fragment_only(init: Any) -> Any:
    def patched(self: Any, names: Any, hosts: Any, private: Any = ()) -> None:
        init(self, names, hosts, private)
        self._private_words = [re.compile(p) for p in private]

    return patched
