"""Validate the versioned HMC reference capability inventory."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = ROOT / "docs" / "capabilities"
HEX_256 = re.compile(r"(?:[0-9a-f]{8}-){7}[0-9a-f]{8}")
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*")
TABLE_SEPARATOR = re.compile(r"^\|(?:\s*:?-+:?\s*\|)+$")
CAPTURE = re.compile(r"^(?:captured|Captured):\s*(\S.*)$", re.MULTILINE)
METHODS = {"GET", "POST", "PUT", "DELETE"}
DISPOSITIONS = {"supported", "coverage-child", "proposed-exclusion", "unknown"}
MATURITY_STATES = {"absent", "partial", "implemented"}
EVIDENCE_CHANNELS = {"contract-review", "automated", "live"}
EVIDENCE_RESULTS = {"not-run", "skipped", "failed", "passed"}
EVIDENCE_CURRENCY = {"current", "stale"}
SHA_1 = re.compile(r"[0-9a-f]{40}")
SHA_256 = re.compile(r"[0-9a-f]{64}")
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


class InventoryError(ValueError):
    """An inventory artifact cannot be parsed safely."""


@dataclass(frozen=True)
class RegistryTool:
    tool: str
    operation: str
    handler: str
    signature: str
    surfaces: tuple[str, ...]


@dataclass(frozen=True)
class Report:
    errors: tuple[str, ...]
    unknown_ids: tuple[str, ...]
    proposed_exclusion_ids: tuple[str, ...]
    pending_ids: tuple[str, ...]
    topic_count: int
    source_unit_count: int
    row_count: int
    operation_count: int
    maturity_operation_count: int

    @property
    def complete(self) -> bool:
        return not (
            self.errors
            or self.unknown_ids
            or self.proposed_exclusion_ids
            or self.pending_ids
        )


def _unique_pairs(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InventoryError(f"duplicate key {key!r}")
        result[key] = value
    return result


def load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs
        )
    except (OSError, UnicodeError, json.JSONDecodeError, InventoryError) as error:
        raise InventoryError(f"{path}: {error}") from error
    if not isinstance(value, dict):
        raise InventoryError(f"{path}: root must be an object")
    return value


def extract_capture_time(text: str) -> str | None:
    match = CAPTURE.search(text)
    return match.group(1).strip() if match else None


def format_sha256(value: bytes) -> str:
    digest = hashlib.sha256(value).hexdigest()
    return "-".join(digest[index : index + 8] for index in range(0, 64, 8))


def _unit(topic_id: str, kind: str, line: int, text: str) -> dict[str, object]:
    normalized = " ".join(text.split())
    return {
        "id": f"{topic_id}:L{line:05d}:{kind}",
        "topic": topic_id,
        "kind": kind,
        "line": line,
        "sha256": format_sha256(normalized.encode()),
        "text": _safe_summary(kind, normalized),
    }


def _safe_summary(kind: str, text: str) -> str:
    """Keep structural source evidence without publishing example identifiers."""
    if kind == "command-synopsis":
        tokens = re.findall(r"(?<!\w)--?[A-Za-z0-9][A-Za-z0-9_-]*|^[A-Za-z0-9_]+", text)
        return " ".join(dict.fromkeys(tokens)) or "syntax"
    if kind in {"command-option", "command-table", "rest-method", "rest-field"}:
        cells = [cell.strip(" *`") for cell in text.strip("|").split("|")]
        return cells[0] if cells and cells[0] else "table-field"
    if kind == "rest-resource":
        path = re.search(r"/rest/[A-Za-z0-9_{}?&=./:-]+", text)
        if path:
            return path.group(0)
        element = re.search(r"<([A-Za-z][A-Za-z0-9_.:-]*)", text)
        if element:
            return f"xml-root:{element.group(1)}"
        key = re.search(r'["\']([A-Za-z][A-Za-z0-9_-]*)["\']\s*:', text)
        return f"payload-root:{key.group(1)}" if key else "structured-payload"
    versions = re.findall(r"\bV?\d+(?:[._RrMm]\d+)+\b", text)
    return (
        "versions:" + ",".join(dict.fromkeys(versions))
        if versions
        else "capability-note"
    )


def extract_source_units(topic_id: str, text: str) -> list[dict[str, object]]:
    """Enumerate structured operation, mode, parameter, and constraint candidates."""
    is_command = topic_id.startswith("commands-")
    section = ""
    in_fence = False
    fence_start = 0
    fence_lines: list[str] = []
    units: list[dict[str, object]] = []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("## "):
            section = stripped[3:].strip().upper()
        if stripped.startswith("```"):
            if in_fence:
                content = " ".join(part.strip() for part in fence_lines if part.strip())
                if content and not is_command:
                    units.append(_unit(topic_id, "rest-resource", fence_start, content))
                fence_lines = []
                in_fence = False
            else:
                in_fence = True
                fence_start = number + 1
            continue
        if in_fence:
            fence_lines.append(line)
            continue
        if is_command and section == "SYNOPSIS" and stripped:
            units.append(_unit(topic_id, "command-synopsis", number, stripped))
            continue
        if stripped.startswith("|") and not TABLE_SEPARATOR.fullmatch(stripped):
            cells = [cell.strip(" *`") for cell in stripped.strip("|").split("|")]
            if is_command:
                kind = "command-option" if section == "OPTIONS" else "command-table"
            else:
                kind = "rest-method" if cells and cells[0] in METHODS else "rest-field"
            units.append(_unit(topic_id, kind, number, stripped))
            continue
        if re.search(
            r"\b(?:Since|Version|HMC version|PowerVM)\b", stripped, re.IGNORECASE
        ):
            units.append(_unit(topic_id, "capability-statement", number, stripped))
    return units


def discover_registry() -> tuple[RegistryTool, ...]:
    from hmc_mcp.server_tools import command, permissions
    from hmc_mcp.server_tools.catalog import TOOL_MODULES, TOOL_SECURITY

    modules = (*TOOL_MODULES, command, permissions)
    result: list[RegistryTool] = []
    for tool, security in sorted(TOOL_SECURITY.items()):
        if tool == "hmc_effective_permissions":
            result.append(
                RegistryTool(
                    tool=tool,
                    operation=security.operation,
                    handler="hmc_mcp.server_tools.permissions.<composed>",
                    signature="()",
                    surfaces=("mcp",),
                )
            )
            continue
        handlers = list(
            {
                id(handler): handler
                for module in modules
                if hasattr(module, tool)
                for handler in (getattr(module, tool),)
            }.values()
        )
        if len(handlers) != 1:
            raise InventoryError(
                f"registry tool {tool!r} resolves to {len(handlers)} handlers"
            )
        handler = inspect.unwrap(handlers[0])
        result.append(
            RegistryTool(
                tool=tool,
                operation=security.operation,
                handler=f"{handler.__module__}.{handler.__name__}",
                signature=str(inspect.signature(handler)),
                surfaces=("mcp",),
            )
        )
    return tuple(result)


def _array(document: Mapping[str, object], key: str, errors: list[str]) -> list[object]:
    value = document.get(key)
    if not isinstance(value, list):
        errors.append(f"{key}: expected array")
        return []
    return value


def _objects(
    values: Sequence[object], key: str, errors: list[str]
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            errors.append(f"{key}[{index}]: expected object")
        else:
            result.append(value)
    return result


def _index(
    records: Sequence[dict[str, object]], kind: str, errors: list[str]
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for index, record in enumerate(records):
        identity = record.get("id")
        if not isinstance(identity, str) or not ID.fullmatch(identity):
            errors.append(f"{kind}[{index}]: invalid id {identity!r}")
            continue
        if identity in result:
            errors.append(f"{kind}: duplicate id {identity}")
        result[identity] = record
    return result


def _validate_versions(
    documents: Mapping[str, Mapping[str, object]], errors: list[str]
) -> None:
    for name, document in documents.items():
        if document.get("format_version") != 1:
            errors.append(f"{name}: format_version must be 1")


def _validate_topics(
    corpora: Mapping[str, dict[str, object]],
    topics: Mapping[str, dict[str, object]],
    errors: list[str],
) -> None:
    paths: set[tuple[object, object]] = set()
    for identity, topic in topics.items():
        corpus = topic.get("corpus")
        path = topic.get("path")
        pair = (corpus, path)
        if corpus not in corpora:
            errors.append(f"topic {identity}: unknown corpus {corpus!r}")
        if (
            not isinstance(path, str)
            or Path(path).is_absolute()
            or ".." in Path(path).parts
        ):
            errors.append(f"topic {identity}: unsafe path {path!r}")
        elif pair in paths:
            errors.append(f"topic {identity}: duplicate corpus path {path!r}")
        paths.add(pair)
        if topic.get("classification") not in {
            "operation",
            "schema",
            "overview",
            "navigation",
        }:
            errors.append(f"topic {identity}: invalid classification")
        if not isinstance(topic.get("reason"), str) or not str(topic["reason"]).strip():
            errors.append(f"topic {identity}: classification reason is required")
        if not HEX_256.fullmatch(str(topic.get("sha256", ""))):
            errors.append(f"topic {identity}: invalid sha256")


def _validate_corpora(
    corpora: Mapping[str, dict[str, object]], errors: list[str]
) -> None:
    for identity, corpus in corpora.items():
        if not str(corpus.get("source_url", "")).startswith("https://"):
            errors.append(f"corpus {identity}: HTTPS source_url is required")
        if not HEX_256.fullmatch(str(corpus.get("archive_sha256", ""))):
            errors.append(f"corpus {identity}: invalid archive_sha256")
        for field in ("captured_pages", "navigation_pages"):
            value = corpus.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                errors.append(
                    f"corpus {identity}: {field} must be a non-negative integer"
                )


def _validate_rows(
    rows: Mapping[str, dict[str, object]],
    units: Mapping[str, dict[str, object]],
    errors: list[str],
) -> tuple[list[str], list[str], list[str]]:
    accounted: Counter[str] = Counter()
    unknown: list[str] = []
    proposed: list[str] = []
    pending: list[str] = []
    for identity, row in rows.items():
        refs = row.get("source_units")
        if not isinstance(refs, list) or not refs:
            errors.append(f"row {identity}: source_units must be a non-empty array")
            continue
        for ref in refs:
            if ref not in units:
                errors.append(f"row {identity}: unknown source unit {ref!r}")
            elif isinstance(ref, str):
                accounted[ref] += 1
        disposition = row.get("disposition")
        if (
            not isinstance(disposition, dict)
            or disposition.get("kind") not in DISPOSITIONS
        ):
            errors.append(f"row {identity}: invalid disposition")
            continue
        kind = str(disposition["kind"])
        if kind == "coverage-child":
            issue = disposition.get("issue")
            if not isinstance(issue, int) or issue < 1:
                errors.append(f"row {identity}: coverage-child requires issue")
            pending.append(identity)
        elif kind == "unknown":
            if not str(disposition.get("question", "")).strip():
                errors.append(f"row {identity}: unknown requires question")
            unknown.append(identity)
        elif kind == "proposed-exclusion":
            if (
                not str(disposition.get("reason", "")).strip()
                or not str(disposition.get("owner", "")).strip()
            ):
                errors.append(
                    f"row {identity}: proposed exclusion requires reason and owner"
                )
            proposed.append(identity)
    for identity, unit in units.items():
        accounting = unit.get("accounting")
        if not isinstance(accounting, dict):
            errors.append(f"source unit {identity}: accounting is required")
            continue
        if accounting.get("kind") == "row":
            row_id = accounting.get("id")
            if row_id not in rows:
                errors.append(
                    f"source unit {identity}: unknown accounting row {row_id!r}"
                )
            elif accounted[identity] != 1 or identity not in rows[str(row_id)].get(
                "source_units", []
            ):
                errors.append(
                    f"source unit {identity}: accounting does not match row {row_id}"
                )
        elif accounting.get("kind") == "non-operation":
            if not str(accounting.get("reason", "")).strip():
                errors.append(
                    f"source unit {identity}: non-operation reason is required"
                )
        else:
            errors.append(f"source unit {identity}: invalid accounting")
        if accounted[identity] > 1:
            errors.append(f"source unit {identity}: accounted by more than one row")
    return unknown, proposed, pending


def _validate_operations(
    records: Sequence[dict[str, object]],
    rows: Mapping[str, dict[str, object]],
    registry: Collection[RegistryTool],
    repo_root: Path,
    errors: list[str],
) -> None:
    expected = {tool.tool: tool for tool in registry}
    observed: dict[str, dict[str, object]] = {}
    for index, record in enumerate(records):
        tool = record.get("tool")
        if not isinstance(tool, str):
            errors.append(f"operations[{index}]: tool is required")
            continue
        if tool in observed:
            errors.append(f"operation evidence: duplicate tool {tool}")
        observed[tool] = record
        found = expected.get(tool)
        if found is None:
            errors.append(f"operation evidence {tool}: tool is not registered")
            continue
        for field in ("operation", "handler", "signature"):
            if record.get(field) != getattr(found, field):
                errors.append(
                    f"operation evidence {tool}: {field} does not match registry"
                )
        if tuple(record.get("surfaces", ())) != found.surfaces:
            errors.append(f"operation evidence {tool}: surfaces do not match registry")
        row_ids = record.get("row_ids")
        if not isinstance(row_ids, list):
            errors.append(f"operation evidence {tool}: row_ids must be an array")
        else:
            for row_id in row_ids:
                if row_id not in rows:
                    errors.append(f"operation evidence {tool}: unknown row {row_id!r}")
        if not row_ids and not str(record.get("composite_reason", "")).strip():
            errors.append(
                f"operation evidence {tool}: rows or composite reason required"
            )
        tests = record.get("tests")
        if not isinstance(tests, list):
            errors.append(f"operation evidence {tool}: tests must be an array")
        else:
            for path_text in tests:
                path = repo_root / str(path_text)
                if (
                    Path(str(path_text)).is_absolute()
                    or ".." in Path(str(path_text)).parts
                    or not path.is_file()
                    or path.is_symlink()
                ):
                    errors.append(
                        f"operation evidence {tool}: invalid test path {path_text!r}"
                    )
    for tool in sorted(expected.keys() - observed.keys()):
        errors.append(f"registry operation {tool}: missing operation evidence")


def _exact_keys(value: object, keys: set[str], label: str, errors: list[str]) -> bool:
    if not isinstance(value, dict) or set(value) != keys:
        errors.append(f"{label}: expected exactly {sorted(keys)}")
        return False
    return True


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _one_of(value: object, choices: set[str]) -> bool:
    return isinstance(value, str) and value in choices


def _matches(pattern: re.Pattern[str], value: object) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _scope_identity(
    value: object, label: str, errors: list[str]
) -> tuple[object, ...] | None:
    if not _exact_keys(value, {"variant", "parameters"}, label, errors):
        return None
    assert isinstance(value, dict)
    variant = value["variant"]
    parameters = value["parameters"]
    if not _nonempty(variant) or not isinstance(parameters, list):
        errors.append(f"{label}: invalid variant or parameters")
        return None
    pairs: list[tuple[str, str]] = []
    for index, parameter in enumerate(parameters):
        parameter_label = f"{label} parameter {index}"
        if not _exact_keys(parameter, {"name", "constraint"}, parameter_label, errors):
            continue
        assert isinstance(parameter, dict)
        name, constraint = parameter["name"], parameter["constraint"]
        if not _nonempty(name) or not _nonempty(constraint):
            errors.append(f"{parameter_label}: name and constraint are required")
        else:
            pairs.append((name, constraint))
    if len({name for name, _ in pairs}) != len(pairs) or pairs != sorted(pairs):
        errors.append(f"{label}: parameters must be unique and sorted")
    return (variant, tuple(pairs))


def _validate_implementation(
    record: dict[str, object], label: str, errors: list[str]
) -> set[tuple[object, ...]]:
    implementation = record.get("implementation")
    if not _exact_keys(
        implementation, {"state", "implemented_scope", "missing_scope"}, label, errors
    ):
        return set()
    assert isinstance(implementation, dict)
    state = implementation["state"]
    implemented = implementation["implemented_scope"]
    missing = implementation["missing_scope"]
    if (
        not _one_of(state, MATURITY_STATES)
        or not isinstance(implemented, list)
        or not isinstance(missing, list)
    ):
        errors.append(f"{label}: invalid implementation state or scope lists")
        return set()
    implemented_ids = {_scope_identity(scope, label, errors) for scope in implemented}
    missing_ids = {_scope_identity(scope, label, errors) for scope in missing}
    implemented_ids.discard(None)
    missing_ids.discard(None)
    if len(implemented_ids) != len(implemented) or len(missing_ids) != len(missing):
        errors.append(f"{label}: scope entries must be unique and valid")
    valid_lists = {
        "absent": (not implemented and bool(missing)),
        "partial": (bool(implemented) and bool(missing)),
        "implemented": (bool(implemented) and not missing),
    }
    if not valid_lists.get(state, False) or implemented_ids & missing_ids:
        errors.append(f"{label}: contradictory implementation scope")
    return implemented_ids


def _validate_timestamp(value: object, label: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not TIMESTAMP.fullmatch(value):
        errors.append(f"{label}: observed_at must be canonical UTC")
        return
    try:
        datetime.fromisoformat(value)
    except ValueError:
        errors.append(f"{label}: observed_at is not a calendar timestamp")


def _environment_identity(
    value: object, label: str, errors: list[str]
) -> tuple[str, ...] | None:
    fields = (
        "hmc_release",
        "hmc_build",
        "hardware_family",
        "firmware",
        "licensing",
        "topology",
    )
    if not _exact_keys(value, set(fields), label, errors):
        return None
    assert isinstance(value, dict)
    if not all(_nonempty(value[field]) for field in fields):
        errors.append(f"{label}: environment fields are required")
        return None
    return tuple(value[field] for field in fields)


def _validate_observation(
    observation: object,
    operation: str,
    scopes: set[tuple[object, ...]],
    evidence_ids: set[str],
    current: set[tuple[object, ...]],
    errors: list[str],
) -> None:
    required = {
        "id",
        "channel",
        "scope",
        "scenario",
        "result",
        "currency",
        "observed_at",
        "implementation_revision",
        "deployed_revision",
        "environment",
        "assertions",
        "cleanup",
        "provenance",
        "promotion",
        "reason",
        "prerequisites",
        "obligation",
        "implementation_fingerprint",
        "invalidated_by",
    }
    if not _exact_keys(
        observation, required, f"maturity operation {operation} evidence", errors
    ):
        return
    assert isinstance(observation, dict)
    identity = observation["id"]
    label = (
        f"maturity evidence {identity}"
        if isinstance(identity, str)
        else f"maturity operation {operation} evidence"
    )
    if not _nonempty(identity) or identity in evidence_ids:
        errors.append(f"{label}: id must be catalog-wide unique")
        return
    assert isinstance(identity, str)
    evidence_ids.add(identity)
    channel, result, currency = (
        observation["channel"],
        observation["result"],
        observation["currency"],
    )
    if (
        not _one_of(channel, EVIDENCE_CHANNELS)
        or not _one_of(result, EVIDENCE_RESULTS)
        or not _one_of(currency, EVIDENCE_CURRENCY)
    ):
        errors.append(f"{label}: invalid channel, result, or currency")
        return
    scope = _scope_identity(observation["scope"], label, errors)
    if scope not in scopes:
        errors.append(f"{label}: scope is not implemented")
        return
    scenario = observation["scenario"]
    if scenario is not None and (
        not _exact_keys(scenario, {"id", "description"}, label, errors)
        or not all(_nonempty(value) for value in scenario.values())
    ):
        errors.append(f"{label}: invalid scenario")
        return
    environment = observation["environment"]
    environment_id = (
        _environment_identity(environment, label, errors) if channel == "live" else None
    )
    if (channel == "live") != (environment is not None):
        errors.append(f"{label}: environment is required only for live evidence")
    if channel == "live" and environment_id is None:
        return
    _validate_result(
        observation, operation, channel, result, scenario, identity, label, errors
    )
    _validate_currency(
        observation,
        operation,
        channel,
        currency,
        scope,
        scenario,
        environment_id,
        current,
        label,
        errors,
    )


def _validate_evidence_lists(
    observation: dict[str, object], label: str, errors: list[str]
) -> None:
    assertions = observation["assertions"]
    prerequisites = observation["prerequisites"]
    if not isinstance(assertions, list) or not all(
        _nonempty(item) for item in assertions
    ):
        errors.append(f"{label}: assertions must be non-empty strings")
    elif len(set(assertions)) != len(assertions):
        errors.append(f"{label}: assertions must be unique")
    if not isinstance(prerequisites, list) or not all(
        _nonempty(item) for item in prerequisites
    ):
        errors.append(f"{label}: prerequisites must be non-empty strings")
    elif prerequisites != sorted(set(prerequisites)):
        errors.append(f"{label}: prerequisites must be sorted and unique")


def _validate_result(
    observation: dict[str, object],
    operation: str,
    channel: object,
    result: object,
    scenario: object,
    identity: object,
    label: str,
    errors: list[str],
) -> None:
    _validate_evidence_lists(observation, label, errors)
    if not _one_of(
        observation["cleanup"], {"not-run", "not-required", "failed", "passed"}
    ):
        errors.append(f"{label}: invalid cleanup")
    promotion = observation["promotion"]
    if not _exact_keys(promotion, {"eligible", "reason"}, label, errors) or (
        promotion.get("eligible") is not False
    ):
        errors.append(f"{label}: format 1 promotion must be ineligible")
    elif not _nonempty(promotion.get("reason")):
        errors.append(f"{label}: promotion reason is required")
    if (channel, result) != ("live", "not-run") and observation[
        "obligation"
    ] is not None:
        errors.append(f"{label}: obligation is only valid for live not-run")
    if result == "not-run":
        _validate_not_run(
            observation, operation, channel, scenario, identity, label, errors
        )
    else:
        _validate_attempted(observation, channel, result, label, errors)


def _validate_attempted(
    observation: dict[str, object],
    channel: object,
    result: object,
    label: str,
    errors: list[str],
) -> None:
    _validate_timestamp(observation["observed_at"], label, errors)
    if observation["scenario"] is None:
        errors.append(f"{label}: attempted evidence requires scenario")
    if not _matches(SHA_1, observation["implementation_revision"]):
        errors.append(f"{label}: implementation_revision must be a full SHA")
    if channel == "live" and not _matches(SHA_1, observation["deployed_revision"]):
        errors.append(f"{label}: live evidence requires deployed_revision")
    if channel != "live" and observation["deployed_revision"] is not None:
        errors.append(f"{label}: non-live evidence has no deployed_revision")
    provenance = observation["provenance"]
    if _exact_keys(provenance, {"kind", "reference"}, label, errors) and (
        provenance.get("kind") != "unverified"
        or not _nonempty(provenance.get("reference"))
    ):
        errors.append(f"{label}: invalid provenance")
    _validate_attempt_outcome(observation, channel, result, label, errors)


def _validate_attempt_outcome(
    observation: dict[str, object],
    channel: object,
    result: object,
    label: str,
    errors: list[str],
) -> None:
    if result == "passed" and not observation["assertions"]:
        errors.append(f"{label}: passed evidence requires assertions")
    if (
        channel == "live"
        and result == "passed"
        and not _one_of(observation["cleanup"], {"passed", "not-required"})
    ):
        errors.append(f"{label}: live pass requires successful cleanup")
    if result in {"skipped", "failed"} and not _nonempty(observation["reason"]):
        errors.append(f"{label}: result requires a reason")
    if result == "passed" and observation["reason"] is not None:
        errors.append(f"{label}: passed evidence has no reason")


def _validate_not_run(
    observation: dict[str, object],
    operation: str,
    channel: object,
    scenario: object,
    identity: object,
    label: str,
    errors: list[str],
) -> None:
    fields = (
        "observed_at",
        "implementation_revision",
        "deployed_revision",
        "provenance",
        "implementation_fingerprint",
    )
    if any(observation[key] is not None for key in fields):
        errors.append(f"{label}: not-run fields must be null")
    if observation["assertions"] or observation["cleanup"] != "not-run":
        errors.append(f"{label}: not-run has no assertions or cleanup")
    if not _nonempty(observation["reason"]):
        errors.append(f"{label}: result requires a reason")
    obligation = observation["obligation"]
    valid_obligation = (
        isinstance(obligation, dict)
        and set(obligation) <= {"catalog", "issue"}
        and obligation.get("catalog") == f"{operation}#{identity}"
    )
    valid_issue = (
        "issue" not in obligation
        or (isinstance(obligation["issue"], int) and obligation["issue"] > 0)
        if isinstance(obligation, dict)
        else False
    )
    if channel == "live" and (
        scenario is None
        or not observation["prerequisites"]
        or not valid_obligation
        or not valid_issue
    ):
        errors.append(
            f"{label}: live not-run requires scenario, prerequisites, and catalog obligation"
        )


def _validate_currency(
    observation: dict[str, object],
    operation: str,
    channel: object,
    currency: object,
    scope: tuple[object, ...] | None,
    scenario: object,
    environment: tuple[str, ...] | None,
    current: set[tuple[object, ...]],
    label: str,
    errors: list[str],
) -> None:
    if observation["result"] != "not-run" and not _matches(
        SHA_256, observation["implementation_fingerprint"]
    ):
        errors.append(f"{label}: implementation_fingerprint must be a full SHA-256")
    if currency == "current":
        if observation["invalidated_by"] is not None:
            errors.append(f"{label}: current evidence has no invalidator")
        scenario_id = scenario.get("id") if isinstance(scenario, dict) else None
        key = (operation, channel, scope, scenario_id, environment)
        if key in current:
            errors.append(f"{label}: duplicate current evidence")
        current.add(key)
        return
    invalidator = observation["invalidated_by"]
    if not _exact_keys(
        invalidator, {"implementation_fingerprint", "reason"}, label, errors
    ):
        return
    if not _matches(
        SHA_256, invalidator.get("implementation_fingerprint")
    ) or not _nonempty(invalidator.get("reason")):
        errors.append(f"{label}: stale evidence requires an invalidator")


def _validate_maturity(
    records: Sequence[dict[str, object]],
    operation_ids: Collection[str],
    errors: list[str],
) -> None:
    seen: set[str] = set()
    evidence_ids: set[str] = set()
    current: set[tuple[object, ...]] = set()
    for record in records:
        operation = record.get("operation")
        label = f"maturity operation {operation}"
        if not _exact_keys(
            record, {"operation", "implementation", "evidence"}, label, errors
        ):
            continue
        if not _nonempty(operation):
            errors.append(f"{label}: operation must be a non-empty string")
            continue
        if operation in seen:
            errors.append(f"{label}: duplicate operation")
        elif operation not in operation_ids:
            errors.append(f"{label}: unknown operation")
        seen.add(operation)
        scopes = _validate_implementation(record, label, errors)
        evidence = record["evidence"]
        if not isinstance(evidence, list):
            errors.append(f"{label}: evidence must be a list")
            continue
        for observation in evidence:
            _validate_observation(
                observation, str(operation), scopes, evidence_ids, current, errors
            )


def implementation_fingerprint(repo_root: Path) -> str:
    paths = _implementation_paths(repo_root)
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.relative_to(repo_root).as_posix()):
        relative = path.relative_to(repo_root).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _implementation_paths(repo_root: Path) -> list[Path]:
    command = [
        "git",
        "-C",
        str(repo_root),
        "ls-files",
        "-z",
        "src",
        "scripts",
        "pyproject.toml",
        "uv.lock",
    ]
    result = subprocess.run(command, capture_output=True, check=False)
    names = (
        result.stdout.decode(errors="surrogateescape").split("\0")
        if result.returncode == 0
        else []
    )
    paths = [repo_root / name for name in names if name]
    if paths:
        return [path for path in paths if path.is_file() and not path.is_symlink()]
    directories = (repo_root / "src", repo_root / "scripts")
    found = [
        path
        for directory in directories
        if directory.is_dir()
        for path in directory.rglob("*")
    ]
    found.extend(
        path
        for path in (repo_root / "pyproject.toml", repo_root / "uv.lock")
        if path.is_file()
    )
    return [path for path in found if path.is_file() and not path.is_symlink()]


def validate_inventory(
    root: Path,
    registry: Collection[RegistryTool],
    *,
    repo_root: Path = ROOT,
) -> Report:
    errors: list[str] = []
    try:
        documents = {
            name: load_json(root / name)
            for name in (
                "corpora.json",
                "rows.json",
                "operations.json",
                "maturity.json",
            )
        }
    except InventoryError as error:
        return Report((str(error),), (), (), (), 0, 0, 0, 0, 0)
    _validate_versions(documents, errors)
    if documents["maturity.json"].get("admission_policy") != "existing-runtime-guards":
        errors.append("maturity.json: admission_policy must be existing-runtime-guards")
    corpora_records = _objects(
        _array(documents["corpora.json"], "corpora", errors), "corpora", errors
    )
    topic_records = _objects(
        _array(documents["corpora.json"], "topics", errors), "topics", errors
    )
    unit_records = _objects(
        _array(documents["corpora.json"], "source_units", errors),
        "source_units",
        errors,
    )
    row_records = _objects(
        _array(documents["rows.json"], "rows", errors), "rows", errors
    )
    operation_records = _objects(
        _array(documents["operations.json"], "operations", errors), "operations", errors
    )
    maturity_records = _objects(
        _array(documents["maturity.json"], "operations", errors),
        "maturity operations",
        errors,
    )
    corpora = _index(corpora_records, "corpora", errors)
    topics = _index(topic_records, "topics", errors)
    units = _index(unit_records, "source_units", errors)
    rows = _index(row_records, "rows", errors)
    _validate_corpora(corpora, errors)
    _validate_topics(corpora, topics, errors)
    for identity, unit in units.items():
        if unit.get("topic") not in topics:
            errors.append(
                f"source unit {identity}: unknown topic {unit.get('topic')!r}"
            )
        if not HEX_256.fullmatch(str(unit.get("sha256", ""))):
            errors.append(f"source unit {identity}: invalid sha256")
    unknown, proposed, pending = _validate_rows(rows, units, errors)
    _validate_operations(operation_records, rows, registry, repo_root, errors)
    _validate_maturity(
        maturity_records,
        {
            record.get("operation")
            for record in operation_records
            if isinstance(record.get("operation"), str)
        },
        errors,
    )
    fingerprint = implementation_fingerprint(repo_root)
    for record in maturity_records:
        for evidence in (
            record.get("evidence", [])
            if isinstance(record.get("evidence"), list)
            else []
        ):
            if (
                isinstance(evidence, dict)
                and evidence.get("currency") == "current"
                and evidence.get("result") != "not-run"
                and evidence.get("implementation_fingerprint") != fingerprint
            ):
                errors.append(
                    f"maturity evidence {evidence.get('id')}: stale implementation fingerprint"
                )
    return Report(
        tuple(errors),
        tuple(sorted(unknown)),
        tuple(sorted(proposed)),
        tuple(sorted(pending)),
        len(topics),
        len(units),
        len(rows),
        len(operation_records),
        len(maturity_records),
    )


def verify_corpora(root: Path, sources: Mapping[str, Path]) -> list[str]:
    try:
        document = load_json(root / "corpora.json")
    except InventoryError as error:
        return [str(error)]
    local_errors: list[str] = []
    corpus_records = _objects(
        document.get("corpora", [])
        if isinstance(document.get("corpora"), list)
        else [],
        "corpora",
        local_errors,
    )
    corpus_ids = {str(record.get("id")) for record in corpus_records}
    topics = _objects(
        document.get("topics", []) if isinstance(document.get("topics"), list) else [],
        "topics",
        local_errors,
    )
    units = _objects(
        document.get("source_units", [])
        if isinstance(document.get("source_units"), list)
        else [],
        "source_units",
        local_errors,
    )
    expected_paths: dict[str, set[str]] = {name: set() for name in sources}
    expected_units: dict[str, list[dict[str, object]]] = {}
    errors = local_errors
    for corpus in sorted(corpus_ids - sources.keys()):
        errors.append(f"{corpus}: source root was not supplied")
    for corpus in sorted(sources.keys() - corpus_ids):
        errors.append(f"{corpus}: unexpected source corpus")
    for topic in topics:
        corpus = str(topic.get("corpus"))
        if corpus not in sources:
            continue
        relative = str(topic.get("path"))
        expected_paths[corpus].add(relative)
        path = sources[corpus] / relative
        if not path.is_file() or path.is_symlink():
            errors.append(f"{corpus}/{relative}: missing or non-regular source file")
            continue
        digest = format_sha256(path.read_bytes())
        if digest != topic.get("sha256"):
            errors.append(f"{corpus}/{relative}: hash mismatch")
        try:
            expected_units[str(topic["id"])] = extract_source_units(
                str(topic["id"]), path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError) as error:
            errors.append(f"{corpus}/{relative}: cannot read: {error}")
    for corpus, source in sources.items():
        if not source.is_dir() or source.is_symlink():
            errors.append(
                f"{corpus}: source root is missing, symlinked, or not a directory"
            )
            continue
        actual = {
            str(path.relative_to(source))
            for path in source.rglob("*.md")
            if path.is_file() and not path.is_symlink()
        }
        for relative in sorted(actual - expected_paths[corpus]):
            errors.append(f"{corpus}/{relative}: unexpected source file")
    recorded_by_topic: dict[str, list[dict[str, object]]] = {}
    for unit in units:
        clean = {key: value for key, value in unit.items() if key != "accounting"}
        recorded_by_topic.setdefault(str(unit.get("topic")), []).append(clean)
    for topic_id, expected in expected_units.items():
        if recorded_by_topic.get(topic_id, []) != expected:
            errors.append(f"{topic_id}: derived source units differ")
    return errors


def _source(value: str) -> tuple[str, Path]:
    name, separator, path = value.partition("=")
    if not separator or not name or not path:
        raise argparse.ArgumentTypeError("source must be CORPUS=PATH")
    return name, Path(path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--source", action="append", default=[], type=_source)
    args = parser.parse_args(argv)
    try:
        registry = discover_registry()
    except InventoryError as error:
        print(f"capability inventory: {error}", file=sys.stderr)
        return 1
    report = validate_inventory(args.inventory, registry)
    errors = list(report.errors)
    if args.source:
        sources = dict(args.source)
        if len(sources) != len(args.source):
            errors.append("duplicate --source corpus ID")
        errors.extend(verify_corpora(args.inventory, sources))
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        return 1
    print(
        "capability inventory: structurally valid; "
        f"{report.topic_count} topics, {report.source_unit_count} source units, "
        f"{report.row_count} rows, {report.operation_count} registry operations, "
        f"{report.maturity_operation_count} maturity operations"
    )
    if report.complete:
        print("capability coverage: complete")
    else:
        print(
            "capability coverage: incomplete; "
            f"{len(report.pending_ids)} coverage children, "
            f"{len(report.unknown_ids)} unknowns, "
            f"{len(report.proposed_exclusion_ids)} proposed exclusions"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
