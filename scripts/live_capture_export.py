"""Tokenize private HMC captures and derive the committed vocabularies (issue #1202).

Three subcommands, all offline:

    python scripts/live_capture_export.py tokenize RAW.jsonl... --out corpus.json \\
        [--private REGEX]...
    python scripts/live_capture_export.py vocabulary corpus.json --enums enums.json \\
        --firmware v10r3-p9 --source TEXT --out vocabulary.json
    python scripts/live_capture_export.py enums corpus.json --firmware v10r3 --out enums.json

`tokenize` reads the records `scripts/live_capture_sweep.py` writes (the capture
harness's `rest`/`ssh` records and the sweep's `tool`/`skip` records) and writes a
tokenized corpus. It is fail-closed: when a collected name, a URL host, an IP address,
a location code, a session value, an SSH key or a `--private` match survives, it exits 1
and writes nothing. The corpus stays private; only `vocabulary` and `enums` output is
committed, and both run the same scan over what they write.

The rules are in docs/workflow/specs/2026-09-30-live-capture-pipeline-design.md.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import re
import shlex
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

UUID = re.compile(
    r"\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\b"
)
#: A word for whole-word name replacement: dots and hyphens join, edges are word chars.
WORD = re.compile(r"\w[\w.-]*\w|\w")
#: Namespace and schema hosts that are public and structural, never a lab host.
PRESERVED_HOSTS = ("www.ibm.com", "www.w3.org")
PLACEHOLDER_IP = "192.0.2.1"
HMC_HOST = "hmc.test"

#: XML elements whose text is a name, and the token kind each becomes.
NAME_ELEMENTS = {
    "SystemName": "sys",
    "PartitionName": "lpar",
    "UserName": "user",
    "ManagementConsoleName": "hmc",
    "ProfileName": "prof",
    "PartitionProfileName": "prof",
    "LastActivatedProfile": "prof",
    "HostName": "host",
    "VolumeGroupName": "vg",
    "MediaName": "media",
    "DeviceName": "dev",
    "TargetName": "dev",
    "BackingDeviceName": "dev",
    "DiskName": "dev",
    "VolumeName": "dev",
    "AdapterName": "dev",
    "ClusterName": "cluster",
    "StoragePoolName": "pool",
    "NetworkName": "net",
    "SwitchName": "vswitch",
    "PartitionTemplateName": "tmpl",
    "templateName": "tmpl",
    "LogicalUnitName": "lu",
    "Description": "desc",
}
#: CLI output fields whose value is a name, and the token kind each becomes.
NAME_FIELDS = {
    "name": "lpar",
    "lpar_name": "lpar",
    "curr_lpar_names": "lpar",
    "lpar_names": "lpar",
    "vios_name": "lpar",
    "profile_name": "prof",
    "curr_profile": "prof",
    "default_profile": "prof",
    "label": "label",
    "group_name": "label",
    "resource_group_name": "label",
    "description": "desc",
    "pool_name": "pool",
    "backup_name": "bk",
}
TOKEN_KINDS = sorted(set(NAME_ELEMENTS.values()) | set(NAME_FIELDS.values()))
#: Values that look like names but are words every HMC prints.
_NOT_NAMES = {"null", "none", "default", "default_profile", "true", "false"}

Replacement = str | Callable[[re.Match[str]], str]

#: Elements whose whole text is a device identifier.
_DEVICE_ID_ELEMENTS = (
    "VolumeUniqueID|UniqueDeviceID|DescriptorPage83|GroupSerialID|UDID|UniqueID"
    "|SerialNumberOfDisk|DeviceSerialNumber|WorldWidePortName|WWPN|PortWWPN"
)
_ADDRESS_ELEMENTS = "NetworkAddress|IPAddress|PrimaryIPAddress|IPv6Address"


def _ipv6(match: re.Match[str]) -> str:
    """Redact an IPv6 address, but not a colon-separated HMC record such as `1:0:1:3`.

    An address has a `::`, a group of three or more digits, a hex letter, or the
    link-local prefix; SR-IOV and slot records are short decimal fields only.
    """
    text = match.group(0)
    groups = text.split(":")
    if (
        "::" in text
        or text.lower().startswith("fe80")
        or any(len(g) >= 3 or re.search("[a-f]", g, re.IGNORECASE) for g in groups)
    ):
        return "2001:db8::1"
    return text


# Replacements write `\g<1>`, never `\1` before a digit: `\1000000000000` is the
# octal escape for `@` and deleted the opening <MACAddress> tag 959 times.
SECRET_RULES: tuple[tuple[re.Pattern[str], Replacement], ...] = (
    # Both key elements are redacted whole; an SSH key in free text loses its body
    # and its comment, which is conventionally user@host, up to the next `<`.
    (
        re.compile(
            r"(<(?:\w+:)?(?:PublicSSHKeyValue|AuthorizedKeysValue)\b[^>]*>)[^<]+"
        ),
        r"\g<1><REDACTED-SSHKEY>",
    ),
    (
        re.compile(
            r"\b(ssh-(?:rsa|ed25519|dss)|ecdsa-sha2-[\w-]+|sk-[\w@.-]+)"
            r"\s+[A-Za-z0-9+/=]{16,}(?:[ \t]+[^\s<\"',}\]]+)?"
        ),
        r"\g<1> <REDACTED-SSHKEY>",
    ),
    (re.compile(r"(?i)\b(x-api-session=)[^,}\s]+"), r"\g<1><REDACTED-SESSION>"),
    (re.compile(r"(?i)(<X-API-Session\b[^>]*>)[^<]+"), r"\g<1><REDACTED-SESSION>"),
    (
        re.compile(r"(?i)\b(JSESSIONID|CCFWSESSION|LtpaToken2)=[^;,}\s]+"),
        r"\g<1>=<REDACTED-COOKIE>",
    ),
    (re.compile(r"(?i)\b(cookie=)[^,}\n]+"), r"\g<1><REDACTED-COOKIE>"),
)
IDENTIFIER_RULES: tuple[tuple[re.Pattern[str], Replacement], ...] = (
    (
        re.compile(r"(?i)\b[\w.+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+\b"),
        "user@example.test",
    ),
    (
        re.compile(
            r"(?<![\w.-])(?!(?:www\.ibm\.com|www\.w3\.org)\b)"
            r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.(?:ibm\.com|internal|local|lab|lan|corp)\b"
        ),
        HMC_HOST,
    ),
    (
        re.compile(rf"(<(?:\w+:)?(?:{_ADDRESS_ELEMENTS})\b[^>]*>)[^<]+"),
        rf"\g<1>{PLACEHOLDER_IP}",
    ),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), PLACEHOLDER_IP),
    # Anywhere, even inside `1eU8375.42A.XXXXXXX-V100-C3`; the slot suffix after the
    # prefix is generic and code joins on it, so it stays.
    (re.compile(r"U[0-9A-Za-z]{4}\.[0-9A-Za-z]{3}\.[0-9A-Za-z]{7}"), "<REDACTED-LOC>"),
    (
        re.compile(rf"(<(?:\w+:)?(?:{_DEVICE_ID_ELEMENTS})\b[^>]*>)[^<]+"),
        r"\g<1><REDACTED-DEVID>",
    ),
    (
        re.compile(r"(?i)\b(unique_id|udid|wwpn|serial_num)=[^,\n\"]+"),
        r"\g<1>=<REDACTED-DEVID>",
    ),
    (
        re.compile(r"(<(?:\w+:)?(?:Logical)?SerialNumber\b[^>]*>)[^<]+"),
        r"\g<1><REDACTED-SERIAL>",
    ),
    (re.compile(r"\b(\d{4}-[0-9A-Z]{3}\*)[0-9A-Z]{7}\b"), r"\g<1><REDACTED-SERIAL>"),
    (re.compile(r"(<(?:\w+:)?MACAddress\b[^>]*>)[^<]+"), r"\g<1>000000000000"),
    (re.compile(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b"), "00:00:00:00:00:00"),
    (re.compile(r"(?i)(mac_addr=)[0-9a-f]{12}"), r"\g<1>000000000000"),
    (re.compile(r"(?i)\b(?:fe80|[0-9a-f]{1,4})(?::[0-9a-f]{0,4}){2,7}\b"), _ipv6),
    (re.compile(r"(?i)\bc0?50[0-9a-f]{13,14}\b"), "c050760000000000"),
    (re.compile(r"(?i)\b(wwpns=)[0-9a-f,]+"), r"\g<1>c050760000000000"),
)
#: Shapes that may never appear in a tokenized output, whatever was collected.
ALWAYS_LEAKS = (
    re.compile(rf"\b(?!{re.escape(PLACEHOLDER_IP)}\b)(?:\d{{1,3}}\.){{3}}\d{{1,3}}\b"),
    re.compile(r"U[0-9A-Za-z]{4}\.[0-9A-Za-z]{3}\.[0-9A-Za-z]{7}"),
    re.compile(r"(?i)x-api-session=(?!<REDACTED)\w"),
    re.compile(r"\b(?:ssh-(?:rsa|ed25519|dss)|ecdsa-sha2-[\w-]+) +AAAA"),
)
#: HMC sentinels and messages: data the tests need verbatim, never a name.
SENTINELS = {
    "No results were found",
    "null",
    "none",
    "None",
    "N/A",
    "unavailable",
    "Unknown",
}


class ExportError(RuntimeError):
    """The export is unsafe or corrupt; nothing was written."""


class LeakError(ExportError):
    """A tokenized output still carries something private."""


class BrokenBodyError(ExportError):
    """Tokenizing turned a body that parsed as XML into one that does not."""


# --- collection -------------------------------------------------------------


def structural_identifiers(texts: Iterable[str]) -> set[str]:
    """Element, attribute, JSON-key and CLI-field names: structure, never a name."""
    found: set[str] = set()
    for text in texts:
        found.update(re.findall(r"</?(?:[\w.-]+:)?([A-Za-z_][\w.-]*)", text))
        # Texts are JSON-encoded records, so an attribute's quote arrives escaped.
        found.update(re.findall(r"\s(?:[\w.-]+:)?([A-Za-z_][\w.-]*)=\\?[\"']", text))
        found.update(re.findall(r"\"([A-Za-z_][\w-]*)\"\s*:", text))
        found.update(re.findall(r"(?:^|[,\"])([a-z_][a-z0-9_]*)=", text, re.MULTILINE))
    return found


def schema_enum_values(records: Iterable[dict[str, Any]]) -> set[str]:
    """Every enumeration value in any captured XSD body."""
    values: set[str] = set()
    for record in records:
        body = record.get("body") or ""
        if "enumeration" in body and "schema" in body:
            values.update(
                re.findall(r"<(?:\w+:)?enumeration\s+value=\"([^\"]*)\"", body)
            )
    return values


def _cli_fields(command: str) -> list[str] | None:
    """The `-F` field list of *command*, or None when it prints native output."""
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if "-F" not in words:
        return None
    index = words.index("-F") + 1
    if index >= len(words) or words[index].startswith("-"):
        return []
    return words[index].split(",")


def cli_rows(command: str, stdout: str) -> list[dict[str, str]]:
    """Parse `ls*` output into rows of field -> value, `-F` lists and native k=v both."""
    lines = [line for line in stdout.splitlines() if line.strip()]
    fields = _cli_fields(command)
    if fields is not None and ("--header" in command or not fields):
        if not lines:
            return []
        fields = next(csv.reader([lines[0]]))
        lines = lines[1:]
    rows = []
    for line in lines:
        values = next(csv.reader([line]))
        if fields:
            rows.append(dict(zip(fields, values, strict=False)))
        else:
            pairs = (value.split("=", 1) for value in values if "=" in value)
            rows.append({key: value for key, value in pairs})
    return rows


class NameCollector:
    """Maps each name found in the captures to a stable token."""

    def __init__(self, excluded: set[str]) -> None:
        self.names: dict[str, str] = {}
        self._excluded = excluded
        self._counts: collections.Counter[str] = collections.Counter()

    def add(self, kind: str, value: str | None) -> None:
        value = (value or "").strip().strip('"')
        if (
            len(value) < 3
            or value in self.names
            or value in self._excluded
            or value.lower() in _NOT_NAMES
            or value.rstrip(".") in SENTINELS
            or value.startswith(("HSCL", "No results"))
            or re.fullmatch(r"[0-9.]+", value)
            or UUID.fullmatch(value)
        ):
            return
        self._counts[kind] += 1
        n = self._counts[kind]
        self.names[value] = f"sys-R{n}" if kind == "sys" else f"{kind}-{n}"

    def finish(self) -> dict[str, str]:
        """Give a partition named after its system the system's token plus its suffix."""
        systems = {n: t for n, t in self.names.items() if t.startswith("sys-R")}
        for name in list(self.names):
            for system, token in systems.items():
                if name != system and name.startswith(system + "-"):
                    self.names[name] = token + name[len(system) :]
        return self.names


def collect_names(records: Sequence[dict[str, Any]]) -> dict[str, str]:
    texts = [_text_of(record) for record in records]
    excluded = structural_identifiers(texts) | schema_enum_values(records)
    collector = NameCollector(excluded)
    blob = "\n".join(texts)
    for element, kind in NAME_ELEMENTS.items():
        pattern = rf"<(?:\w+:)?{element}(?:\s[^>]*)?>([^<]+)</(?:\w+:)?{element}>"
        for value in re.findall(pattern, blob):
            collector.add(kind, value)
    for record in records:
        if record.get("kind") != "ssh" or not record.get("stdout"):
            continue
        for row in cli_rows(record.get("command") or "", record["stdout"]):
            for field, value in row.items():
                if field in NAME_FIELDS:
                    for part in value.split(","):
                        collector.add(NAME_FIELDS[field], part)
    return collector.finish()


def url_hosts(texts: Iterable[str]) -> set[str]:
    """Hosts of every `https://` URL, the HMC's own address among them."""
    hosts = set()
    for text in texts:
        hosts.update(re.findall(r"https://([A-Za-z0-9.-]+)", text))
    return {h for h in hosts if h not in PRESERVED_HOSTS and h != HMC_HOST}


def _text_of(record: dict[str, Any]) -> str:
    return json.dumps(record, default=str)


# --- tokenizing --------------------------------------------------------------


class Tokenizer:
    def __init__(
        self, names: dict[str, str], hosts: set[str], private: Sequence[str] = ()
    ) -> None:
        self.names = names
        self.hosts = hosts
        self.private = [re.compile(p) for p in private]
        self._uuids: dict[str, str] = {}
        spaced = sorted(
            (n for n in names if not WORD.fullmatch(n)), key=len, reverse=True
        )
        self._spaced = (
            re.compile("|".join(rf"(?<!\w){re.escape(n)}(?!\w)" for n in spaced))
            if spaced
            else None
        )
        host_alternation = "|".join(
            re.escape(h) for h in sorted(hosts, key=len, reverse=True)
        )
        self._hosts = (
            re.compile(rf"(?<![\w.-])(?:{host_alternation})(?::\d+)?(?![\w-])")
            if hosts
            else None
        )

    def _uuid(self, match: re.Match[str]) -> str:
        original = match.group(0)
        key = original.lower()
        if key not in self._uuids:
            n = len(self._uuids) + 1
            self._uuids[key] = f"{n:08x}-abcd-4ef0-8abc-{n:012x}"
        token = self._uuids[key]
        return token.upper() if original == original.upper() else token

    def _word(self, match: re.Match[str]) -> str:
        word = match.group(0)
        if word in self.names:
            return self.names[word]
        # A trailing dot belongs to the sentence, not the name.
        if word.endswith(".") and word[:-1] in self.names:
            return self.names[word[:-1]] + "."
        return word

    def tokenize(self, text: str | None) -> str | None:
        if text is None:
            return None
        xml = text.lstrip().startswith("<")
        text = UUID.sub(self._uuid, text)
        for pattern, replacement in SECRET_RULES:
            text = pattern.sub(replacement, text)
        if self._hosts:
            text = self._hosts.sub(f"{HMC_HOST}:443", text)
        for pattern, replacement in IDENTIFIER_RULES:
            text = pattern.sub(replacement, text)
        for pattern in self.private:
            text = pattern.sub("<REDACTED-PRIVATE>", text)
        if self._spaced:
            text = self._spaced.sub(lambda m: self.names[m.group(0)], text)
        text = WORD.sub(self._word, text)
        if xml:
            # The token must not open an element, or the body would stop parsing.
            text = re.sub(r"<(REDACTED-[A-Z]+)>", r"&lt;\1&gt;", text)
        return text

    def leaks(self, text: str) -> list[str]:
        """Everything private still present in *text*."""
        words = set(WORD.findall(text))
        found = [
            name
            for name in self.names
            if len(name) >= 4
            and (name in words if WORD.fullmatch(name) else name in text)
        ]
        found += [host for host in self.hosts if host in text]
        for pattern in (*ALWAYS_LEAKS, *self.private):
            found += pattern.findall(text)
        return found


def _tokenize_record(tok: Tokenizer, record: dict[str, Any]) -> dict[str, Any]:
    kind = record.get("kind")
    out: dict[str, Any] = {
        "capture": record["_src"],
        "step": record.get("step"),
        "kind": kind,
    }
    if kind == "rest":
        headers = {
            k.lower(): v for k, v in (record.get("response_headers") or {}).items()
        }
        out.update(
            method=record.get("method"),
            path=tok.tokenize(record.get("path")),
            accept=record.get("accept"),
            status=record.get("status"),
            content_type=headers.get("content-type"),
            body=tok.tokenize(record.get("body")),
            exception=tok.tokenize(record.get("exception")),
        )
    elif kind == "ssh":
        out.update(
            command=tok.tokenize(record.get("command")),
            exit_status=record.get("exit_status"),
            stdout=tok.tokenize(record.get("stdout")),
            stderr=tok.tokenize(record.get("stderr")),
            exception=tok.tokenize(record.get("exception")),
        )
    else:
        rest = {
            k: v for k, v in record.items() if k not in ("_src", "step", "kind", "t")
        }
        out["record"] = json.loads(tok.tokenize(json.dumps(rest, default=str)) or "{}")
    return out


def tokenize_records(
    records: Sequence[dict[str, Any]], private: Sequence[str] = ()
) -> list[dict[str, Any]]:
    """Tokenize *records*; raise LeakError when anything private survives."""
    tok = Tokenizer(collect_names(records), url_hosts(map(_text_of, records)), private)
    corpus = [_tokenize_record(tok, record) for record in records]
    leaks = tok.leaks(json.dumps(corpus))
    if leaks:
        raise LeakError(
            f"{len(leaks)} private value(s) survived tokenizing; nothing written"
        )
    broken = [
        out["capture"]
        for raw, out in zip(records, corpus, strict=True)
        if _parses(raw.get("body")) and not _parses(out.get("body"))
    ]
    if broken:
        raise BrokenBodyError(
            f"tokenizing broke the XML of {len(broken)} body(ies), first {broken[0]}; "
            "nothing written"
        )
    return corpus


def _parses(body: Any) -> bool:
    """Whether *body* is an XML document (feed, entry or schema) that parses."""
    if not isinstance(body, str) or not body.lstrip().startswith("<"):
        return False
    try:
        ET.fromstring(body.encode("utf-8"))
    except ET.ParseError:
        return False
    return True


def load_raw(paths: Sequence[Path]) -> list[dict[str, Any]]:
    records = []
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if line.strip():
                    record = json.loads(line)
                    record["_src"] = f"{path.name}#{number}"
                    records.append(record)
    return records


# --- vocabulary --------------------------------------------------------------

LEAF = re.compile(
    r"<(?:[\w.-]+:)?([A-Za-z][\w.]*)(?:\s[^>]*)?>([^<]*)</(?:[\w.-]+:)?\1>"
)
#: Elements and fields whose values identify something; only their shape is kept.
NAME_BEARING = re.compile(
    r"(?i)name|description|serial|mac|host|address|location|loc$|wwpn|^ip|_ip|ip_|uuid"
    r"|id$|_ids?$|link|key|session|user|date|time|href|label|path|url|drc|vpd|unique"
    r"|profile$|role$"
)
LITERAL = re.compile(r"[A-Za-z][A-Za-z _/+-]{0,40}")
TOKEN = re.compile(
    rf"\b(?:sys-R\d+(?:-[\w]+)?|(?:{'|'.join(TOKEN_KINDS)})-\d+)\b|&lt;REDACTED-\w+&gt;|<REDACTED-\w+>"
)
_STRUCTURAL_SEGMENT = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:\.xsd)?")


def shape(value: str) -> str:
    v = value.strip()
    if v == "":
        return "<empty>"
    if re.fullmatch(r"-?\d+", v):
        return "<int>"
    if re.fullmatch(r"-?\d+\.\d+", v):
        return "<float>"
    if UUID.fullmatch(v):
        if v == v.lower():
            return "<uuid-lower>"
        return "<uuid-upper>" if v == v.upper() else "<uuid-mixed>"
    if v in ("true", "false") or (LITERAL.fullmatch(v) and not TOKEN.search(v)):
        return v
    return "<text>"


def observed(field: str, value: str) -> str:
    kept = shape(value)
    if kept.startswith("<") or kept in ("true", "false"):
        return kept
    return "<text>" if NAME_BEARING.search(field) else kept


def path_template(path: str) -> str:
    """A captured path with identifiers templated; structure and `group=` kept."""
    base, _, query = path.partition("?")
    base = UUID.sub("{uuid}", base)
    base = re.sub(r"\(([A-Za-z]+)==[^)]*\)", r"(\1=={value})", base)
    segments = [
        s
        if not s or s.startswith(("{", "(")) or _STRUCTURAL_SEGMENT.fullmatch(s)
        else "{value}"
        for s in base.split("/")
    ]
    templated = "/".join(segments)
    if not query:
        return templated
    params = []
    for param in query.split("&"):
        key = param.partition("=")[0]
        params.append(param if key == "group" else f"{key}={{value}}")
    return templated + "?" + "&".join(params)


#: Option flags whose value identifies something; templated as `{}`.
_VALUE_FLAGS = {"-m", "-p", "--id", "--ip", "-e"}
#: Option flags whose value selects what the command reads; part of a command's key.
KEY_FLAGS = ("-r", "--rsubtype", "--level", "-o")


def command_template(command: str) -> str:
    words = shlex.split(command)
    out: list[str] = []
    previous = ""
    for word in words:
        if previous in _VALUE_FLAGS:
            word = "{}"
        elif previous == "--filter":
            word = ",".join(f"{p.split('=', 1)[0]}={{}}" for p in word.split(","))
        out.append(word)
        previous = word if word.startswith("-") else ""
    return " ".join(out)


def command_key(command: str) -> str:
    """Command name plus its selecting flags, e.g. `lshwres -r sriov --rsubtype adapter`."""
    words = shlex.split(command)
    key = [words[0]] if words else []
    for flag in KEY_FLAGS:
        if flag in words[:-1]:
            key += [flag, words[words.index(flag) + 1]]
    return " ".join(key)


def _template_message(text: str) -> str:
    text = UUID.sub("{uuid}", text)
    return TOKEN.sub("{name}", text).strip()[:300]


def _error_form(record: dict[str, Any]) -> dict[str, Any]:
    body = record.get("body") or ""
    title = re.search(r"<title>([^<]*)</title>", body)
    reason = re.search(r"<(?:\w+:)?ReasonCode\b[^>]*>([^<]*)<", body)
    message = re.search(r"<(?:\w+:)?Message\b[^>]*>([^<]*)<", body)
    return {
        "method": record.get("method"),
        "path": path_template(record.get("path") or ""),
        "status": record.get("status"),
        "content_type": record.get("content_type"),
        "form": title.group(1).strip()
        if title
        else ("<empty>" if not body.strip() else "<text>"),
        "reason_code": _template_message(reason.group(1)) if reason else None,
        "message": _template_message(message.group(1)) if message else None,
    }


def _sentinel(command: str, stdout: str) -> str | None:
    """A one-line fixed message such as `No results were found.`, kept verbatim.

    Only from a command whose output fields name nothing, so a one-line description
    or partition name is never mistaken for a message.
    """
    line = stdout.strip()
    fields = _cli_fields(command) or []
    if (
        "\n" in line
        or "=" in line
        or not re.fullmatch(r"[A-Za-z][A-Za-z ,.'()-]{0,120}", line)
        or TOKEN.search(line)
        or any(NAME_BEARING.search(field) for field in fields)
    ):
        return None
    return line


def _bound_enum(
    element: str, literals: set[str], types: dict[str, list[str]]
) -> str | None:
    """The one enum type whose values hold every literal observed for *element*.

    A same-named enum binds on one literal. Any looser match needs two, because one
    value is weak evidence: `Status` observed only as `OPERATIONAL` would otherwise bind
    to a VNIC enum and then judge every job status against it.
    """
    if not literals:
        return None
    candidates = [t for t, values in types.items() if literals <= set(values)]
    rules = [lambda base: base == element]
    if len(literals) >= 2:
        rules += [
            lambda base: base.endswith(element) or element.endswith(base),
            lambda base: True,
        ]
    for rule in rules:
        matches = [t for t in candidates if rule(t.removesuffix(".Enum"))]
        if len(matches) == 1:
            return matches[0]
        if matches:
            return None
    return None


def _accept(record: dict[str, Any]) -> str | None:
    # The 2026-09-30 corpus predates the tracked harness and records request headers.
    return record.get("accept") or (record.get("req_headers") or {}).get("Accept")


def _is_schema(record: dict[str, Any]) -> bool:
    path = record.get("path") or ""
    return path.endswith(".xsd") or "/schema" in path


def build_vocabulary(
    corpus: Sequence[dict[str, Any]], enums: dict[str, Any], firmware: str, source: str
) -> dict[str, Any]:
    endpoints: set[tuple[Any, ...]] = set()
    errors: list[dict[str, Any]] = []
    values: dict[str, set[str]] = collections.defaultdict(set)
    commands: dict[str, dict[str, set[Any]]] = collections.defaultdict(
        lambda: {"exit_status": set(), "codes": set(), "outputs": set()}
    )
    cli_values: dict[str, set[str]] = collections.defaultdict(set)
    for record in corpus:
        if record.get("kind") == "rest" and record.get("status") is not None:
            template = path_template(record.get("path") or "")
            endpoints.add(
                (
                    record["method"],
                    template,
                    _accept(record),
                    record["status"],
                    record.get("content_type"),
                )
            )
            if record["status"] >= 400:
                error = _error_form(record)
                if error not in errors:
                    errors.append(error)
            elif not _is_schema(record):
                for element, value in LEAF.findall(record.get("body") or ""):
                    values[element].add(observed(element, value))
        elif record.get("kind") == "ssh" and record.get("command"):
            command = record["command"]
            entry = commands[command_template(command)]
            entry["exit_status"].add(record.get("exit_status"))
            output = (record.get("stdout") or "") + (record.get("stderr") or "")
            entry["codes"].update(re.findall(r"\bHSCL\w{4}\b", output))
            sentinel = _sentinel(command, record.get("stdout") or "")
            if sentinel:
                entry["outputs"].add(sentinel)
            if record.get("exit_status") == 0:
                key = command_key(command)
                for row in cli_rows(command, record.get("stdout") or ""):
                    for field, value in row.items():
                        cli_values[f"{key} :: {field}"].add(observed(field, value))
    types = enums.get("types", {})
    bindings = dict(enums.get("elements", {}))
    for element, seen in values.items():
        if element not in bindings:
            literals = {
                v for v in seen if not v.startswith("<") and v not in ("true", "false")
            }
            bound = _bound_enum(element, literals, types)
            if bound:
                bindings[element] = bound
    return {
        "firmware": firmware,
        "source": source,
        "rest": {
            "endpoints": [
                dict(
                    zip(
                        ("method", "path", "accept", "status", "content_type"),
                        e,
                        strict=True,
                    )
                )
                for e in sorted(endpoints, key=lambda e: tuple(str(x) for x in e))
            ],
            "errors": sorted(errors, key=lambda e: json.dumps(e, sort_keys=True)),
            "values": {k: sorted(v) for k, v in sorted(values.items())},
            "element_enums": {k: bindings[k] for k in sorted(bindings) if k in values},
        },
        "cli": {
            "commands": [
                {
                    "command": template,
                    "exit_status": sorted(e["exit_status"], key=str),
                    "codes": sorted(e["codes"]),
                    "outputs": sorted(e["outputs"]),
                }
                for template, e in sorted(commands.items())
            ],
            "values": {k: sorted(v) for k, v in sorted(cli_values.items())},
        },
    }


# --- schema enums ------------------------------------------------------------

_XSD = "{http://www.w3.org/2001/XMLSchema}"


def derive_enums(corpus: Sequence[dict[str, Any]], firmware: str) -> dict[str, Any]:
    """Enum types from the captured `Enumerations.xsd`, bindings from any captured XSD."""
    types: dict[str, list[str]] = {}
    elements: dict[str, str] = {}
    for record in corpus:
        body = record.get("body") or ""
        if (
            record.get("kind") != "rest"
            or record.get("status") != 200
            or not _is_schema(record)
        ):
            continue
        try:
            root = ET.fromstring(body.encode("utf-8"))
        except ET.ParseError:
            continue
        for simple in root.iter(f"{_XSD}simpleType"):
            name = simple.get("name") or ""
            if name.endswith(".Enum"):
                types[name] = [
                    e.get("value") or "" for e in simple.iter(f"{_XSD}enumeration")
                ]
        for element in root.iter(f"{_XSD}element"):
            name = element.get("name")
            bound = element.get("type") or next(
                (ext.get("base") for ext in element.iter(f"{_XSD}extension")), None
            )
            if name and bound and bound.endswith(".Enum"):
                elements[name] = bound
    if not types:
        raise ValueError("no Enumerations.xsd record with enum types in the corpus")
    return {
        "firmware": firmware,
        "types": dict(sorted(types.items())),
        "elements": dict(sorted(elements.items())),
    }


# --- entry point -------------------------------------------------------------


def _write_scanned(data: Any, out: Path, private: Sequence[str]) -> None:
    text = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
    leaks = Tokenizer({}, set(), private).leaks(text)
    if leaks:
        raise LeakError(
            f"{len(leaks)} private value(s) in the derived output; nothing written"
        )
    out.write_text(text, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    tok = sub.add_parser(
        "tokenize", help="tokenize raw capture JSONL into a private corpus"
    )
    tok.add_argument("raw", nargs="+", type=Path)
    vocab = sub.add_parser("vocabulary", help="derive a committed vocabulary")
    vocab.add_argument("corpus", type=Path)
    vocab.add_argument("--enums", type=Path, required=True)
    vocab.add_argument("--firmware", required=True)
    vocab.add_argument("--source", required=True)
    enum = sub.add_parser("enums", help="derive the committed schema enum lists")
    enum.add_argument("corpus", type=Path)
    enum.add_argument("--firmware", required=True)
    for p in (tok, vocab, enum):
        p.add_argument("--out", type=Path, required=True)
        p.add_argument(
            "--private",
            action="append",
            default=[],
            metavar="REGEX",
            help="a lab name or host pattern that must not survive (repeatable)",
        )
    args = parser.parse_args(argv)
    try:
        if args.command == "tokenize":
            corpus = tokenize_records(load_raw(args.raw), args.private)
            fd = os.open(
                args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(corpus, handle, indent=1)
            print(f"{len(corpus)} records tokenized -> {args.out}")
            return 0
        corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
        if args.command == "enums":
            data = derive_enums(corpus, args.firmware)
        else:
            enums = json.loads(args.enums.read_text(encoding="utf-8"))
            data = build_vocabulary(corpus, enums, args.firmware, args.source)
        _write_scanned(data, args.out, args.private)
        print(f"wrote {args.out}")
        return 0
    except (ExportError, ValueError, OSError) as exc:
        print(f"live_capture_export: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
