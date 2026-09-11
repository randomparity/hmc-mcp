# Durable declared-limitation gaps (#769)

## Problem and scope

Declared firmware/license/endpoint failures currently produce SKIP without a durable
gap. Implement the approved one-PR scope in WORK:SCOPE q769-a18c4e52, using ADR 0132.
Declarations are in connectivity, lpar, network, metrics, provisioning, users and
vmedia. PR #760 restored connectivity declarations while this branch was being built;
they require the same operation-specific migration as the original six modules.
No hardware API coverage, runtime authorization or eligibility, automatic issue
creation, historical backfill, or scenario generation is added.

## Contracts and components

ExpectedOutcome gains required operation/variant strings and `transient=False`.
Its constructor validates token shapes; startup resolves registered operations.
RunState.call accepts keyword-only `expected=()` and `reuse_gaps=True` before tool kwargs;
it validates each declared operation against the tool before any call or gap reuse.
Declared calls pass the same expectations to call and record_with_expected.
The user-create and user-list declarations become separate identities.
PCM preferences, processed links and aggregated links also get separate identities.
Connectivity's shared firmware matcher splits across system.list, capacity.report
and placement.find; global-job-feed limitations name job.list.
Existing idempotent cleanup declarations set transient=True.

The runner collects missing-scope rows in a separate gaps list. A matching real
failure records SKIP, never a promoting result, and adds the declared variant with
empty parameters plus an observation time. Emission fills commit, closure and
environment under the existing clean-tree and ignored-destination checks. The
output remains a list: evidence rows have `observation`; gap rows have `missing_scope`.
Deduplicate repeated gap matches within a run by operation/variant.

Format 3 accepts optional confirmation only on missing_scope. The validator rejects
unknown fields, invalid commit/hash/time/environment, future timestamps, duplicate
gap variants and confirmation on implemented_scope. Existing unconfirmed scope
entries remain valid. Closed-shape variant tokens are required for confirmed gaps.
Confirmed entries require empty parameters, matching the operation/variant reuse key.
The runtime projection and evidence-derived verification do not consume confirmation.

Before entering the hardware client, main validates module-level declarations from
the registered subtask modules. A bounded AST walk validates each declared call's
literal tool and literal list of module-level ExpectedOutcome constants against the
registry; dynamic/unreadable declaration sites or mismatched operation/tool pairs fail
startup. It loads/validates maturity records using the existing
catalog validator. Current matching confirmations populate RunState's known-gap set;
malformed records fail startup. A declared call checks its schema first, then returns
a distinct known-gap marker instead of calling the client when its pair is known.
record_with_expected recognizes that marker as SKIP without emitting a new gap.
Only confirmations matching release/family, closure and age 0..90 days qualify.
Changing those values, waiting beyond 90 days, or removing confirmation causes a new
attempt; stale historical missing scope itself remains recorded and non-promoting.
ST11 user-list calls set reuse_gaps=False: they must discover the UUID required to
delete a user just created by the scenario, even when inventory previously failed.
They can still emit a gap for an actual limitation; ordinary user inventory can reuse it.

## Success

- Current module-level ExpectedOutcome declarations have valid identities and match
  their dispatch tools; malformed declarations stop before a live call.
- Matched limitations emit validated missing_scope rows; InvalidDispatch, transient
  states, unmatched failures and known-gap reuse emit no gap or promoting evidence.
- Copying a confirmed row into a valid catalog makes the next matching declared call
  skip without reaching its client. Expired, changed-environment and changed-closure
  records allow the call again.
- Focused tests, just verify and pinned prek hooks pass; live proof is recorded only
  for the matching deployed commit and authorized scenario, or explicitly parked.

## Global Constraints

Python 3.11 through 3.14; amd64 and arm64. No new dependencies. Runtime authorization,
eligibility and the stable facade remain unchanged. Only record_verified promotes.
All public evidence is free of personal, machine and network identifiers.

## Failure model

- Actors and deployments: local operators using the existing runner with a reviewed
  repository catalog; CI validates catalog shape and tests without hardware.
- Invariants and assets: never promote a gap; never suppress cleanup based on past
  transient state or omit cleanup-dependent UUID discovery after user creation;
  reject invalid identities before hardware; retain honest timestamps.
- Accepted failure classes: release/family labels are coarse and operator supplied;
  a repaired remote limitation may remain cached for at most 90 days unless manually
  invalidated. Existing limitation matchers retain their current interpretation.
- Covered elsewhere: actual HMC API coverage and verification are #620/#625; scenario
  generation is #706; runtime authorization and existing cleanup implementation remain
  owned by their current boundaries. This change owns preserving cleanup-dependent
  discovery when introducing gap reuse.

## Threat model

- Added boundary: hand-edited confirmation enters the runner via maturity.json.
  Existing boundary: runner results reach a manually reviewed public catalog.
- Actors: operator and PR contributor can edit catalog data; HMC errors are untrusted.
- Controls: closed field sets and grammars, registry identity validation, exact
  declaration/tool agreement, existing ignored-output and clean-source checks;
  error text never enters confirmations. Malformed catalog fails before execution.
- Out of scope: credentials and hardware access policy stay with existing runtime
  controls; confirmations do not grant runtime permission.

## Validation

Focused tests exercise constructor/preflight rejection, limitation/transient/dispatch
separation, mixed output, duplicate matches, malformed catalog data, copy-and-reuse,
and expiry/environment/closure invalidation with controlled clock and fake client.
Include a later-scenario mismatched-operation test proving zero client calls across
the run, a confirmed constrained-scope rejection, and an ST11 cached-list-gap test
where successful create/list still reaches deletion.
Tests run across existing CI's eight Python/architecture legs. Guardrails are
`just verify` and `uv run --no-sync prek run --all-files` inside the feature worktree.
Live verification requires an authorized affected scenario against the branch commit;
record environment prerequisites and exact required operator action if unavailable.
