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
