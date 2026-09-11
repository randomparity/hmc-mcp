# HMC reference capability ledger

This directory inventories the four IBM documentation snapshots named by issue
[#621](https://github.com/randomparity/hmc-mcp/issues/621). It separates the
reference denominator from current implementation and verification maturity.

The artifacts use format version 1:

- `corpora.json` identifies the POWER10 and POWER11 command and REST snapshots,
  records archive and per-file hashes, classifies all 832 documents, and accounts
  for 14,311 structured source units. Source units cover command synopsis lines,
  option and attribute table rows, REST resources, methods and fields, and explicit
  version or capability statements.
- `rows.json` groups source units into 381 normalized command or REST capabilities.
  Each row preserves its POWER10/POWER11 presence and maps to a concrete child of
  epic #620. A missing page in either snapshot records only that document delta;
  it does not claim that an HMC release lacks the capability.
- `operations.json` reconciles all 156 registered MCP operations to reference
  families or explains a repository-specific composite. It records the current
  handler signature, registered surface and relevant test paths. These links do
  not claim that a test passed or that hardware verification exists.

Run `just capability-inventory` for the offline structural and registry check.
The command succeeds when the ledger is internally sound even while coverage
children remain. Its second output line states whether implementation coverage is
complete; the initial ledger is deliberately incomplete until those children land.

To reproduce the source identity and source-unit extraction, provide the four
retained directories explicitly:

```text
uv run --no-sync python scripts/check_capability_inventory.py \
  --source commands-p10=<SOURCE_ROOT>/hmc-commands-p10 \
  --source commands-p11=<SOURCE_ROOT>/hmc-commands-p11 \
  --source rest-p10=<SOURCE_ROOT>/hmc-rest-api-p10 \
  --source rest-p11=<SOURCE_ROOT>/hmc-rest-api-p11
```

The validator checks only those named roots, rejects symlinks and unexpected files,
and compares file bytes and regenerated source units. The large third-party source
snapshots remain outside Git; their URLs, capture timestamps and hashes remain here.

`supported`, `coverage-child`, `proposed-exclusion` and `unknown` are accounting
states for this inventory. They are separate from the maturity and live-evidence
contract owned by #622 and from runtime eligibility. A proposed exclusion is not an
approved exclusion, and either it or an unknown blocks a complete-coverage claim.

## Maturity and evidence catalog

`maturity.json` is a sparse, format-versioned catalog keyed by the stable operation
IDs in `operations.json`. Format 3 ([ADR 0132](../adr/0132-confirmed-live-limitation-gaps.md))
adds optional confirmations to missing scope. The evidence, currency, and promotion
model from [ADR 0127](../adr/0127-derived-live-verification-staleness.md) is unchanged.
Implementation — `absent`, `partial`, or `implemented`, with explicit implemented
and missing scope — is recorded independently from evidence.

Evidence retains one observation shape, with an exact key set:

```json
{
  "id": "st12-hmc-get-job",
  "channel": "live",
  "result": "passed",
  "scenario": "st12-job-inspection",
  "tested_commit": "<40 hex>",
  "observed_at": "2026-09-06T00:03:02Z",
  "hmc_release": "V10R3",
  "hardware_family": "POWER10",
  "cleanup": "not-required",
  "closure_fingerprint": "<64 hex>",
  "assertions": ["job-found", "job-identity-matches", "job-status-successful"]
}
```

`result` is `passed` or `failed`; ADR 0126's `not-run` placeholder is gone, because an
operation with no observation already reports as `unevidenced`. `assertions` lists the
ids that **held**, in declaration order — so a `failed` observation is distinguishable
from a `passed` one on that field alone. `hmc_release` and `hardware_family` are the
only free text and each has a grammar (`V<n>R<n>[M<n>]` and `POWER<n>`) rather than a
permissive character class, so a hostname, serial, or location code cannot be written
there. An operation carries at most one live observation; re-validating replaces it, and
the superseded record stays in `git log`.

### Confirmed limitation gaps

A declared, matching HMC limitation records `SKIP` and emits a separate
`{operation, missing_scope}` row, never an observation or promoting result.
`InvalidDispatch` remains a failure. Transient cleanup conditions (already absent
resources or already powered-off partitions) only skip; they do not confirm gaps.
Every declaration supplies a registered operation and a closed variant token.
Startup validates declarations and their tool associations before opening a client.

Copy an emitted `missing_scope` object into the operation's implementation record:

```json
{
  "variant": "managed-system-pcm",
  "parameters": [],
  "confirmation": {
    "tested_commit": "<40 hex>",
    "observed_at": "2026-09-10T00:00:00Z",
    "hmc_release": "V10R3",
    "hardware_family": "POWER10",
    "closure_fingerprint": "<64 hex>"
  }
}
```

The placeholders above describe hash lengths, not valid catalog values. Review the
implementation state and scopes together: `absent` has only missing scope; `partial`
has both; `implemented` has no missing scope. Confirmations are forbidden on implemented
scope, require empty parameters and have no evidence fields. Existing unconfirmed,
parameter-constrained scope entries remain valid. Duplicate scope identities fail validation.

On the next run a validated confirmation skips the matching declared operation/variant
only when release, hardware family and current handler import-closure fingerprint match,
and its age is between zero and 90 days inclusive. A cached skip does not refresh the
timestamp or emit another confirmation. After 90 days, an environment/closure change,
or removal of the confirmation, the runner attempts the call again. Replace a confirmed
gap only with a fresh observed limitation; remove/revise missing scope when it is fixed.
Stale confirmations remain valid historical records; future timestamps are invalid.
ST11 user listings always run because their UUID discovery enables cleanup after create.
Coarse environment labels can retain a repaired limitation for up to 90 days; remove
its confirmation when earlier revalidation is needed. Missing environment labels disable reuse.

### Derived staleness

Currency is **derived when the catalog is read**, never stored, and never a validation
error. An observation goes stale when either trigger fires:

- its operation's **import closure changed** — the SHA-256 over the handler module and
  every `src/hmc_mcp/` module it transitively imports no longer matches; the closure is
  computed from the source, so nothing is omitted by hand;
- it is **older than 90 days** — the ceiling that sees changes on the HMC itself, which
  this repository cannot observe.

Each operation then reports one of five states:

| State | Condition |
|---|---|
| `unrecorded` | no maturity record |
| `unevidenced` | a record, but no live observation |
| `stale` | the closure changed, or the observation exceeded the age ceiling |
| `failed` | a `failed` observation that is not stale |
| `current` | a `passed` observation that is not stale — the only promoting state |

### Reporting

`just verification-report` prints one line per operation carrying the implementation
state beside the derived state — `verification: sriov.set_mode partial current`, so a
partly implemented operation cannot read as fully verified — and a summary line. It
exits 0 and emits workflow warnings on a pull request or push; the weekly scheduled run
passes `--fail-on-stale` and fails when anything is stale. That weekly run is the only
forcing function: a stale observation nobody re-runs stays visibly stale, and nothing
promotes it back.

### Recording an observation

The live runner writes observations and confirmed gaps to a gitignored file beside its results document,
and never into the catalog: a human copies them in, and the pull request that commits
one is where the record is reviewed. The runner writes nothing unless the tree is clean
under `src/` and `scripts/`, both environment settings are present in `.env`
(`LIVE_TEST_ENV_HMC_RELEASE` and `LIVE_TEST_ENV_HARDWARE_FAMILY` — both or neither), and
`git check-ignore` claims the destination.

The validator proves shape, not truth. It applies every bound the runner applies —
because the catalog is hand-copied and hand-editable, so a check on the way out is not a
check on the way in — but it cannot know whether an observation describes a run that
happened. Trust rests on the record being small, closed-shape, and reviewed.

Scenario coverage remains hand-written until #706.

The catalog records no runtime eligibility. `existing-runtime-guards` neither grants
nor revokes admission: authorization, ownership, validation, capability, and safety
guards continue to control runtime behavior. Validate the catalog and its joins with
`just capability-inventory`.
