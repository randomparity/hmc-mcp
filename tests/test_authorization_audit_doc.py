"""`docs/authorization-audit.md` must agree with the audit vocabulary it mirrors.

#486. `audit.REASONS`, `audit.EVENTS`, `audit.State` and `tool_registry.EFFECTS` are
closed vocabularies, each derived from a `Literal` so "a checker and a test can
consult" it. The document restates all four — as a reason-code table, as one
`### event:` section apiece, as the `effect` row of the authorization field table, and
as the sentence enumerating the `connection.state` arms — and nothing read it, so the
two sides could drift apart silently. They had.

Every equality check below is a set comparison, so it fails on an orphan (documented,
not defined) and on a dangling entry (defined, not documented) alike. Each is paired
with a mutation test that feeds the extractor a deliberately drifted copy of the real
document and asserts it notices, so the check cannot pass by extracting nothing.

The counts stay out of the assertions on purpose: pinning one here would recreate the
stale literal this guard exists to remove, the same anti-pattern
`test_project_metadata.test_generated_policy_guidance_does_not_pin_a_stale_tool_count`
already guards in the README. Two patterns hold the document to the same rule — one for
counts of reason codes, one for counts of records or events, which is the class that had
actually gone stale ("The two records", written when there were two).

Restated *constants* are checked where the constant is exported and nothing else.

What this does not reach, so a green run is not read as more coverage than it is:

- an enumeration written out in prose rather than as a table or a heading — the guard
  compares vocabularies, so a sentence listing the members by name goes stale silently.
  The document avoids them where it can and states the rule instead;
- the `source` values on the TLS record. `client._verify_ssl_source` returns bare strings
  with no `Literal` behind them, so there is nothing to derive from. Issue #497 owns it.
"""

import re
from pathlib import Path
from typing import get_args

from hmc_mcp import audit, tool_registry


ROOT = Path(__file__).parents[1]
DOCUMENT = ROOT / "docs" / "authorization-audit.md"

TABLE_ROW_CODE = re.compile(r"^\|\s*`([^`]+)`\s*\|", re.MULTILINE)
EVENT_HEADING = re.compile(r'^### `event: "([^"]+)"`\s*$', re.MULTILINE)
EFFECT_ROW = re.compile(r"^\|\s*`effect`\s*\|([^|]*)\|", re.MULTILINE)
#: The comma-and-`or` run at the head of that cell, so a clarification appended after
#: it is prose rather than a fifth effect. Same reason as STATE_ARM below.
EFFECT_LIST = re.compile(r"`[a-z-]+`(?:,\s*(?:or\s+)?`[a-z-]+`)*")
STATE_SENTENCE = re.compile(r"`connection\.state` is ([^.]*)\.")
#: One arm of that sentence. Anchored on the `x` when … shape rather than taking every
#: backticked token, so an unrelated term added to the sentence is not read as a state.
STATE_ARM = re.compile(r"`([a-z-]+)`\s+when\b")
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

#: The same rule for the record and event vocabulary. Only plural counts, because
#: "one record per client construction" is a rate and stays true however many kinds
#: of record exist, while "the two records" and "both records" are the enumeration
#: that goes stale the next time ``audit.Event`` grows — as it did.
PLURAL_COUNT = (
    r"(?:\*{2})?(?:both|[2-9]|\d\d[\d,]*|two|three|four|five|six|seven|eight|nine"
    r"|ten|eleven|twelve)(?:\*{2})?"
)
FIXED_KIND_COUNT = re.compile(
    rf"\b{PLURAL_COUNT}\s+(?:[\w-]+\s+)?(?:records?|events?)\b", re.IGNORECASE
)

#: The same count in pronoun form, which names no noun for the pattern above to anchor
#: on. "Both are one physical line of ASCII JSON" is how the other half of this
#: document's drift was written, and it is why this is a separate pattern: it is safe
#: only in the lead paragraph that introduces the record kinds, where the whole subject
#: is how many there are.
PRONOUN_COUNT = re.compile(r"\b(?:both|neither|either)\b", re.IGNORECASE)


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
    """Scoped to the section the document's own intro calls the full set."""
    return frozenset(EVENT_HEADING.findall(_section(document, "## The records")))


def _documented_effects(document: str) -> frozenset[str]:
    """The `effect` row's value cell, whose members are backticked individually."""
    rows = EFFECT_ROW.findall(document)
    assert len(rows) == 1, f"expected one `effect` field row, found {len(rows)}"
    listing = EFFECT_LIST.search(rows[0])
    assert listing is not None, f"no effect list in cell: {rows[0]!r}"
    return frozenset(BACKTICKED.findall(listing.group(0)))


def _records_lead(document: str) -> str:
    """The paragraph introducing the record kinds, before the first one's section."""
    return _section(document, "## The records").split("\n### ", 1)[0]


def _documented_states(document: str) -> frozenset[str]:
    """The `connection.state` arms, which one sentence enumerates."""
    sentences = STATE_SENTENCE.findall(document)
    assert len(sentences) == 1, f"expected one state sentence, found {len(sentences)}"
    return frozenset(STATE_ARM.findall(sentences[0]))


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

    orphaned = document.replace(
        "## The records\n", '## The records\n\n### `event: "retired-event"`\n', 1
    )
    assert orphaned != document
    assert _documented_events(orphaned) - audit.EVENTS == {"retired-event"}


def test_an_event_section_outside_the_records_section_is_not_counted() -> None:
    """The intro calls that section the full set; the guard holds it to that."""
    document = _document()
    stray = document + '\n### `event: "elsewhere"`\n'

    assert stray != document
    assert "elsewhere" not in _documented_events(stray)


def test_event_names_are_read_from_headings_only() -> None:
    """Every event name is also a table cell and a JSON value; only headings count."""
    document = _document()
    headings = EVENT_HEADING.findall(document)

    assert len(headings) == len(set(headings)), f"duplicate headings: {headings}"

    in_json = document.replace('"event":"authorization"', '"event":"not-a-heading"', 1)
    assert in_json != document
    assert "not-a-heading" not in _documented_events(in_json)

    in_table = document.replace(
        '| `event` | `"authorization"` |', '| `event` | `"not-a-row"` |', 1
    )
    assert in_table != document
    assert "not-a-row" not in _documented_events(in_table)


def test_documented_effects_are_exactly_the_registry_vocabulary() -> None:
    assert _documented_effects(_document()) == tool_registry.EFFECTS


def test_effect_drift_is_caught_in_both_directions() -> None:
    document = _document()
    dangling = sorted(tool_registry.EFFECTS)[0]
    row = EFFECT_ROW.search(document)
    assert row is not None

    undocumented = document.replace(
        row.group(1), row.group(1).replace(f"`{dangling}`", dangling, 1), 1
    )
    assert undocumented != document
    assert tool_registry.EFFECTS - _documented_effects(undocumented) == {dangling}

    orphaned = document.replace(
        row.group(1), f"{row.group(1).rstrip()}, or `retired-effect` ", 1
    )
    assert orphaned != document
    assert _documented_effects(orphaned) - tool_registry.EFFECTS == {"retired-effect"}


def test_effects_survive_an_unrelated_backticked_term() -> None:
    """As for the state arms: a clarification in the cell is prose, not an effect."""
    document = _document()
    row = EFFECT_ROW.search(document)
    assert row is not None

    reworded = document.replace(
        row.group(1), f"{row.group(1).rstrip()} (see `effect` in the registry) ", 1
    )
    assert reworded != document
    assert _documented_effects(reworded) == tool_registry.EFFECTS


def test_documented_connection_states_are_exactly_the_audit_vocabulary() -> None:
    assert _documented_states(_document()) == frozenset(get_args(audit.State))


def test_connection_state_drift_is_caught_in_both_directions() -> None:
    document = _document()
    states = frozenset(get_args(audit.State))
    dangling = sorted(states)[0]
    sentence = STATE_SENTENCE.search(document)
    assert sentence is not None

    undocumented = document.replace(
        sentence.group(1), sentence.group(1).replace(f"`{dangling}`", dangling, 1), 1
    )
    assert undocumented != document
    assert states - _documented_states(undocumented) == {dangling}

    orphaned = document.replace(
        sentence.group(1),
        f"{sentence.group(1)}, and `retired-state` when nothing else applies",
        1,
    )
    assert orphaned != document
    assert _documented_states(orphaned) - states == {"retired-state"}


def test_state_arms_survive_an_unrelated_backticked_term() -> None:
    """The arms are read structurally, so editing the sentence's prose is safe."""
    document = _document()
    sentence = STATE_SENTENCE.search(document)
    assert sentence is not None

    reworded = document.replace(
        sentence.group(1), f"{sentence.group(1)}, such as `bytes`", 1
    )
    assert reworded != document
    assert _documented_states(reworded) == frozenset(get_args(audit.State))


def test_the_prose_pins_no_literal_vocabulary_count() -> None:
    document = _document()

    assert not FIXED_REASON_COUNT.search(document)
    assert not FIXED_KIND_COUNT.search(document)
    assert not PRONOUN_COUNT.search(_records_lead(document))


def test_a_pinned_reason_count_would_be_caught() -> None:
    document = _document()

    for pinned in ("seven codes", "**7** codes", "seven reason codes"):
        stale = document.replace("codes below", f"{pinned} below", 1)
        assert stale != document
        assert FIXED_REASON_COUNT.search(stale)

    assert not FIXED_REASON_COUNT.search("one of the codes below")
    assert not FIXED_REASON_COUNT.search("Treat null as not recorded for any code")


def test_a_pinned_record_count_would_be_caught() -> None:
    """The stale literal this document actually carried, and its near misses."""
    document = _document()

    for pinned in ("## The two records", "## The **4** records", "## Three events"):
        stale = document.replace("## The records", pinned, 1)
        assert stale != document
        assert FIXED_KIND_COUNT.search(stale)

    assert FIXED_KIND_COUNT.search("`DEBUG` and `INFO` keep both records")
    assert not FIXED_KIND_COUNT.search("One record per client construction")
    assert not FIXED_KIND_COUNT.search("Two other things produce no record, by design")


def test_a_pinned_count_in_pronoun_form_would_be_caught() -> None:
    """The other stale sentence named no noun: `Both are one physical line`."""
    document = _document()

    stale = document.replace(
        "Each is one physical line", "Both are one physical line", 1
    )
    assert stale != document
    assert not FIXED_KIND_COUNT.search(stale)
    assert PRONOUN_COUNT.search(_records_lead(stale))


def test_restated_constants_are_the_exported_ones() -> None:
    """Only where a source of truth exists to restate — see the module docstring."""
    document = _document()

    assert f"**{audit.MAX_VALUE_LENGTH} characters**" in document
    assert f"`{audit.DEFAULT_RENDERING}`" in document
    assert f"`{audit.UNRESOLVED_RENDERING}`" in document
    assert f'"environment:{audit.ATTRIBUTION_ENV}"' in document
