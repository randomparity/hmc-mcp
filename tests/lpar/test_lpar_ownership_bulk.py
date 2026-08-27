"""Bulk per-system LPAR ownership read over the REST list feed (#375).

The live-REST survey on #374 established that the ``<Description>`` element is
inlined in the bulk list feed ``GET
/rest/api/uom/ManagedSystem/<uuid>/LogicalPartition`` (no per-partition detail
calls), round-trips the ownership-token characters (``[``, ``=``, ``:``)
byte-for-byte, and is *absent* — not empty — when the description is unset.
"""

from __future__ import annotations

import asyncio

import httpx

from hmc_mcp.operations import lpar_ownership
from hmc_mcp.server import hmc_list_lpar_ownership

SYSTEM_UUID = "22222222-2222-4222-8222-222222222222"
SYSTEM_NAME = "Server-9080-M9S-SN123456"

LIST_ROUTE = f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition"


def _lpar_entry(uuid: str, name: str, description: str | None = None) -> str:
    """Render one Atom feed entry; ``None`` omits <Description> entirely."""
    if description is None:
        desc_xml = ""
    else:
        desc_xml = f"<Description>{description}</Description>"
    return f"""  <entry>
    <id>urn:uuid:{uuid}</id>
    <title>LogicalPartition:{name}</title>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <LogicalPartition xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <PartitionName>{name}</PartitionName>
        <PartitionID>1</PartitionID>
        <PartitionState>Not Activated</PartitionState>{desc_xml}
      </LogicalPartition>
    </content>
  </entry>"""


def _feed(*entries: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom">\n'
        + "\n".join(entries)
        + "\n</feed>\n"
    )


def _hmc_env(monkeypatch) -> None:
    """Set env vars so HMCConfig() resolves inside the tool."""
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "abc123")


MIXED_FEED = _feed(
    # Owned: a well-formed ADR 0011 ownership stamp.
    _lpar_entry(
        "11111111-1111-4111-8111-111111111111",
        "lp-owned",
        "[hmc-mcp owner:alice created:2026-08-22]",
    ),
    # Owned with a trailing ADR 0064 caller segment after the stamp.
    _lpar_entry(
        "11111111-1111-4111-8111-111111111112",
        "lp-owned-caller",
        "[hmc-mcp owner:bob created:2026-01-02] [caller ticket-4711]",
    ),
    # Unparsable: a description, but not an hmc-mcp ownership token.
    _lpar_entry(
        "11111111-1111-4111-8111-111111111113",
        "lp-foreign",
        "production database server",
    ),
    # Empty description: the HMC signals it by omitting the element (#374 R1).
    _lpar_entry("11111111-1111-4111-8111-111111111114", "lp-empty"),
)


def test_bulk_read_parses_mixed_ownership(monkeypatch, mock_hmc):
    """Owned, foreign-described, and description-less LPARs stay distinct."""
    _hmc_env(monkeypatch)
    mock_hmc.get(LIST_ROUTE).mock(return_value=httpx.Response(200, text=MIXED_FEED))

    result = hmc_list_lpar_ownership(SYSTEM_UUID)

    by_name = {row["lpar_name"]: row for row in result}
    assert set(by_name) == {"lp-owned", "lp-owned-caller", "lp-foreign", "lp-empty"}

    owned = by_name["lp-owned"]
    assert owned["owned"] is True
    assert owned["owner"] == "alice"
    assert owned["unparsed"] is False
    assert owned["description"] == "[hmc-mcp owner:alice created:2026-08-22]"

    caller = by_name["lp-owned-caller"]
    assert caller["owned"] is True
    assert caller["owner"] == "bob"

    foreign = by_name["lp-foreign"]
    assert foreign["owned"] is False
    assert foreign["owner"] is None
    assert foreign["unparsed"] is True
    assert foreign["description"] == "production database server"

    empty = by_name["lp-empty"]
    assert empty["owned"] is False
    assert empty["owner"] is None
    assert empty["unparsed"] is False
    assert empty["description"] is None


def test_bulk_read_issues_exactly_one_rest_call(monkeypatch, mock_hmc):
    """One list request covers every partition — no N+1 detail calls."""
    _hmc_env(monkeypatch)
    route = mock_hmc.get(LIST_ROUTE).mock(
        return_value=httpx.Response(200, text=MIXED_FEED)
    )

    result = hmc_list_lpar_ownership(SYSTEM_UUID)

    assert route.call_count == 1
    assert len(result) == 4


def test_token_characters_round_trip_unescaped(monkeypatch, mock_hmc):
    """Bracket, equals, colon, and space survive the REST body byte-for-byte."""
    _hmc_env(monkeypatch)
    raw = "[hmc-mcp owner:ops-team created:2026-03-04] project=[env:x] note=a=b:c d"
    feed = _feed(_lpar_entry("11111111-1111-4111-8111-111111111115", "lp-chars", raw))
    mock_hmc.get(LIST_ROUTE).mock(return_value=httpx.Response(200, text=feed))

    result = hmc_list_lpar_ownership(SYSTEM_UUID)

    assert len(result) == 1
    assert result[0]["description"] == raw
    assert result[0]["owner"] == "ops-team"


def test_absent_element_differs_from_empty_element(monkeypatch, mock_hmc):
    """Absent <Description> means no description; an empty element is present text."""
    _hmc_env(monkeypatch)
    feed = _feed(
        _lpar_entry("11111111-1111-4111-8111-111111111116", "lp-absent"),
        _lpar_entry("11111111-1111-4111-8111-111111111117", "lp-blank", ""),
    )
    mock_hmc.get(LIST_ROUTE).mock(return_value=httpx.Response(200, text=feed))

    result = hmc_list_lpar_ownership(SYSTEM_UUID)

    by_name = {row["lpar_name"]: row for row in result}
    assert by_name["lp-absent"]["description"] is None
    assert by_name["lp-absent"]["unparsed"] is False
    # Defensive: the survey says the HMC omits the element when empty, but a
    # present-but-empty element must still parse as "not an ownership token".
    assert by_name["lp-blank"]["description"] == ""
    assert by_name["lp-blank"]["unparsed"] is True


def test_selector_by_system_name_resolves_then_lists_once(monkeypatch, mock_hmc):
    """A SystemName selector resolves to its UUID, then issues one list call."""
    _hmc_env(monkeypatch)
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/search/(SystemName=={SYSTEM_NAME})").mock(
        return_value=httpx.Response(
            200,
            text=_feed(
                "  <entry>"
                f"<id>urn:uuid:{SYSTEM_UUID}</id>"
                f"<title>ManagedSystem:{SYSTEM_NAME}</title>"
                '<content type="application/vnd.ibm.powervm.uom+xml">'
                '<ManagedSystem xmlns="http://www.ibm.com/xmlns/systems/power/'
                'firmware/uom/mc/2012_10/">'
                f"<SystemName>{SYSTEM_NAME}</SystemName>"
                "</ManagedSystem></content></entry>"
            ),
        )
    )
    route = mock_hmc.get(LIST_ROUTE).mock(
        return_value=httpx.Response(200, text=MIXED_FEED)
    )

    result = hmc_list_lpar_ownership(SYSTEM_NAME)

    assert route.call_count == 1
    assert {row["lpar_name"] for row in result} == {
        "lp-owned",
        "lp-owned-caller",
        "lp-foreign",
        "lp-empty",
    }


def test_omitted_selector_reads_fleet_feed_once(monkeypatch, mock_hmc):
    """No selector follows the hmc_list_lpars convention: one fleet-wide read."""
    _hmc_env(monkeypatch)
    route = mock_hmc.get("/rest/api/uom/LogicalPartition").mock(
        return_value=httpx.Response(200, text=MIXED_FEED)
    )

    result = hmc_list_lpar_ownership()

    assert route.call_count == 1
    assert len(result) == 4


def test_operation_reuses_the_shared_ownership_parser(monkeypatch, mock_hmc):
    """The bulk path parses through parse_lpar_ownership_owner, not a copy."""
    _hmc_env(monkeypatch)
    mock_hmc.get(LIST_ROUTE).mock(return_value=httpx.Response(200, text=MIXED_FEED))

    calls: list[str] = []
    real_parse = lpar_ownership.parse_lpar_ownership_owner

    def spy(description: str):
        calls.append(description)
        return real_parse(description)

    monkeypatch.setattr(lpar_ownership, "parse_lpar_ownership_owner", spy)

    from hmc_mcp.client.client_factory import client_from_env

    async def _run_op():
        async with client_from_env() as hmc:
            return await lpar_ownership.list_lpar_ownership(hmc, SYSTEM_UUID)

    result = asyncio.run(_run_op())

    assert len(result) == 4
    # The shared parser saw every description-bearing entry (three of four;
    # the absent-element partition has no description to parse).
    assert len(calls) == 3
