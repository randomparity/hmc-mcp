"""Strict version-1 portable LPAR snapshot values and local I/O."""

from __future__ import annotations

import json
import math
import re
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, NoReturn, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

FORMAT = "hmc-mcp.lpar-snapshot"
VERSION = 1
MAX_SNAPSHOT_BYTES = 1024 * 1024
PROFILE_MEDIA_TYPE = "text/vnd.ibm.hmc.lssyscfg-profile;version=1;charset=utf-8"
PLACEMENT_MEDIA_TYPE = "application/vnd.hmc-mcp.runtime-placement+json;version=1"
SCORES_MEDIA_TYPE = "application/vnd.hmc-mcp.affinity-scores+json;version=1"
_RFC3339 = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


class SnapshotValidationError(ValueError):
    """A safe, actionable snapshot validation diagnostic."""

    def __init__(self, operation: str, pointer: str, rule: str, correction: str):
        super().__init__(
            f"{operation} failed at {pointer}: {rule}. Suggested correction: {correction}"
        )
        self.operation = operation
        self.pointer = pointer
        self.rule = rule
        self.correction = correction


class _Value(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class HmcIdentity(_Value):
    uuid: str = Field(min_length=1)
    name: str | None
    version: str | None

    @field_validator("uuid")
    @classmethod
    def nonblank_uuid(cls, value: str) -> str:
        return _nonblank(value)

    @field_validator("name", "version")
    @classmethod
    def nonblank_optional(cls, value: str | None) -> str | None:
        return _nonblank(value) if value is not None else None


class SystemIdentity(_Value):
    uuid: str = Field(min_length=1)
    name: str | None
    machine_type_model: str = Field(min_length=1)
    serial: str = Field(min_length=1)

    @field_validator("uuid", "machine_type_model", "serial")
    @classmethod
    def nonblank_required(cls, value: str) -> str:
        return _nonblank(value)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str | None) -> str | None:
        return _nonblank(value) if value is not None else None


class LparIdentity(_Value):
    uuid: str = Field(min_length=1)
    name: str = Field(min_length=1)
    partition_id: int = Field(gt=0)

    @field_validator("uuid", "name")
    @classmethod
    def nonblank(cls, value: str) -> str:
        return _nonblank(value)


class SnapshotSource(_Value):
    hmc: HmcIdentity
    system: SystemIdentity
    lpar: LparIdentity


class SnapshotCapability(_Value):
    name: Literal["affinity-scores", "lpar-profile-record", "runtime-placement"]
    version: Literal[1]
    supported: bool
    collection: Literal["hmc-rest", "hmc-cli", "derived"]


class NativeProfile(_Value):
    media_type: Literal["text/vnd.ibm.hmc.lssyscfg-profile;version=1;charset=utf-8"]
    data: str = Field(min_length=1)


class MemoryProjection(_Value):
    minimum: int = Field(gt=0)
    desired: int = Field(gt=0)
    maximum: int = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if not self.minimum <= self.desired <= self.maximum:
            raise ValueError("memory values must satisfy minimum <= desired <= maximum")
        return self


class ProcessorProjection(_Value):
    dedicated: bool
    minimum: float = Field(gt=0)
    desired: float = Field(gt=0)
    maximum: float = Field(gt=0)
    virtual_minimum: int = Field(gt=0)
    virtual_desired: int = Field(gt=0)
    virtual_maximum: int = Field(gt=0)
    sharing_mode: Literal[
        "keep_idle_procs",
        "share_idle_procs",
        "share_idle_procs_active",
        "share_idle_procs_always",
        "capped",
        "uncapped",
    ]
    uncapped: bool

    @model_validator(mode="after")
    def consistent(self) -> Self:
        values = (self.minimum, self.desired, self.maximum)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("processor values must be finite")
        if not self.minimum <= self.desired <= self.maximum:
            raise ValueError(
                "processor values must satisfy minimum <= desired <= maximum"
            )
        if not self.virtual_minimum <= self.virtual_desired <= self.virtual_maximum:
            raise ValueError("virtual processor values must be ordered")
        dedicated_modes = {
            "keep_idle_procs",
            "share_idle_procs",
            "share_idle_procs_active",
            "share_idle_procs_always",
        }
        if self.dedicated != (self.sharing_mode in dedicated_modes):
            raise ValueError("dedicated must agree with sharing_mode")
        if self.uncapped != (self.sharing_mode == "uncapped"):
            raise ValueError("uncapped must agree with sharing_mode")
        if self.dedicated and any(not float(value).is_integer() for value in values):
            raise ValueError("dedicated processor values must be integers")
        return self


class NormalizedConfiguration(_Value):
    memory_mib: MemoryProjection
    processors: ProcessorProjection


class SnapshotConfiguration(_Value):
    profile_name: str = Field(min_length=1)
    native: NativeProfile
    normalized: NormalizedConfiguration

    @field_validator("profile_name")
    @classmethod
    def nonblank_profile(cls, value: str) -> str:
        return _nonblank(value)


class ObservationEnvelope(_Value):
    media_type: str = Field(min_length=1)
    data: dict[str, Any]


class SnapshotObservations(_Value):
    observed_at: datetime
    runtime_placement: ObservationEnvelope | None = None
    scores: ObservationEnvelope | None = None


class LparSnapshot(_Value):
    format: Literal["hmc-mcp.lpar-snapshot"]
    version: Literal[1]
    captured_at: datetime
    source: SnapshotSource
    capabilities: tuple[SnapshotCapability, ...]
    configuration: SnapshotConfiguration
    observations: SnapshotObservations

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.captured_at.tzinfo is None:
            raise ValueError("captured_at must include an explicit offset")
        if self.observations.observed_at.tzinfo is None:
            raise ValueError("observed_at must include an explicit offset")
        if self.observations.observed_at > self.captured_at:
            raise ValueError("observed_at must not be later than captured_at")
        names = [item.name for item in self.capabilities]
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("capabilities must be unique and sorted by name")
        profile = next(
            (item for item in self.capabilities if item.name == "lpar-profile-record"),
            None,
        )
        if profile is None or not profile.supported or profile.collection != "hmc-cli":
            raise ValueError(
                "lpar-profile-record capability must be supported via hmc-cli"
            )
        self._check_observation(
            "runtime-placement",
            self.observations.runtime_placement,
            PLACEMENT_MEDIA_TYPE,
        )
        self._check_observation(
            "affinity-scores", self.observations.scores, SCORES_MEDIA_TYPE
        )
        native = _parse_profile(self.configuration.native.data)
        if native.get("name") != self.configuration.profile_name:
            raise ValueError(
                "native profile name must equal configuration profile_name"
            )
        if native.get("lpar_name") != self.source.lpar.name:
            raise ValueError("native lpar_name must equal source LPAR name")
        expected = _normalized_from_profile(native)
        if expected != self.configuration.normalized:
            raise ValueError("native profile and normalized projection must agree")
        return self

    def _check_observation(
        self, name: str, value: ObservationEnvelope | None, media_type: str
    ) -> None:
        capability = next(
            (item for item in self.capabilities if item.name == name), None
        )
        if value is not None and (capability is None or not capability.supported):
            raise ValueError(f"{name} observation requires a supported capability")
        if value is not None and value.media_type != media_type:
            raise ValueError(f"{name} observation media_type is unsupported")


class SnapshotInspection(_Value):
    format: str | None
    version: int | None
    supported: bool


def _pointer(location: tuple[Any, ...]) -> str:
    if not location:
        return "/"
    return "/" + "/".join(
        str(part).replace("~", "~0").replace("/", "~1") for part in location
    )


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("identity must not be blank")
    return value


def _error(
    pointer: str, rule: str, correction: str = "correct the snapshot document"
) -> NoReturn:
    raise SnapshotValidationError("snapshot validation", pointer, rule, correction)


def _relabel(error: SnapshotValidationError, operation: str) -> SnapshotValidationError:
    return SnapshotValidationError(
        operation, error.pointer, error.rule, error.correction
    )


def _bounded(text: str) -> None:
    if len(text.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        _error("/", "document exceeds 1 MiB", "provide a snapshot no larger than 1 MiB")


class _DuplicateScanner:
    def __init__(self, text: str):
        self.text = text
        self.decoder = json.JSONDecoder()

    def scan(self) -> None:
        end = self._value(0, ())
        if self.text[end:].strip():
            return

    def _space(self, index: int) -> int:
        while index < len(self.text) and self.text[index].isspace():
            index += 1
        return index

    def _value(self, index: int, path: tuple[Any, ...]) -> int:
        index = self._space(index)
        if index >= len(self.text):
            return index
        if self.text[index] == "{":
            return self._object(index, path)
        if self.text[index] == "[":
            return self._array(index, path)
        try:
            _, end = self.decoder.raw_decode(self.text, index)
        except json.JSONDecodeError:
            return len(self.text)
        return end

    def _object(self, index: int, path: tuple[Any, ...]) -> int:
        index = self._space(index + 1)
        keys: set[str] = set()
        if index < len(self.text) and self.text[index] == "}":
            return index + 1
        while index < len(self.text):
            try:
                key, end = self.decoder.raw_decode(self.text, index)
            except json.JSONDecodeError:
                return len(self.text)
            if not isinstance(key, str):
                return len(self.text)
            if key in keys:
                _error(_pointer((*path, key)), "duplicate JSON member")
            keys.add(key)
            index = self._space(end)
            if index >= len(self.text) or self.text[index] != ":":
                return len(self.text)
            index = self._space(self._value(index + 1, (*path, key)))
            if index < len(self.text) and self.text[index] == "}":
                return index + 1
            if index >= len(self.text) or self.text[index] != ",":
                return len(self.text)
            index = self._space(index + 1)
        return index

    def _array(self, index: int, path: tuple[Any, ...]) -> int:
        index = self._space(index + 1)
        offset = 0
        if index < len(self.text) and self.text[index] == "]":
            return index + 1
        while index < len(self.text):
            index = self._space(self._value(index, (*path, offset)))
            if index < len(self.text) and self.text[index] == "]":
                return index + 1
            if index >= len(self.text) or self.text[index] != ",":
                return len(self.text)
            index = self._space(index + 1)
            offset += 1
        return index


def _load(text: str) -> Any:
    _bounded(text)
    try:
        _DuplicateScanner(text).scan()
        return json.loads(
            text,
            parse_constant=lambda value: _error(
                "/", f"non-standard JSON constant {value} is not permitted"
            ),
        )
    except SnapshotValidationError:
        raise
    except json.JSONDecodeError as exc:
        _error("/", f"invalid JSON at line {exc.lineno} column {exc.colno}")


def _parse_profile(record: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in record.split(","):
        if item.count("=") != 1:
            raise ValueError("native profile contains an unsupported attribute record")
        key, value = item.split("=", 1)
        if not key or not key.replace("_", "a").isalnum() or not key.isascii():
            raise ValueError("native profile contains an invalid attribute name")
        if key in values:
            raise ValueError("native profile contains a duplicate attribute")
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("native profile contains a control character")
        if any(character in value for character in ',="'):
            raise ValueError(
                "native profile contains unsupported quoting or delimiters"
            )
        values[key] = value
    return values


def _normalized_from_profile(values: dict[str, str]) -> NormalizedConfiguration:
    required = (
        "min_mem",
        "desired_mem",
        "max_mem",
        "proc_mode",
        "min_proc_units",
        "desired_proc_units",
        "max_proc_units",
        "min_procs",
        "desired_procs",
        "max_procs",
        "sharing_mode",
    )
    missing = [key for key in required if key not in values]
    if missing:
        raise ValueError("native profile is missing required normalized attributes")
    modes = {
        "keep_idle_procs": "keep_idle_procs",
        "share_idle_procs": "share_idle_procs",
        "share_idle_procs_active": "share_idle_procs_active",
        "share_idle_procs_always": "share_idle_procs_always",
        "cap": "capped",
        "uncap": "uncapped",
    }
    mode = modes.get(values["sharing_mode"])
    if values["proc_mode"] not in {"ded", "shared"} or mode is None:
        raise ValueError("native profile contains unsupported processor values")
    try:
        return NormalizedConfiguration(
            memory_mib=MemoryProjection(
                minimum=int(values["min_mem"]),
                desired=int(values["desired_mem"]),
                maximum=int(values["max_mem"]),
            ),
            processors=ProcessorProjection(
                dedicated=values["proc_mode"] == "ded",
                minimum=float(values["min_proc_units"]),
                desired=float(values["desired_proc_units"]),
                maximum=float(values["max_proc_units"]),
                virtual_minimum=int(values["min_procs"]),
                virtual_desired=int(values["desired_procs"]),
                virtual_maximum=int(values["max_procs"]),
                sharing_mode=cast(
                    Literal[
                        "keep_idle_procs",
                        "share_idle_procs",
                        "share_idle_procs_active",
                        "share_idle_procs_always",
                        "capped",
                        "uncapped",
                    ],
                    mode,
                ),
                uncapped=mode == "uncapped",
            ),
        )
    except (ValueError, ValidationError) as exc:
        raise ValueError("native profile contains invalid normalized values") from exc


def parse_snapshot(text: str) -> LparSnapshot:
    """Parse and strictly validate one version-1 snapshot JSON document."""
    value = _load(text)
    if not isinstance(value, dict):
        _error("/", "snapshot root must be an object")
    try:
        prepared = dict(value)
        captured = prepared.get("captured_at")
        if isinstance(captured, str):
            if _RFC3339.fullmatch(captured) is None:
                _error("/captured_at", "timestamp must use RFC 3339 syntax")
            try:
                prepared["captured_at"] = datetime.fromisoformat(
                    captured.replace("Z", "+00:00")
                )
            except ValueError:
                _error("/captured_at", "timestamp must be valid RFC 3339")
        capabilities = prepared.get("capabilities")
        if isinstance(capabilities, list):
            prepared["capabilities"] = tuple(capabilities)
        observations = prepared.get("observations")
        if isinstance(observations, dict):
            prepared["observations"] = dict(observations)
            observed = observations.get("observed_at")
            if isinstance(observed, str):
                if _RFC3339.fullmatch(observed) is None:
                    _error(
                        "/observations/observed_at",
                        "timestamp must use RFC 3339 syntax",
                    )
                try:
                    prepared["observations"]["observed_at"] = datetime.fromisoformat(
                        observed.replace("Z", "+00:00")
                    )
                except ValueError:
                    _error(
                        "/observations/observed_at",
                        "timestamp must be valid RFC 3339",
                    )
        return LparSnapshot.model_validate(prepared)
    except ValidationError as exc:
        item = exc.errors(include_input=False)[0]
        message = item["msg"]
        pointer = _pointer(item["loc"])
        if "native profile and normalized projection" in message:
            pointer = "/configuration/normalized"
        _error(pointer, message)
    except ValueError as exc:
        _error("/configuration/normalized", str(exc))


def serialize_snapshot(snapshot: LparSnapshot) -> str:
    """Serialize a validated snapshot as deterministic UTF-8 JSON text."""
    payload = snapshot.model_dump(mode="json", exclude_none=True)
    payload["captured_at"] = _writer_timestamp(snapshot.captured_at)
    payload["observations"]["observed_at"] = _writer_timestamp(
        snapshot.observations.observed_at
    )
    try:
        text = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=False,
            allow_nan=False,
        )
    except ValueError as exc:
        raise SnapshotValidationError(
            "snapshot serialization",
            "/",
            "snapshot contains a non-finite JSON number",
            "correct the snapshot document",
        ) from exc
    try:
        _bounded(text)
    except SnapshotValidationError as exc:
        raise _relabel(exc, "snapshot serialization") from exc
    return text


def _writer_timestamp(value: datetime) -> str:
    return (
        value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def read_snapshot(path: Path) -> LparSnapshot:
    """Read a bounded regular UTF-8 snapshot file and validate it."""
    try:
        return parse_snapshot(read_snapshot_text(path))
    except SnapshotValidationError as exc:
        raise _relabel(exc, "snapshot read") from exc


def read_snapshot_text(path: Path) -> str:
    """Read bounded UTF-8 snapshot text from a non-symlink regular file."""
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            _error("/", "snapshot path must name a regular file")
        if info.st_size > MAX_SNAPSHOT_BYTES:
            _error("/", "document exceeds 1 MiB")
        return path.read_text(encoding="utf-8")
    except SnapshotValidationError as exc:
        raise _relabel(exc, "snapshot read") from exc
    except (OSError, UnicodeError) as exc:
        raise SnapshotValidationError(
            "snapshot read",
            "/",
            f"cannot read UTF-8 snapshot file: {exc}",
            "provide a readable UTF-8 snapshot file",
        ) from exc


def inspect_snapshot(text: str) -> SnapshotInspection:
    """Inspect only the discriminator and version without accepting the document."""
    try:
        value = _load(text)
        if not isinstance(value, dict):
            _error("/", "snapshot root must be an object")
        format_value = value.get("format")
        version_value = value.get("version")
        if format_value is not None and not isinstance(format_value, str):
            _error("/format", "format must be a string")
        if version_value is not None and (
            not isinstance(version_value, int) or isinstance(version_value, bool)
        ):
            _error("/version", "version must be an integer")
        return SnapshotInspection(
            format=format_value,
            version=version_value,
            supported=format_value == FORMAT and version_value == VERSION,
        )
    except SnapshotValidationError as exc:
        raise _relabel(exc, "snapshot inspection") from exc
