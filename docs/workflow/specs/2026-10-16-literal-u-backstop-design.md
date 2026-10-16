# Literal backslash-u identifier guard (#1684)

## Problem

Raw `\u` immediately before a WWN, hyphen MAC or dotted MAC is mistaken for
an escape; scrubbers and leak checks miss the identifier. Authority: #1684,
scoped by `q1684-e82330a7`, parent #1558 and its approved report/refusal option.

## Scope

Guard survivors at the end of each existing `Tokenizer.tokenize` call, before
separate REST/SSH fields or tool JSON are combined or decoded. Reuse `_WWN_16`,
`_MAC_FORMS` and the exact MAC scrub matcher; exempt only its value-group spans
in that same representation. Merge ordered spans/matches once; raise existing
`LeakError` on a survivor. Keep legacy flat leak checks unchanged.
The existing derived writer checks actual supplied string leaves and keys,
without MAC exemptions: firmware/source metadata was not scrubbed by that call.
Files: `scripts/live_capture_export.py`, its test module, this unnumbered spec.
No new argument, dependency, data model, migration or compatibility path.
Approved exclusions (campaign chat 2026-10-16): closing tags #1680, serial-key
consolidation #1682, live/release validation #1506, IPv4 escapes #1683.

### Failure model

- Actors/deployments: local operators export untrusted offline capture strings
  and supply derived metadata; CI uses synthetic/committed fixtures.
- Invariants/assets: scoped identifiers cannot silently enter published outputs;
  genuine JSON stays valid; committed fixture exports remain unchanged.
- Accepted failures: ambiguous surviving raw values may refuse before writing
  (#1684); existing LUN/wholly zeroed-MAC exemptions remain accepted.
- Covered elsewhere: closing tags #1680; serial consolidation #1682;
  IPv4 escapes #1683; live/release checks #1506.
- Boundary inventory: capture strings enter scrubbing; survivors are checked
  before aggregation/dequoting. Derived data is checked before filesystem write.
- Actor/trust model: producers control strings/metadata; refuse survivors before publication.
- Control per boundary: Unicode escapes plus zeroed MAC digits resemble live
  identifiers. Only actual scrub-call value spans retain those exemptions.
  An unclosed stdout opener cannot own stderr/another record; a decoded Unicode
  opener cannot claim ownership absent from the tool's encoded scrub input.
  Attributes, unmatched tags, CDATA and child-separated text remain checked.
  Fixed lookbehind, guarded runs, ordered spans and iterative derived traversal
  stay linear; use existing serialization limits, with no new nesting policy.
- Threat exclusions: independently owned classes above; live hosts are unreachable offline.

## Success

Scoped raw literal-u identifiers never export silently (#1684); genuine
`json.dumps` escapes retain current handling and valid JSON. All committed
fixtures remain byte-identical and acquire no new refusal.

## Validation

- focused-test: raw refusal, actual scrub spans, cross-field/record/nested JSON,
  encoded-Unicode opener, derived metadata/keys, and valid Unicode cases in
  `tests/scripts/test_live_capture_export.py`; red: export succeeds; green:
  `uv run --no-sync pytest tests/scripts/test_live_capture_export.py --no-cov -q`.
- focused-test: existing JSON, zeroed/LUN, long-run and fixture cases; compare
  all 119 source bytes, capture re-exports and derived writes against the base.
- task-test-not-applicable: prose has no executable consumer; review its mapping
  and size. Offline CLI: raw refusal leaves no output; genuine JSON stays valid.
