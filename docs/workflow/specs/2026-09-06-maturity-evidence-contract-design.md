# Operation maturity and evidence contract design

Status: approved for implementation by issue #622 and frozen scope annotation
`q622-a9e980a4`.
Decision: [ADR 0126](../../adr/0126-operation-keyed-maturity-evidence.md).

## Outcome

Define and validate the smallest operation-keyed contract that keeps implementation
scope, contract review, automated evidence, live evidence, and runtime eligibility as
independent facts. This foundation records representative current operations without
claiming that existing tests or hardware runs produced evidence they did not capture.

## Boundaries

This change extends the offline capability inventory from ADR 0125. It does not import
the catalog at runtime, alter `ToolSecurity`, change authorization or ownership, weaken a
capability or input guard, add an HMC call, or change user-visible discovery. Issue #623
owns producing trustworthy live-runner observations; issue #624 owns joining maturity to
discovery and generated documentation.

The catalog is sparse. A registered operation without a maturity record is `unknown` to
consumers. Unknown is not an evidence result, does not imply `absent`, and grants no
runtime eligibility.

## Catalog contract

`docs/capabilities/maturity.json` is UTF-8 JSON with duplicate-key rejection and this
top-level shape:

```json
{"format_version": 1, "admission_policy": "existing-runtime-guards", "operations": []}
```

Each operation record contains:

- `operation`: an exact unique ID from `operations.json`;
- `implementation`: `state`, `implemented_scope`, and `missing_scope`; and
- `evidence`: a list of uniquely identified observations.

Scope entries are exact objects with a non-empty stable `variant` ID and sorted unique
`parameters` entries. Each parameter entry has a non-empty `name` and a public-safe
non-empty `constraint` containing the applicable value or predicate; names alone are not
enough. `absent` requires an empty implemented list and a non-empty missing list;
`partial` requires both lists; `implemented` requires a non-empty implemented list and an
empty missing list. Parameter names are unique within a scope and entries are sorted
bytewise by `(name, constraint)`. Scope identity is the parsed tuple
`(variant, tuple((name, constraint), ...))`, independent of JSON member order and
whitespace. The implemented and missing identity sets are each duplicate-free and must be
disjoint.

Every evidence observation uses a uniform object with these fields:

- `id`: a stable catalog-wide ID;
- `channel`: `contract-review`, `automated`, or `live`;
- `scope`: an object whose parsed canonical scope identity equals an entry in the
  operation's `implemented_scope` list; JSON member order and whitespace are irrelevant;
- `scenario`: an object with a stable non-empty `id` and public-safe non-empty
  `description`;
- `result`: `not-run`, `skipped`, `failed`, or `passed`;
- `currency`: `current` or `stale`;
- `observed_at`: the canonical RFC 3339 UTC form `YYYY-MM-DDTHH:MM:SSZ`, or `null`
  for `not-run`; offsets, fractional seconds, spaces, and basic compact forms are rejected;
- `implementation_revision`: a full lowercase Git SHA, or `null` for `not-run`;
- `deployed_revision`: a full lowercase Git SHA for live attempted observations,
  otherwise `null`;
- `environment`: `null` outside the live channel; live observations require an object with
  non-empty `hmc_release`, `hmc_build`, `hardware_family`, `firmware`, `licensing`, and
  `topology` strings;
- `assertions`: unique non-empty postcondition descriptions;
- `cleanup`: `not-run`, `not-required`, `failed`, or `passed`;
- `provenance`: `{"kind": "unverified", "reference": <non-empty string>}` for attempted
  evidence and `null` for `not-run`; format 1 accepts no trusted producer kind;
- `promotion`: `{"eligible": false, "reason": <non-empty string>}`; `eligible: true` is
  rejected until a later format and validator add a trusted producer contract;
- `reason`: a non-empty explanation for `not-run`, `skipped`, or `failed`, otherwise
  `null`;
- `prerequisites`: sorted unique non-empty strings; required for a live `not-run` gap and
  otherwise possibly empty;
- `obligation`: an object with `catalog` equal to
  `<operation>#<observation-id>` and optional positive integer `issue` for a live
  `not-run` gap, otherwise `null`; the catalog identity is the durable owner and the issue
  is only a pointer;
- `implementation_fingerprint`: a full lowercase SHA-256 over the validator's normalized
  implementation surface for every current attempted observation; `null` for `not-run`;
  stale attempted history retains the fingerprint observed at its run;
- `invalidated_by`: `null` for current observations; stale observations require an
  object with the new full lowercase `implementation_fingerprint` and a non-empty
  `reason`. This pre-commit identity avoids predicting the invalidating commit's SHA.

Attempted observations (`skipped`, `failed`, `passed`) require a timestamp,
implementation revision, scenario, and provenance. A pass requires at least one asserted
postcondition. A live pass also requires cleanup `passed` or `not-required`; transport
success or job submission can satisfy the free-text shape but cannot promote in format 1
because no trusted provenance kind exists. Issue #623 must bind trusted scenario IDs to
validator-recognized outcome assertions before adding such a kind. `not-run` carries no
timestamp, revision, assertions, or provenance and requires cleanup `not-run` plus a
reason. A live `not-run` additionally requires a scenario, prerequisites, environment,
and durable obligation; other channels may use a planned scenario with empty prerequisites
and no obligation. `skipped` and `failed` require a reason and cannot promote.

At most one observation may be current for a channel plus canonical scope, scenario ID,
and live environment. Environment identity is the tuple of
`(hmc_release, hmc_build, hardware_family, firmware, licensing, topology)` in that fixed
order, independent of JSON member order. Revisions are evidence identity fields but do
not create a second current slot: a new revision must stale the old observation.
Historical observations for that key must be stale. Different scenarios and live
environments remain separate keys and may carry mixed current results.

The validator computes the implementation fingerprint from the relative path and bytes of
every tracked regular file under `src/` and `scripts/`, plus `pyproject.toml` and
`uv.lock`, excluding generated caches and the maturity catalog. Sorting paths bytewise and
hashing length-prefixed path/content pairs makes the value deterministic. A future trusted
promotion must match this live fingerprint. Every current attempted observation must
already match it, so any tracked implementation-surface change makes the catalog invalid
until the observation is marked stale or re-run. The format-1 catalog rejects
`promotion.eligible=true`, so mocked tests, issue URLs, opt-ins, and narrative claims
cannot masquerade as promotable live evidence before issue #623 installs a trusted
artifact validator.

The initial catalog contains representative records for `system.list` and
`sriov.set_mode`. They ground implemented and partial scope respectively. Their evidence
lists start empty unless an existing artifact establishes every field this contract
requires. Absence of qualifying evidence remains unknown; test file references and older
narrative hardware evidence are not upgraded into `not-run` or passing records by
inference.

## Promotion and admission

Promotion is derived per channel and exact canonical scope, scenario ID, implementation
revision, deployed revision where applicable, and live environment. It additionally
requires `currency=current`, `result=passed`, a matching implementation fingerprint, and
trusted provenance accepted by the validator. Format 1 has no trusted provenance kind and
therefore produces no promotion; its observations are non-promoting history and gaps.
Contract review cannot promote automated or live evidence; automated evidence, including
mocks, cannot promote live evidence. An opt-in flag, issue state, or implementation state
has no evidence effect.

The admission policy value `existing-runtime-guards` means the catalog is informational.
An implemented-but-unverified operation remains admitted only when the existing runtime
authorization, ownership, input-validation, capability, and safety checks admit it.
Maturity cannot bypass those checks. Known failure evidence does not silently change
runtime behavior; any restriction or widening requires its own reviewed decision.

## Validation and errors

Extend `scripts/check_capability_inventory.py` rather than adding a second parser or
dependency. The existing `capability-inventory` recipe remains the single gate. It loads
`maturity.json` with the same duplicate-key and UTF-8 checks, validates exact keys and
conditional fields, joins operation IDs to `operations.json`, rejects evidence outside
implemented scope, duplicate evidence IDs, contradictory current observations, live gaps
without self-resolving catalog obligations, current attempted evidence with a stale
fingerprint, or any format-1 promotion claim, computes the conservative
implementation fingerprint, and reports a deterministic maturity-record count beside the
existing structural result.

Every error names the maturity operation or evidence ID and the violated rule. Malformed
maturity data makes the existing command nonzero. Missing maturity rows remain valid
unknown state and do not affect capability-ledger completeness, which still describes F1
implementation coverage rather than F2 evidence.

## Verification

Focused tests cover an unknown operation (no maturity row), the three implementation
states, all four evidence results, mixed current live evidence across environments,
stale history plus a current regression, duplicate current scope, unknown operation IDs,
and false live promotion shapes. Controlled faults must show that parameter names with
different bindings remain distinct, scenarios share no current slot, evidence cannot cite
missing implementation scope, implemented and missing scopes cannot overlap, reordered
JSON objects retain one identity, a mocked test/issue/opt-in/transport-only record cannot set
promotion eligible, missing asserted postconditions reject a live pass, failed cleanup
rejects a live pass, a changed implementation fingerprint invalidates current attempted
evidence, and a different environment does not replace another environment's current
observation. A live not-run fixture must retain its scenario, prerequisites, and an exact
`<operation>#<observation-id>` catalog obligation; dangling references are rejected.

Run the focused validator tests, `just capability-inventory`, `just adr-numbering`, then
the full `just verify` and `uv run --no-sync prek run --all-files`. CI supplies the final
amd64/arm64 and Python 3.11–3.14 evidence. No live hardware run is required or authorized.

## Global constraints

- Python 3.11+ and only the standard library for validation.
- Host x86_64; declared CI targets amd64 and arm64; relationship included.
- JSON is UTF-8, versioned, deterministic, duplicate-key rejecting, and public-safe.
- No runtime import, dependency, migration, hardware mutation, safety change, new issue,
  or unrelated API work.
- Branch: `feat/maturity-evidence-contract-622`; base branch: `main`.
- Guardrails: `just capability-inventory`; `just adr-numbering`; `just verify`;
  `uv run --no-sync prek run --all-files`.
