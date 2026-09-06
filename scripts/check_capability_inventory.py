"""Validate the versioned HMC reference capability inventory."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import re
import sys
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = ROOT / "docs" / "capabilities"
HEX_256 = re.compile(r"[0-9a-f]{64}")
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*")
TABLE_SEPARATOR = re.compile(r"^\|(?:\s*:?-+:?\s*\|)+$")
CAPTURE = re.compile(r"^(?:captured|Captured):\s*(\S.*)$", re.MULTILINE)
METHODS = {"GET", "POST", "PUT", "DELETE"}
DISPOSITIONS = {"supported", "coverage-child", "proposed-exclusion", "unknown"}


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


def _unit(topic_id: str, kind: str, line: int, text: str) -> dict[str, object]:
    normalized = " ".join(text.split())
    return {
        "id": f"{topic_id}:L{line:05d}:{kind}",
        "topic": topic_id,
        "kind": kind,
        "line": line,
        "sha256": hashlib.sha256(normalized.encode()).hexdigest(),
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
            for name in ("corpora.json", "rows.json", "operations.json")
        }
    except InventoryError as error:
        return Report((str(error),), (), (), (), 0, 0, 0, 0)
    _validate_versions(documents, errors)
    corpora_records = _objects(_array(documents["corpora.json"], "corpora", errors), "corpora", errors)
    topic_records = _objects(_array(documents["corpora.json"], "topics", errors), "topics", errors)
    unit_records = _objects(_array(documents["corpora.json"], "source_units", errors), "source_units", errors)
    row_records = _objects(_array(documents["rows.json"], "rows", errors), "rows", errors)
    operation_records = _objects(
        _array(documents["operations.json"], "operations", errors), "operations", errors
    )
    corpora = _index(corpora_records, "corpora", errors)
    topics = _index(topic_records, "topics", errors)
    units = _index(unit_records, "source_units", errors)
    rows = _index(row_records, "rows", errors)
    _validate_topics(corpora, topics, errors)
    for identity, unit in units.items():
        if unit.get("topic") not in topics:
            errors.append(f"source unit {identity}: unknown topic {unit.get('topic')!r}")
        if not HEX_256.fullmatch(str(unit.get("sha256", ""))):
            errors.append(f"source unit {identity}: invalid sha256")
    unknown, proposed, pending = _validate_rows(rows, units, errors)
    _validate_operations(operation_records, rows, registry, repo_root, errors)
    return Report(
        tuple(errors),
        tuple(sorted(unknown)),
        tuple(sorted(proposed)),
        tuple(sorted(pending)),
        len(topics),
        len(units),
        len(rows),
        len(operation_records),
    )


def verify_corpora(root: Path, sources: Mapping[str, Path]) -> list[str]:
    try:
        document = load_json(root / "corpora.json")
    except InventoryError as error:
        return [str(error)]
    topics = _objects(document.get("topics", []) if isinstance(document.get("topics"), list) else [], "topics", [])
    units = _objects(
        document.get("source_units", []) if isinstance(document.get("source_units"), list) else [],
        "source_units",
        [],
    )
    expected_paths: dict[str, set[str]] = {name: set() for name in sources}
    expected_units: dict[str, list[dict[str, object]]] = {}
    errors: list[str] = []
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
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
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
        f"{report.row_count} rows, {report.operation_count} registry operations"
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
