"""The audit documents must agree with the vocabularies they mirror.

#486. `audit.REASONS`, `audit.EVENTS`, `audit.State`, `tool_registry.EFFECTS` and
`client.VERIFY_SSL_SOURCES` are closed vocabularies, each derived from a `Literal` so
"a checker and a test can consult" it. `docs/authorization-audit.md` restates all five —
as a reason-code table, as one `### event:` section apiece, as the `effect` row of the
authorization field table, as the sentence enumerating the `connection.state` arms, and
as the clause naming the TLS record's `source` values — and nothing read it, so the two
sides could drift apart silently. They had.

`docs/environment-variables.md`'s `HMC_VERIFY_SSL` note describes the same TLS record, so
its restatement of the `source` values is held to the same set and neither document can
drift alone (#497). Two further restatements are in code and are *not* reached; they are
in the ledger below.

Every equality check below is a set comparison, so it fails on an orphan (documented,
not defined) and on a dangling entry (defined, not documented) alike — with one bounded
exception in the ledger, for a member appended to a run-based list with "and". Each is
paired with a mutation test that feeds the extractor a deliberately drifted copy of the
real document and asserts it notices, so the check cannot pass by extracting nothing.

The counts stay out of the assertions on purpose: pinning one here would recreate the
stale literal this guard exists to remove, the same anti-pattern
`test_project_metadata.test_generated_policy_guidance_does_not_pin_a_stale_tool_count`
already guards in the README. Two patterns hold the document to the same rule — one for
counts of reason codes, one for counts of records or events, which is the class that had
actually gone stale ("The two records", written when there were two).

Restated *constants* are checked where the constant is exported and nothing else.

What this does not reach, so a green run is not read as more coverage than it is:

- an enumeration written out in prose rather than as a table or a heading, unless it is
  one of the two `source` clauses below — the guard compares vocabularies, so a sentence
  listing the members by name goes stale silently. The documents avoid them where they
  can and state the rule instead;
- a pinned count of the `source` vocabulary, in either document. The count patterns below
  anchor on a noun, and "source" is ordinary English this document already uses for
  something else — "the two sources" of an ownership-override record names two code paths,
  not two `source` values — so an alternation for it would fail on prose that is correct.
  Both documents are held to the membership of that vocabulary and not to its size;
- a pinned count anywhere in `docs/environment-variables.md`. The count patterns run over
  the audit document, whose subject is these vocabularies; turning them loose on a
  document about twenty settings would read its ordinary prose ("one SSH login plus two
  REST GETs") the same wrong way. Only the `HMC_VERIFY_SSL` note's membership is guarded;
- a member appended to a run-based list with "and" rather than a comma or "or".
  `EFFECT_LIST` and `SOURCE_LIST` end the run at the first separator they do not
  recognise, which is what lets the negative controls treat a trailing clarification as
  prose — and the same tradeoff means `..., or `field-default`, and `config-file`` reads as
  three values plus prose. The dangling direction is unaffected; only the orphan half has
  the hole, and only for those two extractors;
- the `source` restatements that live in code rather than in a document:
  `audit.record_tls_verification_disabled`'s docstring, which is where a consumer reads
  the field (`audit` imports nothing from `hmc_mcp`, so its parameter is a plain `str`),
  and `tests/unit/test_audit.py`'s TLS record test. Both spell the three values out and
  neither is reachable from here. Issue #504 owns replacing them with a pointer;
- the literal values inside the documents' JSON sample records. `"event"` and `"source"`
  are written unbackticked there, so no extractor reads them: rename a vocabulary member
  and every list restatement reddens while the samples — the one place a consumer copies a
  log query from — keep the old value behind a green run. Document-wide and pre-existing,
  not specific to the TLS record; guarding it means a sample extractor across every event
  section, which is more machinery than the residual is worth here. Issue #506 owns it;
- the editing constraint this guard puts on the two documents. Both TLS passages must keep
  those eight words in that order — the environment-variable note writes no `source`
  identifier to anchor on, so ordinary prose is the only anchor available — and that note
  must stay one `- **` bullet. Line breaks within the clause do not matter: `_tls_passage`
  collapses whitespace, so re-wrapping either paragraph is safe. Every violation fails
  loud, but it fails naming a regex, and an editor of a settings document has no reason to
  open a test named for a different one. `docs/authorization-audit.md` carries a marker
  saying so; #504 covers the same marker for the other document.

The environment-variable restatement is kept rather than replaced by a cross-reference,
which would have deleted half this machinery. It is where an operator deciding whether to
leave verification off actually reads, and #497's fourth criterion asked for it to be
covered. Guarding it is the price of leaving it there, and that price is this ledger.
"""

import re
from pathlib import Path
from typing import get_args

import pytest

from hmc_mcp import audit, client, tool_registry


ROOT = Path(__file__).parents[1]
DOCUMENT = ROOT / "docs" / "authorization-audit.md"
ENVIRONMENT_DOCUMENT = ROOT / "docs" / "environment-variables.md"

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

#: `docs/authorization-audit.md`'s editor marker is one of these and quotes the anchor
#: phrase, so comments come out of a passage before the clause is read.
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

#: The passage describing the TLS record in each document that restates its `source`
#: values: the event's own section in the audit document, the `HMC_VERIFY_SSL` note in the
#: environment-variable one. Anchored on the record's own name, as every sibling extractor
#: anchors on something structurally unique to its vocabulary — the clause below is
#: ordinary English about settings, and `docs/environment-variables.md` documents nothing
#: but settings, so scanning that document whole would let an unrelated note collide.
TLS_PASSAGE = {
    "authorization-audit.md": re.compile(
        r'^### `event: "tls-verification-disabled"`$.*?(?=^#{2,6} )',
        re.MULTILINE | re.DOTALL,
    ),
    "environment-variables.md": re.compile(
        r"^- \*\*TLS verification\*\* \(`HMC_VERIFY_SSL`\):.*?(?=^- \*\*|\Z)",
        re.MULTILINE | re.DOTALL,
    ),
}
#: The clause both passages introduce the `source` values with. Anchored on the wording
#: rather than the punctuation, which differs between them: em dashes in the audit
#: document, parentheses in the environment-variable note. Single spaces are enough
#: because `_tls_passage` has already collapsed the passage's line breaks; both documents
#: are hard-wrapped prose, so where a break lands inside these eight words is an accident
#: of the surrounding paragraph rather than anything an editor chose.
SOURCE_CLAUSE = re.compile(r"where the (?:effective )?setting came from\b")
#: One `source` value: a lowercase hyphenated name, optionally suffixed with the
#: environment variable it names, as `environment:HMC_VERIFY_SSL` is.
SOURCE_VALUE = r"[a-z][a-z-]*(?::[A-Z][A-Z0-9_]*)?"
#: The comma-and-`or` run of them, as EFFECT_LIST is for effects — so a clarification
#: appended after the run is prose rather than a fourth source.
SOURCE_LIST = re.compile(rf"`{SOURCE_VALUE}`(?:,\s*(?:or\s+)?`{SOURCE_VALUE}`)*")

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


def _tls_passage(document: str, name: str) -> str:
    """The passage of *document* describing the TLS record, where the `source` clause lives.

    HTML comments are dropped first: the editor marker quotes the anchor phrase, so a
    marker moved inside the section it describes — which is where its own wording points —
    would otherwise be read as a second clause. Then whitespace is collapsed, so no
    pattern below can depend on where a hard wrap happens to fall.
    """
    match = TLS_PASSAGE[name].search(document)
    assert match is not None, f"no TLS record passage in {name}"
    return re.sub(r"\s+", " ", HTML_COMMENT.sub("", match.group(0)))


def _source_sentence(passage: str) -> str:
    """The sentence naming the TLS record's `source` values.

    Bounded to that one sentence, so a passage carrying the clause but no list fails
    rather than reaching forward to unrelated backticks. No `source` value contains a
    period, which is what makes the sentence end a safe bound.
    """
    clauses = SOURCE_CLAUSE.findall(passage)
    assert len(clauses) == 1, f"expected one `source` clause, found {len(clauses)}"
    clause = SOURCE_CLAUSE.search(passage)
    assert clause is not None
    return passage[clause.end() :].split(".", 1)[0]


def _source_list(passage: str) -> str:
    """The comma-and-`or` run within that sentence."""
    sentence = _source_sentence(passage)
    listing = SOURCE_LIST.search(sentence)
    assert listing is not None, f"no source list in clause: {sentence!r}"
    return listing.group(0)


def _documented_sources(passage: str) -> frozenset[str]:
    return frozenset(BACKTICKED.findall(_source_list(passage)))


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


#: Both restatements of the TLS record's `source` field. The environment-variable note
#: describes the same record, so it is held to the same set rather than left to drift.
SOURCE_DOCUMENTS = (DOCUMENT, ENVIRONMENT_DOCUMENT)


@pytest.mark.parametrize("path", SOURCE_DOCUMENTS, ids=lambda path: path.name)
def test_documented_tls_sources_are_exactly_the_client_vocabulary(path: Path) -> None:
    assert (
        _documented_sources(_tls_passage(path.read_text(), path.name))
        == client.VERIFY_SSL_SOURCES
    )


@pytest.mark.parametrize("path", SOURCE_DOCUMENTS, ids=lambda path: path.name)
def test_tls_source_drift_is_caught_in_both_directions(path: Path) -> None:
    passage = _tls_passage(path.read_text(), path.name)
    listing = _source_list(passage)
    #: The first value listed, which is never the last one — so dropping it and the
    #: separator that follows leaves a run the extractor still reads.
    dangling = BACKTICKED.findall(listing)[0]

    undocumented = re.sub(rf"`{re.escape(dangling)}`,\s*", "", passage, count=1)
    assert undocumented != passage
    assert client.VERIFY_SSL_SOURCES - _documented_sources(undocumented) == {dangling}

    orphaned = passage.replace(listing, f"{listing}, or `retired-source`", 1)
    assert orphaned != passage
    expected = {"retired-source"}
    assert _documented_sources(orphaned) - client.VERIFY_SSL_SOURCES == expected


@pytest.mark.parametrize("path", SOURCE_DOCUMENTS, ids=lambda path: path.name)
def test_tls_sources_survive_an_unrelated_backticked_term(path: Path) -> None:
    """As for the effects and the state arms: a term after the run is prose, not a source.

    The added term is itself list-shaped, so what excludes it is the run's own
    comma-and-`or` anchoring rather than the value pattern.
    """
    passage = _tls_passage(path.read_text(), path.name)
    sentence = _source_sentence(passage)

    reworded = passage.replace(
        sentence, f"{sentence}, and `verify-ssl` is the field it names", 1
    )
    assert reworded != passage
    assert _documented_sources(reworded) == client.VERIFY_SSL_SOURCES


def test_the_editor_marker_quotes_the_anchor_it_names() -> None:
    """The marker is the only warning an editor of the document gets, so it is derived.

    Deleting it, or letting its quoted phrase drift from `SOURCE_CLAUSE`, reddens here
    rather than leaving the guard's one piece of documentation silently wrong.
    """
    quoted = [
        phrase
        for marker in HTML_COMMENT.findall(_document())
        for phrase in re.findall(r'"([^"]+)"', marker)
    ]

    assert quoted, "the TLS section's editor marker is missing"
    assert any(SOURCE_CLAUSE.fullmatch(phrase) for phrase in quoted), quoted


def test_a_marker_moved_inside_the_section_is_not_read_as_a_clause() -> None:
    """Its own wording says "the values below", so inside the section is where it lands."""
    document = _document()
    heading = '### `event: "tls-verification-disabled"`'
    marker = HTML_COMMENT.search(document)
    assert marker is not None

    moved = document.replace(
        f"{marker.group(0)}\n{heading}", f"{heading}\n{marker.group(0)}", 1
    )
    assert moved != document
    assert _documented_sources(_tls_passage(moved, DOCUMENT.name)) == (
        client.VERIFY_SSL_SOURCES
    )


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
