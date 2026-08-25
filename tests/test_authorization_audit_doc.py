"""`docs/authorization-audit.md` must agree with the audit vocabulary it mirrors.

#486. `audit.REASONS`, `audit.EVENTS` and `tool_registry.EFFECTS` are the closed
vocabularies, derived from their `Literal`s so "a checker and a test can consult"
them. The document restates all three — as a reason-code table, as one `### event:`
section apiece, and as the `effect` row of the authorization field table — and
nothing read it, so the two could drift apart silently.

Every equality check below is a set comparison, so it fails on an orphan (documented,
not defined) and on a dangling entry (defined, not documented) alike. Each is paired
with a mutation test that feeds the extractor a deliberately drifted copy of the real
document and asserts it notices, so the check cannot pass by extracting nothing.

The counts stay out of the assertions on purpose: pinning one here would recreate the
stale literal this guard exists to remove, the same anti-pattern
`test_project_metadata.test_generated_policy_guidance_does_not_pin_a_stale_tool_count`
already guards in the README.
"""

import re
from pathlib import Path

from hmc_mcp import audit, tool_registry


ROOT = Path(__file__).parents[1]
DOCUMENT = ROOT / "docs" / "authorization-audit.md"

TABLE_ROW_CODE = re.compile(r"^\|\s*`([^`]+)`\s*\|", re.MULTILINE)
EVENT_HEADING = re.compile(r'^### `event: "([^"]+)"`\s*$', re.MULTILINE)
EFFECT_ROW = re.compile(r"^\|\s*`effect`\s*\|([^|]*)\|", re.MULTILINE)
BACKTICKED = re.compile(r"`([^`]+)`")

#: A count — spelled or numeric, bold or plain — pinned to the reason-code
#: vocabulary. One filler word is allowed between ("seven reason codes"); two are
#: not, so the countless phrasing "one of the codes below" reads clean.
COUNT_WORD = (
    r"(?:\*{2})?(?:\d[\d,]*|one|two|three|four|five|six|seven|eight|nine|ten"
    r"|eleven|twelve)(?:\*{2})?"
)
FIXED_REASON_COUNT = re.compile(
    rf"\b{COUNT_WORD}\s+(?:[\w-]+\s+)?codes?\b", re.IGNORECASE
)


def _document() -> str:
    return DOCUMENT.read_text()


def _section(document: str, heading: str) -> str:
    """The body under a `##` heading, up to the next one."""
    marker = f"\n{heading}\n"
    assert marker in document, f"missing section: {heading}"
    return document.split(marker, 1)[1].split("\n## ", 1)[0]


def _documented_reasons(document: str) -> frozenset[str]:
    """The first column of the reason-code table, minus its separator row."""
    section = _section(document, "## Reason codes")
    return frozenset(TABLE_ROW_CODE.findall(section))


def _documented_events(document: str) -> frozenset[str]:
    return frozenset(EVENT_HEADING.findall(document))


def _documented_effects(document: str) -> frozenset[str]:
    """The `effect` row's value cell, whose members are backticked individually."""
    rows = EFFECT_ROW.findall(document)
    assert len(rows) == 1, f"expected one `effect` field row, found {len(rows)}"
    return frozenset(BACKTICKED.findall(rows[0]))


def test_documented_reason_codes_are_exactly_the_audit_vocabulary() -> None:
    assert _documented_reasons(_document()) == audit.REASONS


def test_reason_code_drift_is_caught_in_both_directions() -> None:
    document = _document()
    dangling = sorted(audit.REASONS)[0]

    undocumented = document.replace(f"| `{dangling}` |", "| removed |", 1)
    assert undocumented != document
    assert audit.REASONS - _documented_reasons(undocumented) == {dangling}

    orphaned = document.replace(
        "## Reason codes\n",
        "## Reason codes\n\n| `retired-code` | deny | no longer defined |\n",
        1,
    )
    assert orphaned != document
    assert _documented_reasons(orphaned) - audit.REASONS == {"retired-code"}


def test_reason_code_table_is_scoped_to_its_own_section() -> None:
    """Field-table rows are backticked too; the extractor must not collect them."""
    document = _document()

    assert "| `time` |" in document
    assert "time" not in _documented_reasons(document)
    assert "code" not in _documented_reasons(document)


def test_documented_event_sections_are_exactly_the_audit_vocabulary() -> None:
    assert _documented_events(_document()) == audit.EVENTS


def test_event_drift_is_caught_in_both_directions() -> None:
    document = _document()
    dangling = sorted(audit.EVENTS)[0]

    undocumented = document.replace(f'### `event: "{dangling}"`', "### removed", 1)
    assert undocumented != document
    assert audit.EVENTS - _documented_events(undocumented) == {dangling}

    orphaned = document + '\n### `event: "retired-event"`\n'
    assert _documented_events(orphaned) - audit.EVENTS == {"retired-event"}


def test_documented_effects_are_exactly_the_registry_vocabulary() -> None:
    assert _documented_effects(_document()) == tool_registry.EFFECTS


def test_effect_drift_is_caught_in_both_directions() -> None:
    document = _document()
    dangling = sorted(tool_registry.EFFECTS)[0]
    row = EFFECT_ROW.search(document)
    assert row is not None

    undocumented = document.replace(row.group(1), row.group(1).replace(
        f"`{dangling}`", dangling, 1
    ), 1)
    assert undocumented != document
    assert tool_registry.EFFECTS - _documented_effects(undocumented) == {dangling}

    orphaned = document.replace(
        row.group(1), f"{row.group(1).rstrip()}, or `retired-effect` ", 1
    )
    assert orphaned != document
    assert _documented_effects(orphaned) - tool_registry.EFFECTS == {"retired-effect"}


def test_reason_code_prose_pins_no_literal_count() -> None:
    assert not FIXED_REASON_COUNT.search(_document())


def test_a_pinned_reason_count_would_be_caught() -> None:
    document = _document()

    for pinned in ("seven codes", "**7** codes", "seven reason codes"):
        stale = document.replace("codes below", f"{pinned} below", 1)
        assert stale != document
        assert FIXED_REASON_COUNT.search(stale)

    assert not FIXED_REASON_COUNT.search("one of the codes below")
    assert not FIXED_REASON_COUNT.search("Treat null as not recorded for any code")
