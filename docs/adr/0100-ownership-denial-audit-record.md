# ADR 0100: A separate audit record for a refused ADR 0011 ownership check

## Status

Accepted (2026-08-26)

## Context

The ADR 0011 ownership guard records the exception and not the rule it enforces. In
`src/hmc_mcp/operations/ownership.py`, `_audit_lpar_ownership_override` is called from the two
override paths — `authorize_lpar_mutation` and `_authorize_lpar_ownership_description` —
and emits one `ownership-override` record at `WARNING`. The two denial branches in
`_authorize_lpar_ownership_description` raise `PermissionError` with no audit call: a
malformed `[hmc-mcp …]` token, and a token naming another agent. `audit.Event` has no
denial member. (Names, not line numbers: this change edits that file, and ADR 0092
needed a repair commit after the #371 guard shifted seven of its citations.)

So the stream carries every approved bypass and no refused attempt, and an operator
reading it cannot tell "nobody tried to mutate a partition they do not own" from "many
attempts were refused".

The `authorization` record does not fill the gap. It is #218's dispatch-time access
policy, emitted from `dispatch_scope.authorize`, and ADR 0092's Context records that
#218's non-goals exclude the CLI and the supported Python API by design — for an
`hmc_mcp.api` consumer the ADR 0011 guard is the only authorization boundary that
applies at all. For those callers a refusal is observable solely as the raised
exception.

#371 made this worse in the deployment where it matters most. It put the guard on
`power_lpar` — the highest-frequency LPAR mutation — behind
`HMC_AUTHORIZE_POWER_OPERATIONS`, and `docs/environment-variables.md` tells the operator
to turn it on "when the HMC is shared with other agents or human operators". That is
exactly where repeated denials against one agent are the signal worth alerting on. #371
wrote the asymmetry into `docs/authorization-audit.md` rather than closing it, because
the fix belongs to the shared guard.

Two things about the surrounding contract bound what this change may do. The rest of the
grounds are in `Considered & rejected`, where the alternatives they sank are.

- `audit.py`'s stability rule, as written: a field may be added, never renamed,
  removed, or retyped; a *reason* code may be added, never repurposed; a consumer
  ignores what it does not know. It says nothing about `Event`, so §1 grounds the
  addition on the last clause and on the filter argument, not on this one.
- `tests/test_authorization_audit_doc.py` holds `docs/authorization-audit.md` to the
  module's vocabularies. Every member of `audit.EVENTS` needs one `### event:` section
  and at least one fenced sample. A vocabulary sampled in a record is held only to
  naming nothing undefined; the coverage direction exists solely where the document
  restates the vocabulary in a passage the guard reads — which is what §5's field rows
  are for.

## Decision

### 1. A new `ownership-denied` event, not a decision arm on `ownership-override`

`audit.Event` gains `ownership-denied`, built by a new
`audit.record_ownership_denied`. `ownership-override` is untouched — same name, same
fields, same level.

The two records answer different questions and carry different facts. A denial knows the
owner the LPAR claims and which of two rules refused it; an override knows neither,
because it never read the token. Folding them together means a `decision` field on an
event whose *name* asserts the opposite, and it silently changes what an existing
`event == "ownership-override"` filter counts: a query that today counts approved
bypasses would start counting refusals.

A new member is additive in the sense the stability rule's last clause covers — a
consumer ignores what it does not know — and it is why folding the denial onto the
existing event is the shape that breaks a filter and this one is not. `EVENTS` is
derived from the `Literal`, but the suite does not follow for free:
`test_events_matches_the_literal_and_every_emitter_uses_it` restates the set and
enumerates its emitters by hand, so it is edited here too.

### 2. Fields

```json
{"time":"2026-08-26T18:00:00+00:00","event":"ownership-denied","operation":"lpar-mutation","denial":"foreign-owner","system":"sys-a","lpar":"db-01","owner":"agent-3","host":"hmc-a.example","attribution":{"claim":"agent-7","source":"config:agent_id","verified":false}}
```

- **`operation`** — which ADR 0011 guard entry point refused, from a closed vocabulary
  `audit.OwnershipOperation`: `lpar-mutation` (`authorize_lpar_mutation`, every guarded
  mutation) or `lpar-decommission-snapshot`
  (`authorize_decommission_lpar_ownership_snapshot`). A two-member vocabulary, not the
  MCP tool or API function name — see `Considered & rejected`.
- **`denial`** — which rule refused, from a closed vocabulary `audit.OwnershipDenial`:
  `malformed-token` or `foreign-owner`. Named `denial` and not `reason`, which already
  names ADR 0040's access-policy vocabulary — see `Considered & rejected`.
- **`owner`** — the owner token parsed out of the LPAR description: the claimed owner on
  `foreign-owner`, and `null` on `malformed-token`, where nothing parsed. It is an
  HMC-supplied value, so it passes through `_value` like every other: truncated to
  `MAX_VALUE_LENGTH` and JSON-escaped by the shared renderer.
- **`system`**, **`lpar`**, **`host`** — as on the override record, and for the same
  reasons: names that repeat across a fleet, plus the `HMCConfig.host` that says which
  HMC they are names on. An unset host renders as the empty string it is.
- **`attribution`** — the acting agent, through the existing `_attribution` builder with
  `source: "config:agent_id"` and `verified: false`. The claim is
  `hmc.config.agent_id or "hmc-mcp"`, not the bare field: `HMCConfig.agent_id` defaults
  to `None`, and the fallback literal is what the guard actually compares against and
  what the override record already carries, so an unconfigured deployment's two
  ownership events name the same actor and can be joined. On `foreign-owner` the record
  therefore carries both halves of the comparison that failed; on `malformed-token` it
  carries the actor alone, because that branch refuses before the comparison is reached.

It carries no `policy`, `decision`, `reason`, `targets`, or `connection`, and not as
nulls — an ownership check on a token parsed from an LPAR description is not an
access-policy decision, and this path runs from the CLI and the Python API where no
policy connection exists. That is the same reasoning ADR 0040 applied to the override
record.

### 3. `WARNING`, matching the override

`_DENY_LEVEL`. A CLI or API process that never installed the sink has no handler on
`hmc_mcp.audit` and no propagation, so `logging.lastResort` is what puts the line on
stderr — and it drops anything below `WARNING`. A denial the operator cannot see is not
the control this ADR is for. It also means `hmc-mcp serve --audit-level WARNING`, the
setting that drops permits, keeps denials.

### 4. Emitted from the shared guard, once

`_authorize_lpar_ownership_description` emits immediately before each `raise
PermissionError`, and takes `operation` as a **required** keyword-only parameter from its
two callers. Required rather than defaulted: a third entry point that forgot the argument
would otherwise be recorded under an existing operation's name, and a stream that asserts
something false is worse than one that is silent.

So a new *call site* of an existing entry point inherits the record for free — all
fourteen do — while a new *entry point* is a type error until its author adds an
`audit.OwnershipOperation` member and the matching row in `docs/authorization-audit.md`.

`operations.lpar` still never resolves the reserved logger — it calls the `audit`
builder, exactly as the override path does, which is what
`test_operations.lpar_does_not_resolve_the_audit_logger` pins.

### 5. The document and its guard

`docs/authorization-audit.md` gains an `### event: "ownership-denied"` section with a
field table and a sample; its "Denials emit nothing" paragraph — which #371 wrote and
which cites this issue — is replaced by what the stream now carries and what it still
does not; and its lead section's claim that an unpolicied server produces
`ownership-override` records "and only those" stops being true, so that passage names
both. `README.md`'s one-line version of that claim names only the override record; it
stays true and becomes incomplete, and is completed in the same change.

Both new vocabularies join the document's drift guard on the same terms as their
siblings: `denial` as a field row held to `audit.OWNERSHIP_DENIALS` in both directions,
and `operation` to `audit.OWNERSHIP_OPERATIONS`. A vocabulary that reaches a record
without joining that guard is the drift #486 exists to stop.

## Consequences

- An operator alerting on the ADR 0011 guard can count refusals per agent, per
  partition, and per HMC, on every transport — including the two the dispatch-boundary
  policy does not reach.
- A raw denial count is not a count of hostile attempts, and this is the first thing to
  know before alerting on it. `docs/environment-variables.md` prescribes
  retry-after-refusal as the sanctioned override procedure, so an override taken after a
  refusal is now *preceded* by a denial record carrying the same `system`, `lpar`, and
  `attribution.claim` as the `ownership-override` record seconds later. The two carry no
  correlation identifier, so pairing them means matching those three fields within a time
  window — and the overrides that were never refused (the bullet below, and any caller
  who passes `ownership_override` on the first attempt) will pair against an unrelated
  earlier denial if one is in the window. A refused `--dry-run` decommission preflight —
  the sequence `README.md` prescribes — emits the same record as a refused destructive
  one, because `_inventory` authorizes ahead of the dry-run return. A field to make
  either distinction explicit would cost more than it removes, and is not added here;
  this is the caveat `docs/authorization-audit.md` already carries for the override
  record's two sources.
- The two ownership events are now asymmetric: the denial names its `operation` and the
  override does not. Closing it is an *addition* rather than the rejected `decision` arm,
  so no `event ==` filter would notice — but reshaping that record is this issue's stated
  exclusion, and the denial stream is complete without it.
- A denied caller can drive these records at attempt rate. Under `hmc-mcp serve` they
  land on the bounded sink ADR 0043 defines, which drops and says so with a
  `records-dropped` count; on the CLI and Python API paths nothing calls
  `install_audit_sink`, so the record goes synchronously to stderr through
  `logging.lastResort` with no bound and no drop count — exactly as the existing
  `ownership-override` record already does there. Reaching a denial costs a
  `get_lpar_description` round trip to the HMC first, which is the practical bound.
- Silence in this stream still is not proof of no refusal, and for one specific reason
  worth writing down: with `HMC_AUTHORIZE_POWER_OPERATIONS` off — the default — the
  power path never runs the guard, so no denial is possible there and none is recorded.
- `provision_lpar`'s activation leg passes the override unconditionally (ADR 0092
  Consequences), so it produces an override record and can never produce a denial one.
- A `malformed-token` record identifies the partition but not the malformation, so
  triage means reading the description off the HMC out of band, and two alerts on one
  permanently-broken token are indistinguishable from an ongoing incident. Accepted
  rather than closed: the description is unbounded operator-authored text that `_value`
  would cut at 128 characters — often before the malformation — and it can carry an
  ADR 0064 caller token beside the ownership one, so a field for it discloses more than
  it triages. `denial` plus `lpar` locates the partition to read.
- No change to `hmc_mcp.api.__all__` and no movement of the frozen public signature
  digest: the new builder lives in `audit`, which the facade does not export, and no
  exported signature changes.

## Considered & rejected

- **A `decision` field on the existing `ownership-override` record.** verified:
  `test_authorize_lpar_mutation_override_is_audited` asserts that record equals an exact
  dict and
  `docs/authorization-audit.md` enumerates its fields, so the field lands in a shape two
  places pin; and an `event == "ownership-override"` filter, which is how the document
  tells an operator to find approved bypasses, would begin matching refusals.
  judgment: an event named for the bypass is the wrong carrier for the refusal.
- **Reusing the `reason` key for the denial vocabulary.** verified:
  `tests/test_authorization_audit_doc.py`'s `SAMPLE_VOCABULARIES` holds every sample
  `reason`, at any depth, to `audit.REASONS`, so a sample carrying
  `"reason": "foreign-owner"` reddens unless that guard is widened into a union, which
  stops it distinguishing an access-policy reason from an ownership one. judgment: one
  key cannot name two boundaries' vocabularies and still tell a reader which it is
  reading.
- **Adding the two ownership codes to `audit.Reason` itself.** verified: `Reason` is
  imported by `target_scope` and the `## Reason codes` table is documented as the
  dispatch-boundary decision vocabulary with an `allow`/`deny` column; the two ownership
  codes belong to neither. judgment: one vocabulary serving two boundaries is a
  vocabulary that describes neither.
- **Threading the MCP tool or API function name through as `operation`.** verified: its
  strongest form breaks no caller — an optional keyword-only parameter on
  `authorize_lpar_mutation` (`src/hmc_mcp/api.py:251`, eleven call sites) and
  `authorize_decommission_lpar_ownership_snapshot` (`:250`, three) — but it moves the
  frozen public signature digest at `tests/unit/test_public_api.py:1658`, which is
  computed over every `api.__all__` signature, across two exports and fourteen call
  sites. judgment: per-tool granularity is a future issue's under this issue's charter.
  Deliberately *not* argued on the grounds that the tool name is recoverable from the
  `authorization` record: that record is a permit, so §3's own `--audit-level WARNING`
  recommendation discards it, and on the CLI and Python API paths it never existed.
- **No `denial` field, distinguishing the branches by `owner: null` alone.** verified:
  `null` is "nothing to render" everywhere else in this stream — `_value` returns it for
  a non-`str`, and `_connection` renders `selector: null` in both of its non-present
  states — so an alert on the malformed-token case would rest on
  an absence rather than on a value.
- **Emitting from each of the fourteen call sites instead of the shared guard.**
  verified: the fourteen enumerated in the bullet above. judgment: fourteen edit sites
  for one rule, and the next guarded operation ships without a record unless its author
  remembers.
- **Documenting the gap and doing nothing.** verified: it is already documented, by
  `docs/authorization-audit.md`'s "Denials emit nothing" paragraph, written by #371.
  judgment: what documenting leaves standing is the Context's own finding — a CLI or
  `hmc_mcp.api` caller has no other authorization boundary, so a refusal there is
  observable to nobody but the caller who was refused.
