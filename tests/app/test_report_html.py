"""HTML rendering contracts for ``hmcpctl report utilization --html`` (#1254)."""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import replace
from datetime import UTC, datetime

from hmcpctl.cli_commands import report
from hmcpctl.cli_commands.report_html import render_html
from hmcpctl.operations.inventory.utilization import (
    AdapterFigures,
    CpuFigures,
    DiskFigures,
    FleetSurvey,
    MemoryFigures,
    PartitionFigures,
    ProfileFailure,
    SystemReading,
)

GENERATED = datetime(2026, 10, 1, 12, 34, tzinfo=UTC)
HOSTILE = "<script>alert(1)</script>\"'&"
EXTERNAL = re.compile(r"(?i)\b(?:src|href)\s*=|url\(|@import")


def _reading(profile: str, ident: str, **changes: object) -> SystemReading:
    reading = SystemReading(
        profile=profile,
        name=f"system-{ident[-1]}",
        machine_type="9080",
        model="HEX",
        serial=ident,
        firmware="FW1120.00 (1)",
        state="operating",
        cpu=CpuFigures(48.0, 48.0, 2.0, 8.0, 4.0, 0.0, 34.0, 12.0, 2.0),
        memory=MemoryFigures(65536, 65536, 2048, 1024, 8192, 4096, 0, 50176),
        partitions=PartitionFigures(2, 1, 0, 0, 0, 0),
        disk=DiskFigures(286102, 286102, 0, 102400, 0, 102400),
        adapters=AdapterFigures(1, 1, 1, 1, 1, 48, 2),
        shared_pools=(0,),
        gaps=(),
    )
    return replace(reading, **changes)


SURVEY = FleetSurvey(
    profiles=("hmc-a", "hmc-b", "hmc-c"),
    readings=(_reading("hmc-a", "SER0001"), _reading("hmc-b", "SER0002")),
    failures=(ProfileFailure("hmc-c", "no answer within 300 s"),),
)


def _page(survey: FleetSurvey) -> str:
    return render_html(report.report_rows(survey), survey.profiles, GENERATED)


def test_page_loads_nothing_external() -> None:
    page = _page(SURVEY)

    assert EXTERNAL.search(page) is None
    assert "default-src 'none'" in page


def test_csp_hash_matches_the_inline_script() -> None:
    page = _page(SURVEY)

    scripts = re.findall(r"<script>(.*?)</script>", page, re.DOTALL)
    assert len(scripts) == 1
    digest = base64.b64encode(hashlib.sha256(scripts[0].encode()).digest()).decode()
    assert f"script-src 'sha256-{digest}'" in page


def test_hostile_text_renders_escaped() -> None:
    text = dict.fromkeys(
        ("name", "machine_type", "model", "serial", "firmware", "state"), HOSTILE
    )
    survey = FleetSurvey(
        profiles=(HOSTILE, "hmc-b"),
        readings=(_reading(HOSTILE, "SER0001", gaps=(HOSTILE,), **text),),
        failures=(ProfileFailure("hmc-b", HOSTILE),),
    )

    page = _page(survey)

    assert page.count("<script") == 1
    assert "alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;&quot;&#x27;&amp;" in page


def test_unknown_disk_renders_unknown_not_zero() -> None:
    unknown = DiskFigures(None, None, None, None, None, None)
    survey = FleetSurvey(("hmc-a",), (_reading("hmc-a", "SER0001", disk=unknown),), ())

    page = _page(survey)

    tile = re.search(r'<section class="tile"><h3>Disk \(GiB\)</h3>.*?</section>', page)
    assert tile is not None
    assert ">0<" not in tile.group(0)
    assert tile.group(0).count('<span class="unknown">unknown</span>') == 5
    row = re.search(r"<tr><td[^>]*>system-1</td>.*?</tr>", page, re.DOTALL)
    assert row is not None
    assert row.group(0).count('<td class="unknown" data-sort="">unknown</td>') == 1


def test_page_names_date_profiles_and_failures() -> None:
    page = _page(SURVEY)

    assert "Generated 2026-10-01 12:34 UTC" in page
    assert "Profiles surveyed: hmc-a, hmc-b, hmc-c" in page
    assert "<td>hmc-c</td><td>no answer within 300 s</td>" in page
    assert page.count("1 of 3 profiles failed (hmc-c)") == 1
    assert page.count('<table class="sortable">') == 1
    systems = page.split('<table class="sortable">')[1].split("</table>")[0]
    assert systems.count("<tr><td") == 2
    hmcs = page.split("<h2>Per HMC</h2>")[1].split("</table>")[0]
    assert "<table>" in hmcs
    assert hmcs.count("<tr><td") == 2


def test_every_profile_failed_renders_failures_only() -> None:
    survey = FleetSurvey(
        ("hmc-a", "hmc-b"),
        (),
        (ProfileFailure("hmc-a", "timed out"), ProfileFailure("hmc-b", "refused")),
    )

    page = _page(survey)

    assert "No systems were surveyed." in page
    assert 'class="tile"' not in page
    assert "<td>hmc-a</td><td>timed out</td>" in page
    assert "<td>hmc-b</td><td>refused</td>" in page
