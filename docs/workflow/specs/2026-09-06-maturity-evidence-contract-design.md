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

Scope entries are concise non-empty strings describing operation variants or parameter
subsets. `absent` requires an empty implemented list and a non-empty missing list;
`partial` requires both lists; `implemented` requires a non-empty implemented list and an
empty missing list. The lists may not contain duplicates.

Every evidence observation uses a uniform object with these fields:

- `id`: a stable catalog-wide ID;
- `channel`: `contract-review`, `automated`, or `live`;
- `variant`: a non-empty operation-variant description;
- `parameters`: sorted unique applicable parameter names, possibly empty;
- `result`: `not-run`, `skipped`, `failed`, or `passed`;
- `currency`: `current` or `stale`;
- `observed_at`: an RFC 3339 UTC timestamp, or `null` only for `not-run`;
- `implementation_revision`: a full lowercase Git SHA, or `null` only for `not-run`;
- `deployed_revision`: a full lowercase Git SHA for live attempted observations,
  otherwise `null`;
- `scenario`: a non-empty public-safe description, or `null` only for `not-run`;
- `environment`: `null` outside the live channel; live attempts require an object with
  non-empty `hmc_release`, `hmc_build`, `hardware_family`, `firmware`, `licensing`, and
  `topology` strings;
- `assertions`: unique non-empty postcondition descriptions;
- `cleanup`: `not-run`, `not-required`, `failed`, or `passed`;
- `source`: a non-empty repository path or public evidence URL, or `null` for `not-run`;
- `reason`: a non-empty explanation for `not-run`, `skipped`, or `failed`, otherwise
  `null`;
- `invalidated_by`: `null` for current observations; stale observations require an
  object with a full lowercase `revision` and non-empty `reason`.

Attempted observations (`skipped`, `failed`, `passed`) require a timestamp,
implementation revision, scenario, and source. A pass requires at least one asserted
postcondition. A live pass also requires cleanup `passed` or `not-required`; transport
success or job submission alone therefore cannot satisfy the shape. `not-run` carries no
timestamp, revision, scenario, environment, assertions, or source and requires cleanup
`not-run` plus a reason. `skipped` and `failed` require a reason and cannot promote.

At most one observation may be current for a channel plus exact variant, parameter list,
and live environment. Historical observations for that key must be stale. This makes a
new failure an explicit regression without erasing the prior pass. Different live
environments remain separate keys and may carry mixed current results.

The initial catalog contains representative records for `system.list` and
`sriov.set_mode`. They ground implemented and partial scope respectively. Their evidence
starts `not-run` unless an existing artifact contains every field this contract requires;
test file references and older narrative hardware evidence are not upgraded into new
passing records by inference.

## Promotion and admission

Promotion is derived per channel and exact scope. Only `currency=current` plus
`result=passed` promotes. Contract review does not promote automated or live evidence;
automated evidence, including mocks, does not promote live evidence. Live evidence is
valid only for its exact recorded environment and scenario. An opt-in flag, an issue
state, or an operation being implemented has no evidence effect.

The admission policy value `existing-runtime-guards` means the catalog is informational.
An implemented-but-unverified operation remains admitted only when the existing runtime
authorization, ownership, input-validation, capability, and safety checks admit it.
Maturity cannot bypass those checks. Known failure evidence does not silently change
runtime behavior; any restriction or widening requires its own reviewed decision.

## Validation and errors

Extend `scripts/check_capability_inventory.py` rather than adding a second parser or
dependency. The existing `capability-inventory` recipe remains the single gate. It loads
`maturity.json` with the same duplicate-key and UTF-8 checks, validates exact keys and
conditional fields, joins operation IDs to `operations.json`, rejects duplicate evidence
IDs and contradictory current observations, and reports a deterministic maturity-record
count beside the existing structural result.

Every error names the maturity operation or evidence ID and the violated rule. Malformed
maturity data makes the existing command nonzero. Missing maturity rows remain valid
unknown state and do not affect capability-ledger completeness, which still describes F1
implementation coverage rather than F2 evidence.

## Verification

Focused tests cover an unknown operation (no maturity row), the three implementation
states, all four evidence results, mixed current live evidence across environments,
stale history plus a current regression, duplicate current scope, unknown operation IDs,
and false live promotion shapes. Controlled faults must show that a mocked automated pass
does not become live, a skip cannot satisfy pass fields, missing asserted postconditions
reject a live pass, failed cleanup rejects a live pass, and a different environment does
not replace another environment's current observation.

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
