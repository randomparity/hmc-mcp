"""Validate the versioned HMC reference capability inventory."""

from __future__ import annotations

import argparse
import ast
import hashlib
import inspect
import json
import os
import re
import sys
import tempfile
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = ROOT / "docs" / "capabilities"
DEFAULT_RUNTIME_PROJECTION = ROOT / "src" / "hmc_mcp" / "_operation_maturity.json"
HEX_256 = re.compile(r"(?:[0-9a-f]{8}-){7}[0-9a-f]{8}")
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*")
TABLE_SEPARATOR = re.compile(r"^\|(?:\s*:?-+:?\s*\|)+$")
CAPTURE = re.compile(r"^(?:captured|Captured):\s*(\S.*)$", re.MULTILINE)
METHODS = {"GET", "POST", "PUT", "DELETE"}
DISPOSITIONS = {"supported", "coverage-child", "proposed-exclusion", "unknown"}
MATURITY_STATES = {"absent", "partial", "implemented"}
EVIDENCE_CHANNELS = {"contract-review", "automated", "live"}
EVIDENCE_RESULTS = {"failed", "passed"}
SHA_1 = re.compile(r"[0-9a-f]{40}")
SHA_256 = re.compile(r"[0-9a-f]{64}")
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

#: `maturity.json` alone moves to format 2 (ADR 0127); the other three catalogs
#: are unchanged and stay at 1.
MATURITY_FORMAT_VERSION = 2
STALE_AFTER_DAYS = 90
PACKAGE = "hmc_mcp"

#: The exact key set of a format 2 observation. There is one observation shape:
#: ADR 0126's `not-run` placeholder is dropped rather than carried forward.
ATTEMPTED_KEYS = {
    "id",
    "channel",
    "result",
    "scenario",
    "tested_commit",
    "observed_at",
    "hmc_release",
    "hardware_family",
    "cleanup",
    "closure_fingerprint",
    "assertions",
}
CLEANUP = {"not-run", "not-required", "failed", "passed"}
OBSERVATION_ID = re.compile(r"[a-z0-9][a-z0-9-]*")
SCENARIO_ID = re.compile(r"st\d+-[a-z0-9-]+")
ASSERTION_ID = re.compile(r"[a-z][a-z0-9-]{1,62}[a-z0-9]")

#: Narrow grammars, not a permissive character class with an address rejection:
#: `[A-Za-z0-9 ._-]{0,39}` admits a hostname, a serial and a location code
#: verbatim while rejecting only a dotted quad.
HMC_RELEASE = re.compile(r"V\d+R\d+(?:M\d+)?")
HARDWARE_FAMILY = re.compile(r"POWER\d+")


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
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
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
    return "versions:" + ",".join(dict.fromkeys(versions)) if versions else "capability-note"


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
            raise InventoryError(f"registry tool {tool!r} resolves to {len(handlers)} handlers")
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


def _objects(values: Sequence[object], key: str, errors: list[str]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            errors.append(f"{key}[{index}]: expected object")
        else:
            result.append(value)
    return result


def _index(records: Sequence[dict[str, object]], kind: str, errors: list[str]) -> dict[str, dict[str, object]]:
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


def _validate_versions(documents: Mapping[str, Mapping[str, object]], errors: list[str]) -> None:
    for name, document in documents.items():
        if name == "maturity.json":
            continue
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
        if not isinstance(path, str) or Path(path).is_absolute() or ".." in Path(path).parts:
            errors.append(f"topic {identity}: unsafe path {path!r}")
        elif pair in paths:
            errors.append(f"topic {identity}: duplicate corpus path {path!r}")
        paths.add(pair)
        if topic.get("classification") not in {"operation", "schema", "overview", "navigation"}:
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
                errors.append(f"corpus {identity}: {field} must be a non-negative integer")


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
        if not isinstance(disposition, dict) or disposition.get("kind") not in DISPOSITIONS:
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
            if not str(disposition.get("reason", "")).strip() or not str(
                disposition.get("owner", "")
            ).strip():
                errors.append(f"row {identity}: proposed exclusion requires reason and owner")
            proposed.append(identity)
    for identity, unit in units.items():
        accounting = unit.get("accounting")
        if not isinstance(accounting, dict):
            errors.append(f"source unit {identity}: accounting is required")
            continue
        if accounting.get("kind") == "row":
            row_id = accounting.get("id")
            if row_id not in rows:
                errors.append(f"source unit {identity}: unknown accounting row {row_id!r}")
            elif accounted[identity] != 1 or identity not in rows[str(row_id)].get("source_units", []):
                errors.append(f"source unit {identity}: accounting does not match row {row_id}")
        elif accounting.get("kind") == "non-operation":
            if not str(accounting.get("reason", "")).strip():
                errors.append(f"source unit {identity}: non-operation reason is required")
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
                errors.append(f"operation evidence {tool}: {field} does not match registry")
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
            errors.append(f"operation evidence {tool}: rows or composite reason required")
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
                    errors.append(f"operation evidence {tool}: invalid test path {path_text!r}")
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


def _validate_observation(
    observation: object,
    operation: str,
    evidence_ids: set[str],
    errors: list[str],
) -> None:
    """Validate one format 2 observation against its single closed shape.

    `maturity.json` is hand-copied from the runner's output and hand-editable, so
    every bound the runner applies on the way out is applied again here: the
    validator is the only check a hand-authored record ever meets.
    """
    if not _exact_keys(
        observation, ATTEMPTED_KEYS, f"maturity operation {operation} evidence", errors
    ):
        return
    assert isinstance(observation, dict)
    identity = observation["id"]
    label = (
        f"maturity evidence {identity}"
        if isinstance(identity, str)
        else f"maturity operation {operation} evidence"
    )
    if not _matches(OBSERVATION_ID, identity):
        errors.append(f"{label}: id must match {OBSERVATION_ID.pattern}")
        return
    if identity in evidence_ids:
        errors.append(f"{label}: id must be catalog-wide unique")
        return
    assert isinstance(identity, str)
    evidence_ids.add(identity)
    if not _one_of(observation["channel"], EVIDENCE_CHANNELS) or not _one_of(
        observation["result"], EVIDENCE_RESULTS
    ):
        errors.append(f"{label}: invalid channel or result")
        return
    if not _matches(SCENARIO_ID, observation["scenario"]):
        errors.append(f"{label}: scenario must match st<n>-<slug>")
    if not _matches(SHA_1, observation["tested_commit"]):
        errors.append(f"{label}: tested_commit must be a full SHA")
    if not _matches(SHA_256, observation["closure_fingerprint"]):
        errors.append(f"{label}: closure_fingerprint must be a full SHA-256")
    _validate_timestamp(observation["observed_at"], label, errors)
    if not _matches(HMC_RELEASE, observation["hmc_release"]) or not _matches(
        HARDWARE_FAMILY, observation["hardware_family"]
    ):
        errors.append(f"{label}: environment values do not match their grammar")
    if not _one_of(observation["cleanup"], CLEANUP):
        errors.append(f"{label}: invalid cleanup")
    _validate_assertions(observation, label, errors)


def _validate_assertions(
    observation: dict[str, object], label: str, errors: list[str]
) -> None:
    """Validate the held-assertion list and what a `passed` observation requires."""
    assertions = observation["assertions"]
    if not isinstance(assertions, list) or not all(
        _matches(ASSERTION_ID, item) for item in assertions
    ):
        errors.append(f"{label}: assertions must be closed-shape tokens")
        return
    if len(set(assertions)) != len(assertions):
        errors.append(f"{label}: assertions must be unique")
    if observation["result"] != "passed":
        return
    if not assertions:
        errors.append(f"{label}: passed evidence requires assertions")
    if not _one_of(observation["cleanup"], {"passed", "not-required"}):
        errors.append(f"{label}: a passed observation requires successful cleanup")


def _validate_maturity(
    records: Sequence[dict[str, object]],
    operation_ids: Collection[str],
    errors: list[str],
) -> None:
    seen: set[str] = set()
    evidence_ids: set[str] = set()
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
        _validate_implementation(record, label, errors)
        evidence = record["evidence"]
        if not isinstance(evidence, list):
            errors.append(f"{label}: evidence must be a list")
            continue
        for observation in evidence:
            _validate_observation(observation, str(operation), evidence_ids, errors)


@dataclass(frozen=True)
class OperationState:
    """One operation's derived verification state, and why it is stale."""

    state: str
    reason: str | None = None
    implementation: str | None = None
    observed_at: str | None = None


def _readable(path: Path) -> bool:
    """Report whether a path is a regular file this walk may hash.

    A symlink is skipped rather than followed: one pointing outside the
    repository would make the fingerprint machine-dependent, so the runner's
    recorded value and CI's recomputation could never agree and the observation
    would read stale forever.
    """
    return path.is_file() and not path.is_symlink()


def _resolution_chain(package_root: Path, dotted: str) -> list[Path]:
    """Every file a dotted name resolves through, packages included.

    A dotted target resolves to ``<path>.py`` when that exists, else to
    ``<path>/__init__.py``; each ``__init__.py`` on the way is part of what the
    import executes and so belongs in the closure.
    """
    parts = dotted.split(".")
    found: list[Path] = []
    for index in range(1, len(parts) + 1):
        directory = package_root.joinpath(*parts[:index])
        if _readable(directory / "__init__.py"):
            found.append(directory / "__init__.py")
        if index == len(parts):
            module = directory.with_suffix(".py")
            if _readable(module):
                found.append(module)
    return found


def _package_of(package_root: Path, path: Path) -> tuple[str, ...]:
    """The dotted package a module file's relative imports resolve against.

    Both forms drop their last component: `jobs/core.py` and `jobs/__init__.py`
    each sit in the package `hmc_mcp.jobs`, so `from .core import …` written in
    the latter resolves against `hmc_mcp.jobs`, not `hmc_mcp.jobs.__init__`.
    """
    return path.relative_to(package_root).with_suffix("").parts[:-1]


def _module_level_statements(body: Sequence[ast.stmt]) -> list[ast.stmt]:
    """Statements the module executes at import time, one `If`/`Try` level in.

    A `TYPE_CHECKING` guard is a module-level `If`, so its body has to be read;
    a `FunctionDef`, `AsyncFunctionDef` or `ClassDef` body must not be. The
    distinction decides the design: `src/hmc_mcp/__init__.py` imports `.cli`
    inside `main()` and sits on every resolution path, so an `ast.walk` here
    would pull 179 of 180 files into every closure and silently reinstate ADR
    0126's repository-wide fingerprint.
    """
    statements: list[ast.stmt] = []
    for statement in body:
        statements.append(statement)
        if isinstance(statement, ast.If):
            statements += _module_level_statements(statement.body)
            statements += _module_level_statements(statement.orelse)
        elif isinstance(statement, ast.Try):
            statements += _module_level_statements(statement.body)
            for handler in statement.handlers:
                statements += _module_level_statements(handler.body)
            statements += _module_level_statements(statement.orelse)
            statements += _module_level_statements(statement.finalbody)
    return statements


def _imported_names(path: Path, package: tuple[str, ...]) -> list[str]:
    """Every dotted name this module imports that could reach the package."""
    try:
        tree = ast.parse(path.read_bytes())
    except (OSError, SyntaxError, ValueError):
        return []
    names: list[str] = []
    for statement in _module_level_statements(tree.body):
        if isinstance(statement, ast.Import):
            names += [
                alias.name
                for alias in statement.names
                if alias.name == PACKAGE or alias.name.startswith(f"{PACKAGE}.")
            ]
        elif isinstance(statement, ast.ImportFrom):
            names += _import_from_names(statement, package)
    return names


def _import_from_names(
    statement: ast.ImportFrom, package: tuple[str, ...]
) -> list[str]:
    """Resolve one `from ... import ...` to dotted names, absolute or relative."""
    if statement.level:
        upward = statement.level - 1
        if upward >= len(package):
            # Resolving this far up leaves the package: at `package` depth 1,
            # `from ..x import y` would resolve `x` against `src/` itself.
            return []
        base = package[: len(package) - upward]
    elif statement.module and (
        statement.module == PACKAGE or statement.module.startswith(f"{PACKAGE}.")
    ):
        base = ()
    else:
        return []
    target = (*base, *(statement.module.split(".") if statement.module else ()))
    if not target:
        return []
    dotted = ".".join(target)
    # An imported name may itself be a module in that package, or may be a class
    # or constant, in which case the name resolves to nothing and is skipped.
    return [dotted, *(f"{dotted}.{alias.name}" for alias in statement.names)]


def closure_paths(repo_root: Path, handler_module: str) -> list[Path]:
    """Every `src/hmc_mcp/` file the handler module transitively imports."""
    package_root = repo_root / "src"
    found: set[Path] = set()
    pending = [handler_module]
    while pending:
        for path in _resolution_chain(package_root, pending.pop()):
            if path in found:
                continue
            found.add(path)
            pending += _imported_names(path, _package_of(package_root, path))
    return sorted(found)


def closure_fingerprint(repo_root: Path, handler_module: str) -> str:
    """Hash the operation's import closure, length-prefixed and path-ordered."""
    digest = hashlib.sha256()
    for path in closure_paths(repo_root, handler_module):
        relative = path.relative_to(repo_root).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _handler_module(handler: str) -> str:
    """The module holding a registry handler, dropping the function component.

    The composed `hmc_effective_permissions` record ends in `<composed>` rather
    than a function name, and the same rule resolves it.
    """
    return handler.rsplit(".", 1)[0]


def _stale_reason(
    observation: Mapping[str, object],
    repo_root: Path,
    handler: str,
    now: datetime,
    *,
    age_staleness: bool,
) -> str | None:
    """Which staleness trigger fired, most specific first, or None."""
    if observation.get("closure_fingerprint") != closure_fingerprint(
        repo_root, _handler_module(handler)
    ):
        return "closure-changed"
    try:
        observed = datetime.fromisoformat(str(observation.get("observed_at")))
    except ValueError:
        return "age-exceeded"
    if age_staleness and now - observed > timedelta(days=STALE_AFTER_DAYS):
        return "age-exceeded"
    return None


def derive_states(
    records: Sequence[Mapping[str, object]],
    registry: Collection[RegistryTool],
    repo_root: Path,
    now: datetime,
    *,
    age_staleness: bool = True,
) -> dict[str, OperationState]:
    """Derive every operation's verification state; never stored, never an error."""
    by_operation = {
        record["operation"]: record
        for record in records
        if isinstance(record.get("operation"), str)
    }
    states: dict[str, OperationState] = {}
    for tool in registry:
        record = by_operation.get(tool.operation)
        if record is None:
            states[tool.operation] = OperationState("unrecorded")
            continue
        implementation = record.get("implementation")
        state = (
            implementation.get("state") if isinstance(implementation, dict) else None
        )
        implementation_state = state if isinstance(state, str) else None
        evidence = record.get("evidence")
        observations = [
            observation
            for observation in (evidence if isinstance(evidence, list) else [])
            if isinstance(observation, dict) and observation.get("channel") == "live"
        ]
        if not observations:
            states[tool.operation] = OperationState(
                "unevidenced", implementation=implementation_state
            )
            continue
        # An operation carries at most one live observation, because re-validation
        # replaces it; ordering by time keeps a hand-edited catalog deterministic.
        latest = max(observations, key=lambda item: str(item.get("observed_at")))
        reason = _stale_reason(
            latest,
            repo_root,
            tool.handler,
            now,
            age_staleness=age_staleness,
        )
        states[tool.operation] = OperationState(
            "stale" if reason else ("current" if latest.get("result") == "passed" else "failed"),
            reason,
            implementation_state,
            str(latest.get("observed_at")),
        )
    return states


def render_runtime_projection(
    records: Sequence[Mapping[str, object]],
    registry: Collection[RegistryTool],
    repo_root: Path,
    now: datetime,
) -> str:
    """Render the sparse, generated package projection as canonical JSON."""
    recorded = {
        str(record["operation"])
        for record in records
        if isinstance(record.get("operation"), str)
    }
    states = derive_states(
        records, registry, repo_root, now, age_staleness=False
    )
    operations = [
        {
            "operation": operation,
            "implementation": states[operation].implementation,
            "verification": states[operation].state,
            "reason": states[operation].reason,
            "observed_at": states[operation].observed_at,
        }
        for operation in sorted(recorded & states.keys())
    ]
    return json.dumps(
        {
            "format_version": 1,
            "runtime_eligibility": "existing-runtime-guards",
            "operations": operations,
        },
        indent=2,
    ) + "\n"


def write_runtime_projection(path: Path, document: str) -> None:
    """Atomically replace the generated runtime projection."""
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(document)
        temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _check_runtime_projection(path: Path, expected: str) -> list[str]:
    """Report a missing or byte-stale generated package projection."""
    try:
        actual = path.read_bytes()
    except OSError as error:
        return [
            (
                f"runtime projection {path} cannot be read: {error}; "
                "run `just capability-metadata`"
            )
        ]
    if actual != expected.encode("utf-8"):
        return [
            (
                f"runtime projection {path} is malformed or stale; "
                "run `just capability-metadata`"
            )
        ]
    return []


def _maturity_records(inventory: Path) -> list[dict[str, object]]:
    operations = load_json(inventory / "maturity.json").get("operations")
    if not isinstance(operations, list):
        return []
    return [record for record in operations if isinstance(record, dict)]


def _process_runtime_projection(
    records: Sequence[Mapping[str, object]],
    registry: Collection[RegistryTool],
    output: Path | None,
) -> list[str]:
    rendered = render_runtime_projection(records, registry, ROOT, datetime.now(UTC))
    if output is None:
        return _check_runtime_projection(DEFAULT_RUNTIME_PROJECTION, rendered)
    try:
        write_runtime_projection(output, rendered)
    except OSError as error:
        return [f"cannot write runtime projection: {error}"]
    print(f"wrote runtime projection: {output}")
    return []


def verification_report(
    states: Mapping[str, OperationState], *, fail_on_stale: bool
) -> int:
    """Print every operation's state and the summary; fail only when asked to."""
    in_actions = bool(os.environ.get("GITHUB_ACTIONS"))
    for operation in sorted(states):
        derived = states[operation]
        columns = [operation, derived.implementation, derived.state]
        print("verification: " + " ".join(column for column in columns if column))
        if in_actions and derived.state == "stale":
            print(f"::warning::{operation} is stale: {derived.reason}")
    counts = Counter(derived.state for derived in states.values())
    print(
        "verification coverage: "
        + ", ".join(
            f"{counts.get(name, 0)} {name}"
            for name in ("unrecorded", "unevidenced", "stale", "failed", "current")
        )
        + f" (of {len(states)})"
    )
    _append_step_summary(states)
    return 1 if fail_on_stale and counts.get("stale") else 0


def _append_step_summary(states: Mapping[str, OperationState]) -> None:
    """Append the report as a Markdown table to the GitHub step summary."""
    destination = os.environ.get("GITHUB_STEP_SUMMARY")
    if not destination:
        return
    lines = ["| Operation | Implementation | State | Reason |", "| --- | --- | --- | --- |"]
    lines += [
        f"| {operation} | {states[operation].implementation or ''} "
        f"| {states[operation].state} | {states[operation].reason or ''} |"
        for operation in sorted(states)
    ]
    try:
        with open(destination, "a", encoding="utf-8") as summary:
            summary.write("\n".join(lines) + "\n")
    except OSError as error:
        # The report must never fail a pull request; an unwritable summary path
        # is a runner problem, not evidence that anything is stale.
        print(f"could not append the step summary: {error}", file=sys.stderr)


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
    maturity_document = documents["maturity.json"]
    _exact_keys(
        maturity_document,
        {"format_version", "admission_policy", "operations"},
        "maturity.json",
        errors,
    )
    if maturity_document.get("format_version") != MATURITY_FORMAT_VERSION or type(
        maturity_document.get("format_version")
    ) is not int:
        errors.append(
            f"maturity.json: format_version must be integer {MATURITY_FORMAT_VERSION}"
        )
    if maturity_document.get("admission_policy") != "existing-runtime-guards":
        errors.append("maturity.json: admission_policy must be existing-runtime-guards")
    corpora_records = _objects(_array(documents["corpora.json"], "corpora", errors), "corpora", errors)
    topic_records = _objects(_array(documents["corpora.json"], "topics", errors), "topics", errors)
    unit_records = _objects(_array(documents["corpora.json"], "source_units", errors), "source_units", errors)
    row_records = _objects(_array(documents["rows.json"], "rows", errors), "rows", errors)
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
            errors.append(f"source unit {identity}: unknown topic {unit.get('topic')!r}")
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
        document.get("corpora", []) if isinstance(document.get("corpora"), list) else [],
        "corpora",
        local_errors,
    )
    corpus_ids = {str(record.get("id")) for record in corpus_records}
    topics = _objects(document.get("topics", []) if isinstance(document.get("topics"), list) else [], "topics", local_errors)
    units = _objects(
        document.get("source_units", []) if isinstance(document.get("source_units"), list) else [],
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
            errors.append(f"{corpus}: source root is missing, symlinked, or not a directory")
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
    parser.add_argument(
        "--verification-report",
        action="store_true",
        help="print each operation's derived live-verification state",
    )
    parser.add_argument(
        "--fail-on-stale",
        action="store_true",
        help="exit non-zero when any operation's evidence has gone stale",
    )
    parser.add_argument(
        "--write-runtime-projection",
        type=Path,
        help="atomically write the generated runtime projection to PATH",
    )
    args = parser.parse_args(argv)
    if args.fail_on_stale and not args.verification_report:
        parser.error("--fail-on-stale requires --verification-report")
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
    maturity_records: list[dict[str, object]] = []
    if not errors:
        maturity_records = _maturity_records(args.inventory)
        errors.extend(
            _process_runtime_projection(
                maturity_records, registry, args.write_runtime_projection
            )
        )
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        return 1
    if args.verification_report:
        # Derived staleness is never a validation error, so the report runs only
        # after validation has passed and reports separately from it.
        states = derive_states(
            maturity_records,
            registry,
            ROOT,
            datetime.now(UTC),
        )
        return verification_report(states, fail_on_stale=args.fail_on_stale)
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
