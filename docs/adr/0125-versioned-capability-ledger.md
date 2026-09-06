# ADR 0125: Store the reference capability ledger as validated JSON

## Status

Accepted

## Context

Issue #621 needs a finite inventory derived from four frozen IBM documentation
snapshots. The inventory must preserve document provenance and POWER10/POWER11
differences while joining semantic command and REST capabilities to current
registered operation IDs. Later foundation work will consume those joins, but
the source snapshots remain local because they are large third-party corpora.

The maintainer approved the proposed JSON representation and validator boundary
in the active quest conversation on 2026-09-06.

## Decision

Store three versioned UTF-8 JSON artifacts under `docs/capabilities/`:

- `corpora.json` records the four snapshots and every captured or navigation
  topic with its source URL, capture time or explicit unknown, SHA-256, and
  operation/schema/overview/navigation classification.
- `rows.json` records normalized capabilities at operation, mode, and parameter
  granularity. Each row cites its source topics and carries exactly one honest
  disposition: supported, coverage child, proposed exclusion, or unknown.
- `operations.json` reconciles every current registry operation ID to capability
  rows or an explained repository-specific composite and names supported
  surfaces and existing tests.

Use a standard-library validator as the single structural reader. Its default
mode validates the checked-in records and their exact registry join offline. An
explicit source-root option additionally verifies the retained corpus files by
path and hash. Unknown and proposed-exclusion rows are structurally valid but
prevent a complete-coverage result.

The ledger records document presence separately from explicit HMC capability
requirements. A topic absent from one snapshot never proves firmware does not
support it. Test paths are inventory links and never claim that a test passed.
This decision introduces no runtime maturity or authorization behavior.

## Consequences

The inventory can be reviewed and gated without checking third-party pages into
the repository. Registry changes fail until their operation IDs are reconciled.
Source changes fail the explicit corpus verification. Semantic decomposition and
equivalence remain authored judgments and require review; hashes prove source
identity, not interpretation. Later work can consume stable IDs without parsing
prose or importing the MCP server.

## Considered & rejected

- **Markdown tables only.** judgment: nested modes, parameters, cross-version
  citations, and exact joins would be difficult to validate without inventing a
  second machine-readable representation.
- **SQLite or another database.** judgment: the offline dataset is small enough
  for JSON, and a database would add tooling and opaque diffs without providing
  a needed query or concurrency property.
- **Extend runtime `ToolSecurity`.** verified: `src/hmc_mcp/tool_registry.py`
  defines dispatch authorization metadata and is imported by server composition;
  issue #622 separately owns maturity semantics. Loading the reference corpus at
  runtime would join unrelated contracts.
- **Derive the denominator from registered tools.** verified: issue #620 requires
  the finite reference inventory to precede and independently check current
  implementation, so the registry cannot define that inventory.
- **Do nothing.** judgment: issue #621's downstream foundation and verification
  children have no finite set to bind or reconcile without this ledger.
