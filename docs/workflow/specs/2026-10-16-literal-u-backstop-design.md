# Literal backslash-u identifier backstop (#1684)

## Problem

Raw `\u` immediately before a WWN, hyphen MAC or dotted MAC is treated as a
Unicode escape opener; scrubbing and leak checks both miss the identifier.
Authority: #1684, scoped by `q1684-e82330a7` and parent #1558.

## Scope

Add a bounded literal-u survivor scan, retaining `_WWN_16` and `_MAC_FORMS`.
Reuse the exact compiled MAC scrub matcher: its value group alone owns MAC
escape exemptions. Merge its ordered value spans with ordered survivor matches,
advancing each iterator once. Keep attributes and unmatched text in the scan.
Leave scrub behavior, LUN/zeroed-MAC exemptions and caller ownership unchanged.
Files: `scripts/live_capture_export.py`, its existing test module, this spec.
No interface, dependency, migration, compatibility path or numbered record.
Approved exclusions (campaign chat 2026-10-16): closing tags #1680, serial-key
consolidation #1682, live/release validation #1506, IPv4 escapes #1683.

### Failure model

- Actors/deployments: local operators export untrusted offline capture strings;
  CI uses synthetic/committed fixtures. No live HMC operations.
- Invariants/assets: scoped identifiers cannot silently enter published captures;
  genuine JSON stays valid; committed fixture exports remain unchanged.
- Accepted failures: ambiguous surviving raw values may refuse before writing
  (#1684); existing LUN and wholly zeroed-MAC exemptions remain accepted.
- Covered elsewhere: closing tags #1680; serial consolidation #1682;
  IPv4 escapes #1683; live/release checks #1506.
- Boundary inventory: captured strings enter tokenization; leak checks gate
  filesystem export. No boundary is added or widened.
- Actor/trust model: capture producers control strings; operators trust the
  exporter to refuse survivors before publishing output.
- Control per boundary: scrubbers preserve JSON. Unicode escapes plus zeroed
  MAC digits resemble live identifiers; exclude only actual MAC value spans,
  never opening attributes, unmatched tags, CDATA or child-separated text.
  Fixed lookbehind, bounded WWNs, guarded possessive MAC runs and ordered span
  merging preserve linear scans, including long slash/group/value runs.
- Threat exclusions: independently owned classes above and live-host threats;
  this offline change neither connects to hosts nor validates a release.

## Success

Scoped raw literal-u identifiers never export silently (#1684); genuine
`json.dumps` escapes retain current handling and valid JSON. All committed
fixtures remain byte-identical and acquire no new refusal.

## Validation

- focused-test: new raw survivor/refusal and span-boundary cases in
  `tests/scripts/test_live_capture_export.py`; red: empty leaks or successful
  export without the raw scan; green: `uv run --no-sync pytest
  tests/scripts/test_live_capture_export.py --no-cov -q`.
- focused-test: existing Unicode JSON, zeroed/LUN, long-run and fixture cases;
  compare all committed fixture re-exports and source bytes against the base.
- task-test-not-applicable: this prose has no executable consumer; review its
  charter mapping and size instead of snapshotting wording.
- Offline CLI: raw input refuses without output; genuine Unicode escapes before
  each complete identifier export valid JSON. Check direct and serialized scans.
