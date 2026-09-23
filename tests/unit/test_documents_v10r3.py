"""V10R3 schema conformance of the UOM write builders (#961).

Expected values come from two sources. The first is the live fixture
tests/storage/vscsi_mapping_v10r3.xml (#940). The second is the table in RECORDED, transcribed
from the live V10R3 values recorded in the #961 body. Neither source is documentation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from defusedxml import ElementTree as DET

from hmcpctl import documents
from hmcpctl.xmlutil import localname

FIXTURE = DET.parse(Path(__file__).parents[1] / "storage" / "vscsi_mapping_v10r3.xml").getroot()


def _tree(xml: str):
    return DET.fromstring(xml.encode("utf-8"))


def _first(root, name: str):
    return next(el for el in root.iter() if localname(el.tag) == name)


def _kbx(el) -> dict[str, str]:
    return {k: v for k, v in el.attrib.items() if k in ("kb", "kxe", "schemaVersion")}


def _children(el) -> list[str]:
    return [localname(child.tag) for child in el]


def _is_subsequence(needle: list[str], haystack: list[str]) -> bool:
    it = iter(haystack)
    return all(name in it for name in needle)


def test_vscsi_adapter_matches_fixture() -> None:
    built = _tree(documents.build_vscsi_adapter_document(7, 11, slot_number=4))
    live = _first(FIXTURE, "ClientAdapter")
    assert _is_subsequence(_children(built), _children(live))
    for child in built:
        name = localname(child.tag)
        assert _kbx(child) == _kbx(_first(live, name)), name


LINK = "https://hmc.example.invalid/rest/api/uom/LogicalPartition/lpar-1"
RECORDED = [
    (
        documents.build_client_network_adapter_document(42, 3, 1, True, "02:00:00:00:00:01"),
        {"VirtualSlotNumber": "COD", "VirtualSwitchID": "ROR", "PortVLANID": "CUR",
         "MACAddress": "CUR"},
    ),
]


@pytest.mark.parametrize(("xml", "expected"), RECORDED)
def test_recorded_kb_values(xml: str, expected: dict[str, str]) -> None:
    root = _tree(xml)
    assert {name: _first(root, name).attrib.get("kb") for name in expected} == expected
