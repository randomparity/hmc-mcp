from __future__ import annotations

import json

import pytest

from hmc_mcp.snapshot import (
    SnapshotValidationError,
    inspect_snapshot,
    parse_snapshot,
    serialize_snapshot,
)


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


def test_complete_snapshot_round_trips_value_semantically() -> None:
    snapshot = parse_snapshot(json.dumps(_document()))
    assert json.loads(serialize_snapshot(snapshot)) == _document()


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

