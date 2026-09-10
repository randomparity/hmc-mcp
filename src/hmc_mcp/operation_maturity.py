"""Read the generated operation-maturity package resource."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import resources
from types import MappingProxyType
from typing import Any

_RESOURCE = "_operation_maturity.json"
_MAX_RESOURCE_BYTES = 1_048_576
_FORMAT_VERSION = 1
_RUNTIME_ELIGIBILITY = "existing-runtime-guards"
_STALE_AFTER = timedelta(days=90)
_OPERATION = re.compile(r"[a-z0-9_]+\.[a-z0-9_]+")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_IMPLEMENTATION_STATES = {"absent", "partial", "implemented"}
_VERIFICATION_STATES = {"unevidenced", "current", "stale", "failed"}
_STALE_REASONS = {"closure-changed", "age-exceeded"}
_ROOT_KEYS = {"format_version", "runtime_eligibility", "operations"}
_OPERATION_KEYS = {
    "operation",
    "implementation",
    "verification",
    "reason",
    "observed_at",
}


class OperationMaturityError(ValueError):
    """The packaged operation-maturity projection is invalid."""


@dataclass(frozen=True)
class OperationMaturity:
    implementation: str
    verification: str
    runtime_eligibility: str
    reason: str | None = None


@dataclass(frozen=True)
class _ProjectedOperation:
    maturity: OperationMaturity
    observed_at: datetime | None


def _unique_pairs(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OperationMaturityError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _parse_timestamp(value: object, operation: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or _TIMESTAMP.fullmatch(value) is None:
        raise OperationMaturityError(
            f"operation {operation!r} has invalid observed_at timestamp"
        )
    try:
        return datetime.fromisoformat(value).astimezone(UTC)
    except ValueError as error:
        raise OperationMaturityError(
            f"operation {operation!r} has invalid observed_at timestamp"
        ) from error


def _parse_operation(value: object, policy: str) -> tuple[str, _ProjectedOperation]:
    if not isinstance(value, dict) or set(value) != _OPERATION_KEYS:
        raise OperationMaturityError(
            f"projection operation must contain exactly {sorted(_OPERATION_KEYS)}"
        )
    operation = value["operation"]
    if not isinstance(operation, str) or _OPERATION.fullmatch(operation) is None:
        raise OperationMaturityError(f"invalid operation ID {operation!r} in projection")
    implementation = value["implementation"]
    verification = value["verification"]
    reason = value["reason"]
    if (
        not isinstance(implementation, str)
        or implementation not in _IMPLEMENTATION_STATES
    ):
        raise OperationMaturityError(
            f"operation {operation!r} has invalid implementation state"
        )
    if not isinstance(verification, str) or verification not in _VERIFICATION_STATES:
        raise OperationMaturityError(
            f"operation {operation!r} has invalid verification state"
        )
    if reason is not None and (
        not isinstance(reason, str) or reason not in _STALE_REASONS
    ):
        raise OperationMaturityError(f"operation {operation!r} has invalid reason")
    observed_at = _parse_timestamp(value["observed_at"], operation)
    has_observation = verification in {"current", "stale", "failed"}
    if has_observation != (observed_at is not None):
        raise OperationMaturityError(
            f"operation {operation!r} has inconsistent verification timestamp"
        )
    if (verification == "stale") != (reason is not None):
        raise OperationMaturityError(
            f"operation {operation!r} has inconsistent verification reason"
        )
    return operation, _ProjectedOperation(
        OperationMaturity(implementation, verification, policy, reason), observed_at
    )


def _load_projection() -> dict[str, _ProjectedOperation]:
    try:
        content = resources.files("hmc_mcp").joinpath(_RESOURCE).read_bytes()
        if len(content) > _MAX_RESOURCE_BYTES:
            raise OperationMaturityError("operation-maturity projection exceeds size limit")
        document = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except OperationMaturityError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise OperationMaturityError(
            f"cannot read packaged operation-maturity projection: {error}"
        ) from error
    if not isinstance(document, dict) or set(document) != _ROOT_KEYS:
        raise OperationMaturityError(
            f"projection root must contain exactly {sorted(_ROOT_KEYS)}"
        )
    if type(document["format_version"]) is not int or document["format_version"] != _FORMAT_VERSION:
        raise OperationMaturityError(
            f"projection format_version must be integer {_FORMAT_VERSION}"
        )
    policy = document["runtime_eligibility"]
    if policy != _RUNTIME_ELIGIBILITY:
        raise OperationMaturityError(
            f"projection runtime_eligibility must be {_RUNTIME_ELIGIBILITY}"
        )
    records = document["operations"]
    if not isinstance(records, list):
        raise OperationMaturityError("projection operations must be an array")
    result: dict[str, _ProjectedOperation] = {}
    for record in records:
        operation, projected = _parse_operation(record, policy)
        if operation in result:
            raise OperationMaturityError(f"duplicate projected operation {operation!r}")
        result[operation] = projected
    if list(result) != sorted(result):
        raise OperationMaturityError("projected operations must be sorted")
    return result


def _resolved(
    projected: _ProjectedOperation, now: datetime
) -> OperationMaturity:
    maturity = projected.maturity
    if (
        maturity.verification in {"current", "failed"}
        and projected.observed_at is not None
        and now - projected.observed_at > _STALE_AFTER
    ):
        return OperationMaturity(
            maturity.implementation,
            "stale",
            maturity.runtime_eligibility,
            "age-exceeded",
        )
    return maturity


def _now(value: datetime | None) -> datetime:
    resolved = value or datetime.now(UTC)
    if resolved.tzinfo is None or resolved.utcoffset() is None:
        raise OperationMaturityError("now must be timezone-aware")
    return resolved.astimezone(UTC)


def operation_maturity_catalog(
    *, now: datetime | None = None
) -> Mapping[str, OperationMaturity]:
    """Return the immutable sparse operation-maturity projection."""
    resolved_now = _now(now)
    return MappingProxyType(
        {
            operation: _resolved(projected, resolved_now)
            for operation, projected in _load_projection().items()
        }
    )


def operation_maturity(
    operation: str, *, now: datetime | None = None
) -> OperationMaturity:
    """Return one operation's maturity, including the sparse default."""
    if not isinstance(operation, str) or _OPERATION.fullmatch(operation) is None:
        raise OperationMaturityError(f"invalid operation ID {operation!r}")
    catalog = operation_maturity_catalog(now=now)
    return catalog.get(
        operation,
        OperationMaturity(
            "unrecorded", "unrecorded", _RUNTIME_ELIGIBILITY
        ),
    )


def operation_maturity_meta(
    operation: str, *, now: datetime | None = None
) -> dict[str, str]:
    """Return one operation's maturity in the presentation metadata shape."""
    maturity = operation_maturity(operation, now=now)
    result = {
        "implementation": maturity.implementation,
        "verification": maturity.verification,
        "runtime_eligibility": maturity.runtime_eligibility,
    }
    if maturity.reason is not None:
        result["reason"] = maturity.reason
    return result
