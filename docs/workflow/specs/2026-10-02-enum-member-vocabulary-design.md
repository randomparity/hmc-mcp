# Keep an enum member the vocabulary would record as `<text>` (#1292)

## Problem

`live_capture_export.py vocabulary` shapes every observed REST value with `observed()`,
which keeps a value verbatim only when `LITERAL` (`[A-Za-z][A-Za-z _/+-]{0,40}`) fully
matches it. A POWER10 9080-HEX answered `ManagedSystem` `State` with
`pending authentication - password updates required` (51 characters), which the HMC's own
`SystemState.Enum` and `FrameState.Enum` list. The committed `v11r2-p10-9080-hex.json`
records it as `<text>`, losing the value and implying free text for a closed element.

The issue proposes resolving the element's bound enum before shaping. That alone does not
fix `State`:

- The captured schemas bind no `State` element (`enums-v11r2-p10-9080-hex.json` has no
  `State` key in `elements`).
- `State` is shared by objects with different enums. Its observed literals (`operating`,
  `recovery`, `no connection`, `Active`, `Inactive`, `Disconnected`, …) are held by no
  single enum, so `_bound_enum` infers no binding, and with the raw values added it still
  would not: several `*State.Enum` types suffix-match and none holds them all.

## Decision

A REST value that `observed()` would record as `<text>` is kept verbatim when it is an
exact member of an enum that applies to its element, unless the element is name-bearing.
The applicable enums are:

1. the enum the captured schema binds the element to (`enums["elements"]`), when there is
   one — that enum alone; otherwise
2. every enum type whose name, less `.Enum`, ends with the element name or is a suffix of
   it — the same naming relation `_bound_enum` already uses for its looser match.

Membership is exact string equality on the stripped value, the same text `shape()`
judges; there is no case folding. A value is kept only when some applicable enum spells it
exactly. For `State` the applicable enums are the 28 `*State.Enum` types, which spell the
value both as `pending authentication - password updates required` (`SystemState.Enum`,
`FrameState.Enum`) and as `Pending Authentication - Password Updates Required`
(`ManagedFrameState.Enum`), so both spellings are kept; an all-capitals spelling, which no
enum lists, records `<text>`.

The rule applies wherever a REST value is shaped: the corpus loop and `_fold_derived`.

### Why this direction

- **(a) A raw-value binding pre-pass** (infer bindings from unshaped values, then shape
  through the bound enum) fixes nothing for `State`: the inference needs one enum that
  holds every literal, and no enum does. It also widens the binding rules, which
  `check_live_vocabulary` consumes, beyond this issue.
- **(b) Membership in any name-related enum (chosen)** keeps exactly the values the HMC
  publishes as constants for an element of that name. It does not bind the element, so
  `element_enums` and the guard's enum checks are unchanged in kind.

### What it keeps

Every kept value is a constant a name-related enum publishes. Specifically:

- It only replaces `<text>`. Values `shape()` already classes (`<int>`, `<float>`,
  `<empty>`, `<uuid-*>`, `true`/`false`) and literals already kept are untouched, so a
  numeric enum such as `MultiCoreScalingValue.Enum` still records `<int>`.
- A kept value equals a schema-published enum constant; it cannot carry an identifier
  except by coinciding with one. The one place a coincidence matters — a name-bearing
  field holding a partition name that happens to equal a constant — is excluded: those
  fields keep their shape class.
- A tokenized value (`sys-R1`, `<REDACTED-…>`) is never an enum member.
- The output still passes `_write_scanned`'s fail-closed leak scan.

A short element name (`State`, `Type`, `Mode`) suffix-matches many enums, so the member set
is broad. The cost is that a value which is a constant of a *different* object's enum of
the same element name is kept rather than recorded as `<text>`. That is still a value the
HMC answered for that element name, which is what `rest.values` records.

## Behaviour

`rest.values`, `rest.element_enums` and the rest of the output keep their JSON shape; only
the set of strings recorded can change. The change reaches every element that records
`<text>` and has a related enum, not only `State` — in the 9080-HEX vocabulary that
includes `NetworkInterface`, `CurrentConnectionSpeed`, `ConfiguredConnectionSpeed` and
`Speed`. `element_enums` inference reads the kept literals, so a newly kept member can add
an inferred binding (`NetworkInterface` to `NetworkInterface.Enum`, for example), which
`check_live_vocabulary` then enforces for that element. That is the guard working from
better evidence, not a change to its rules; the re-derivation step reviews every changed
`rest.values` and `rest.element_enums` key and runs `just live-vocabulary`. The `State`
binding stays absent.

Unchanged: `LITERAL` and its 41-character cap; tokenizing; `NAME_BEARING`; CLI values (no
enum applies to a CLI field name); `derive_enums`; `check_live_vocabulary.py`.

## Re-derivation

`v11r2-p10-9080-hex.json` is re-derived on the live-test host from the same tokenized
2026-10-02 combined corpus and arguments that produced the committed file. At `origin/main`
that invocation reproduces the committed file byte for byte, so the diff after this change
is attributable to the change alone. `_fold_derived` never re-shapes a value starting with
`<`, so only a re-derivation from the corpus removes the stale `<text>`. Other pairs'
vocabularies are not re-derived here.

## Failure model

- A member set computed from the wrong enums (binding ignored, or suffix relation
  inverted) keeps a non-member or drops a member — caught by the member/non-member tests.
- Applying the rule before the name-bearing check would keep an identifier — caught by a
  name-bearing test with an enum-member value.
- Applying it to already-classed values would change `<int>`/`<empty>` — caught by a
  numeric-member test.
- The fold path left on `observed()` would record `<text>` for a folded member — caught by
  a fold test.

## Tests (`tests/scripts/test_live_capture_export.py`)

- A 51-character member of a suffix-matched enum for an unbound element is kept; a
  non-member of the same length and an all-capitals spelling of the member record `<text>`.
- With a schema binding, a member of only a different suffix-matched enum records `<text>`.
- A name-bearing element (`HostState`, with a `HostState.Enum` holding the value) records
  `<text>`, so the test fails if the name-bearing check is removed.
- A numeric member records `<int>`.
- A folded member is kept, with enums supplied to `build_vocabulary`.
- Against the committed `enums-v11r2-p10-9080-hex.json`, `State` keeps both spellings of
  the pending-authentication value.

Each test is checked to fail when the rule it covers is removed.
