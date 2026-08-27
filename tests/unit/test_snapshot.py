from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from hmc_mcp.snapshot import (
    SnapshotValidationError,
    inspect_snapshot,
    parse_snapshot,
    serialize_snapshot,
)
from hmc_mcp.operations_snapshot import assess_snapshot_affinity


def _document() -> dict:
    return {
        "format": "hmc-mcp.lpar-snapshot",
        "version": 1,
        "captured_at": "2026-08-24T20:00:01Z",
        "source": {
            "hmc": {"uuid": "hmc-1", "name": "hmc", "version": "V11R1M1110"},
            "system": {
                "uuid": "sys-1",
                "name": "sys",
                "machine_type_model": "9080-HEX",
                "serial": "ABC123",
            },
            "lpar": {"uuid": "lpar-1", "name": "aix", "partition_id": 7},
        },
        "capabilities": [
            {
                "name": "affinity-scores",
                "version": 1,
                "supported": True,
                "collection": "hmc-cli",
            },
            {
                "name": "lpar-profile-record",
                "version": 1,
                "supported": True,
                "collection": "hmc-cli",
            },
            {
                "name": "runtime-placement",
                "version": 1,
                "supported": True,
                "collection": "hmc-rest",
            },
        ],
        "configuration": {
            "profile_name": "default",
            "native": {
                "media_type": "text/vnd.ibm.hmc.lssyscfg-profile;version=1;charset=utf-8",
                "data": "name=default,lpar_name=aix,min_mem=4096,desired_mem=8192,max_mem=16384,proc_mode=shared,min_proc_units=0.5,desired_proc_units=1.0,max_proc_units=2.0,min_procs=1,desired_procs=2,max_procs=4,sharing_mode=uncap",
            },
            "normalized": {
                "memory_mib": {"minimum": 4096, "desired": 8192, "maximum": 16384},
                "processors": {
                    "dedicated": False,
                    "minimum": 0.5,
                    "desired": 1.0,
                    "maximum": 2.0,
                    "virtual_minimum": 1,
                    "virtual_desired": 2,
                    "virtual_maximum": 4,
                    "sharing_mode": "uncapped",
                    "uncapped": True,
                },
            },
        },
        "observations": {
            "observed_at": "2026-08-24T20:00:00Z",
            "runtime_placement": {
                "media_type": "application/vnd.hmc-mcp.runtime-placement+json;version=1",
                "data": {},
            },
            "scores": {
                "media_type": "application/vnd.hmc-mcp.affinity-scores+json;version=1",
                "data": {"current": {}, "predicted": {}, "resource_groups": {}},
            },
        },
    }


def _assessment_document() -> dict:
    document = _document()
    document["capabilities"].insert(
        2,
        {
            "name": "minimum-affinity-policy",
            "version": 1,
            "supported": True,
            "collection": "hmc-cli",
        },
    )
    document["observations"]["minimum_affinity_policy"] = {
        "media_type": (
            "application/vnd.hmc-mcp.minimum-affinity-policy+json;version=1"
        ),
        "data": {"min_affinity_score": 80, "min_affinity_score_action": "warn"},
    }
    return document


def test_complete_snapshot_round_trips_value_semantically() -> None:
    snapshot = parse_snapshot(json.dumps(_document()))
    assert json.loads(serialize_snapshot(snapshot)) == _document()


@pytest.mark.asyncio
async def test_snapshot_affinity_assessment_composes_captured_evidence() -> None:
    document = _assessment_document()
    document["observations"]["scores"]["data"]["current"] = {
        "lpar": {"lpar_name": "aix", "lpar_id": "7", "curr_lpar_score": "90"},
        "system": {"curr_sys_score": "91"},
    }

    result = await assess_snapshot_affinity(
        json.dumps(document),
        current_score=80,
        predicted_score=95,
        policy_state="configured",
        configured_minimum=80,
        regression_threshold=5,
        optimization_threshold=5,
        assessed_at=datetime(2026, 8, 24, 21, tzinfo=UTC),
    )

    assert result.classification == "regression"
    assert result.evidence.captured_score == 90
    assert result.evidence.assessed_at == "2026-08-24T21:00:00+00:00"


@pytest.mark.asyncio
async def test_snapshot_affinity_rejects_single_foreign_lpar_score() -> None:
    document = _assessment_document()
    document["observations"]["scores"]["data"]["current"] = {
        "lpar": {
            "lpar_name": "wrong-lpar",
            "lpar_id": "999",
            "curr_lpar_score": "90",
        },
        "system": {"curr_sys_score": "91"},
    }

    result = await assess_snapshot_affinity(
        json.dumps(document),
        current_score=80,
        predicted_score=95,
        policy_state="configured",
        configured_minimum=80,
        regression_threshold=5,
        optimization_threshold=5,
        assessed_at=datetime(2026, 8, 24, 21, tzinfo=UTC),
    )

    assert result.classification == "unsupported-data"
    assert "captured score is missing" in result.explanation


@pytest.mark.parametrize("raw", [80.9, "80.0", " 80", "080", True])
@pytest.mark.asyncio
async def test_snapshot_affinity_rejects_noncanonical_captured_scores(raw) -> None:
    document = _assessment_document()
    document["observations"]["scores"]["data"]["current"] = {
        "lpar": {"lpar_name": "aix", "lpar_id": "7", "curr_lpar_score": raw},
        "system": {"curr_sys_score": "91"},
    }

    result = await assess_snapshot_affinity(
        json.dumps(document),
        current_score=80,
        predicted_score=95,
        policy_state="configured",
        configured_minimum=80,
        regression_threshold=5,
        optimization_threshold=5,
        assessed_at=datetime(2026, 8, 24, 21, tzinfo=UTC),
    )

    assert result.classification == "unsupported-data"
    assert "captured score is missing" in result.explanation


@pytest.mark.asyncio
async def test_snapshot_affinity_rejects_anonymous_captured_score() -> None:
    document = _assessment_document()
    document["observations"]["scores"]["data"]["current"] = {
        "lpar": {"curr_lpar_score": "90"},
        "system": {"curr_sys_score": "91"},
    }

    result = await assess_snapshot_affinity(
        json.dumps(document),
        current_score=80,
        predicted_score=95,
        policy_state="configured",
        configured_minimum=80,
        regression_threshold=5,
        optimization_threshold=5,
        assessed_at=datetime(2026, 8, 24, 21, tzinfo=UTC),
    )

    assert result.classification == "unsupported-data"
    assert "captured score is missing" in result.explanation


@pytest.mark.parametrize("raw", [101, "101"])
@pytest.mark.asyncio
async def test_snapshot_affinity_returns_unsupported_for_out_of_range_score(
    raw,
) -> None:
    document = _assessment_document()
    document["observations"]["scores"]["data"]["current"] = {
        "lpar": {"lpar_name": "aix", "lpar_id": "7", "curr_lpar_score": raw},
        "system": {"curr_sys_score": "91"},
    }

    result = await assess_snapshot_affinity(
        json.dumps(document),
        current_score=80,
        predicted_score=95,
        policy_state="configured",
        configured_minimum=80,
        regression_threshold=5,
        optimization_threshold=5,
        assessed_at=datetime(2026, 8, 24, 21, tzinfo=UTC),
    )

    assert result.classification == "unsupported-data"
    assert "captured score is missing" in result.explanation


@pytest.mark.asyncio
async def test_snapshot_affinity_uses_thresholds_when_policy_is_unsupported() -> None:
    document = _document()
    document["capabilities"].insert(
        2,
        {
            "name": "minimum-affinity-policy",
            "version": 1,
            "supported": False,
            "collection": "hmc-cli",
            "unavailable_reason": "unsupported platform",
        },
    )
    document["observations"]["scores"]["data"]["current"] = {
        "lpar": {"lpar_name": "aix", "lpar_id": "7", "curr_lpar_score": "90"},
        "system": {"curr_sys_score": "91"},
    }

    result = await assess_snapshot_affinity(
        json.dumps(document),
        current_score=80,
        predicted_score=95,
        regression_threshold=5,
        optimization_threshold=5,
        assessed_at=datetime(2026, 8, 24, 21, tzinfo=UTC),
    )

    assert result.classification == "regression"
    assert result.evidence.captured_policy_state == "unsupported"


def test_minimum_affinity_policy_observation_round_trips() -> None:
    document = _document()
    document["capabilities"].insert(
        2,
        {
            "name": "minimum-affinity-policy",
            "version": 1,
            "supported": True,
            "collection": "hmc-cli",
        },
    )
    document["observations"]["minimum_affinity_policy"] = {
        "media_type": (
            "application/vnd.hmc-mcp.minimum-affinity-policy+json;version=1"
        ),
        "data": {"min_affinity_score": 80, "min_affinity_score_action": "warn"},
    }
    assert (
        json.loads(serialize_snapshot(parse_snapshot(json.dumps(document)))) == document
    )


def test_unsupported_minimum_affinity_policy_requires_reason_and_no_observation():
    document = _document()
    document["capabilities"].insert(
        2,
        {
            "name": "minimum-affinity-policy",
            "version": 1,
            "supported": False,
            "collection": "hmc-cli",
            "unavailable_reason": "upgrade system firmware",
        },
    )
    assert parse_snapshot(json.dumps(document)).capabilities[2].supported is False

    document["capabilities"][2]["unavailable_reason"] = " "
    with pytest.raises(SnapshotValidationError, match="unavailable_reason"):
        parse_snapshot(json.dumps(document))


def test_supported_minimum_affinity_policy_requires_observation_and_no_reason():
    document = _document()
    document["capabilities"].insert(
        2,
        {
            "name": "minimum-affinity-policy",
            "version": 1,
            "supported": True,
            "collection": "hmc-cli",
            "unavailable_reason": "not allowed",
        },
    )
    with pytest.raises(SnapshotValidationError, match="minimum-affinity-policy"):
        parse_snapshot(json.dumps(document))


@pytest.mark.parametrize(
    ("mutation", "pointer"),
    [
        (lambda value: value.pop("source"), "/source"),
        (lambda value: value.update(extra=True), "/extra"),
        (lambda value: value.update(version=2), "/version"),
        (lambda value: value.update(captured_at="later"), "/captured_at"),
        (
            lambda value: value["source"]["lpar"].update(name=" "),
            "/source/lpar/name",
        ),
        (
            lambda value: value["configuration"]["normalized"]["memory_mib"].update(
                desired=9999
            ),
            "/configuration/normalized",
        ),
    ],
)
def test_invalid_snapshot_reports_precise_pointer(mutation, pointer) -> None:
    document = _document()
    mutation(document)
    with pytest.raises(SnapshotValidationError, match=pointer):
        parse_snapshot(json.dumps(document))


def test_duplicate_members_are_rejected() -> None:
    with pytest.raises(SnapshotValidationError, match="/version"):
        parse_snapshot('{"format":"hmc-mcp.lpar-snapshot","version":1,"version":1}')


def test_nested_duplicate_reports_full_pointer() -> None:
    text = serialize_snapshot(parse_snapshot(json.dumps(_document())))
    text = text.replace('"minimum":4096', '"minimum":4096,"minimum":4096', 1)
    with pytest.raises(
        SnapshotValidationError, match=r"/configuration/normalized/memory_mib/minimum"
    ):
        parse_snapshot(text)


def test_numeric_strings_are_not_coerced() -> None:
    document = _document()
    document["source"]["lpar"]["partition_id"] = "7"
    with pytest.raises(SnapshotValidationError, match="/source/lpar/partition_id"):
        parse_snapshot(json.dumps(document))


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nonstandard_json_constants_are_rejected(constant: str) -> None:
    with pytest.raises(
        SnapshotValidationError,
        match="snapshot inspection failed.*non-standard JSON constant",
    ):
        inspect_snapshot(
            '{"format":"hmc-mcp.lpar-snapshot","version":1,"value":' + constant + "}"
        )


def test_required_source_identities_reject_whitespace() -> None:
    document = _document()
    document["source"]["system"]["serial"] = " "
    with pytest.raises(SnapshotValidationError, match="/source/system/serial"):
        parse_snapshot(json.dumps(document))


def test_profile_name_rejects_whitespace() -> None:
    document = _document()
    document["configuration"]["profile_name"] = " "
    document["configuration"]["native"]["data"] = document["configuration"]["native"][
        "data"
    ].replace("name=default", "name= ")
    with pytest.raises(SnapshotValidationError, match="/configuration/profile_name"):
        parse_snapshot(json.dumps(document))


def test_timestamp_requires_rfc3339_separator_and_offset() -> None:
    document = _document()
    document["captured_at"] = "2026-08-24 20:00:01+00:00"
    with pytest.raises(SnapshotValidationError) as raised:
        parse_snapshot(json.dumps(document))
    assert raised.value.pointer == "/captured_at"


def test_serializer_canonicalizes_timestamps_to_utc_seconds() -> None:
    document = _document()
    document["captured_at"] = "2026-08-24T13:00:01.987654-07:00"
    document["observations"]["observed_at"] = "2026-08-24T13:00:00.123456-07:00"
    payload = json.loads(serialize_snapshot(parse_snapshot(json.dumps(document))))
    assert payload["captured_at"] == "2026-08-24T20:00:01Z"
    assert payload["observations"]["observed_at"] == "2026-08-24T20:00:00Z"


def test_serializer_enforces_reader_size_limit() -> None:
    document = _document()
    document["configuration"]["native"]["data"] += ",padding=" + ("x" * 1_048_576)
    snapshot = parse_snapshot(json.dumps(_document()))
    oversized = snapshot.model_copy(
        update={
            "configuration": snapshot.configuration.model_copy(
                update={
                    "native": snapshot.configuration.native.model_copy(
                        update={"data": document["configuration"]["native"]["data"]}
                    )
                }
            )
        }
    )
    with pytest.raises(SnapshotValidationError, match="snapshot serialization.*1 MiB"):
        serialize_snapshot(oversized)


def test_native_profile_never_appears_in_diagnostic() -> None:
    document = _document()
    secret = document["configuration"]["native"]["data"]
    document["configuration"]["normalized"]["processors"]["desired"] = 9
    with pytest.raises(SnapshotValidationError) as raised:
        parse_snapshot(json.dumps(document))
    assert secret not in str(raised.value)


def test_inspection_identifies_unsupported_version_without_validation() -> None:
    result = inspect_snapshot('{"format":"hmc-mcp.lpar-snapshot","version":2}')
    assert result.format == "hmc-mcp.lpar-snapshot"
    assert result.version == 2
    assert result.supported is False


def test_raw_text_limit_is_utf8_bytes() -> None:
    with pytest.raises(SnapshotValidationError, match="1 MiB"):
        inspect_snapshot('"' + ("é" * 524_288) + '"')


@pytest.mark.parametrize("operation", [parse_snapshot, inspect_snapshot])
def test_excessive_json_nesting_has_bounded_diagnostic(operation) -> None:
    text = ("[" * 900) + "0" + ("]" * 900)
    with pytest.raises(
        SnapshotValidationError, match="JSON nesting exceeds 100 levels"
    ):
        operation(text)


@pytest.mark.parametrize("operation", [parse_snapshot, inspect_snapshot])
def test_oversized_integer_literal_has_bounded_diagnostic(operation) -> None:
    text = '{"format":"hmc-mcp.lpar-snapshot","version":' + ("9" * 5_000) + "}"
    with pytest.raises(
        SnapshotValidationError, match="JSON number exceeds the supported size"
    ):
        operation(text)


@pytest.mark.parametrize("number", ["1e999999", "-1e999999", "1e-999999", "-1e-999999"])
@pytest.mark.parametrize("operation", [parse_snapshot, inspect_snapshot])
def test_unrepresentable_float_has_bounded_diagnostic(number: str, operation) -> None:
    if operation is parse_snapshot:
        document = json.dumps(_document())
        text = document.replace('"data": {}', f'"data": {{"value": {number}}}', 1)
    else:
        text = '{"value":' + number + "}"
    with pytest.raises(
        SnapshotValidationError, match="JSON number is not finitely representable"
    ):
        operation(text)
