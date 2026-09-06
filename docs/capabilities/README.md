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
IDs in `operations.json`. An operation without a maturity row is unknown; an empty
`evidence` list is also unknown, not an inferred `not-run` result. Implementation
state and scope are recorded independently from evidence observations.

Evidence is independent across three channels:

- `contract-review` records review of the operation contract.
- `automated` records an automated check.
- `live` records a check in its named HMC release/build, hardware family, firmware,
  licensing, and topology.

Every observation has a result (`not-run`, `skipped`, `failed`, or `passed`) and
currency (`current` or `stale`). `not-run` is an explicit unattempted observation;
for a live gap it names the intended scenario, prerequisites, and a catalog obligation
joined to that operation and observation. `skipped` records an attempted check that
was not completed, `failed` records an attempted check that did not pass, and `passed`
records a check with asserted postconditions. `current` evidence has no invalidator
and covers the current implementation fingerprint. `stale` evidence is retained
history with an invalidating fingerprint and reason, including when its formerly
implemented scope has since been removed or narrowed; a changed runtime source, script,
or dependency manifest requires re-evaluation.

Format 1 admits no trusted promotion: every observation has `unverified` provenance.
Mocks, skips, opt-in, issue closure, transport-only success, and evidence from another
live environment do not promote live evidence because trusted provenance is absent;
free-text assertions alone do not establish trusted postconditions. A live-gap
obligation is work tracking, never evidence or promotion; an issue number is only an
optional pointer and does not own the obligation.

The catalog records no runtime eligibility. `existing-runtime-guards` neither grants
nor revokes admission: authorization, ownership, validation, capability, and safety
guards continue to control runtime behavior. Validate the catalog and its joins with
`just capability-inventory`.
