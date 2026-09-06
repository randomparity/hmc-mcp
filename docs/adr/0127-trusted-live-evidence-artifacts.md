# ADR 0127: Bind live promotion to a committed runner-emitted evidence artifact

## Status

Accepted

## Context

[ADR 0126](0126-operation-keyed-maturity-evidence.md) defined an operation-keyed maturity
catalog whose format 1 admits only `unverified` provenance, and assigned issue #623 the
first trusted live-artifact validator. Until that validator exists, every live observation
is hand-authorable prose and nothing distinguishes a real hardware run from a typed claim.

The live runner cannot supply that distinction as it stands. `RunState.call` records `PASS`
for any tool call that returned, so a call that did nothing is indistinguishable from one
that did the work. `record_expected_or_real` converts failures to `SKIP` by matching
caller-supplied substrings against the exception text *and its traceback*, so a fragment
such as `"400"` matches a line number. Nothing records the HMC release, the revision under
test, or whether the deployed build matches it, which epic #620 requirements 7 and 16 both
require before any evidence can be scoped. A survey of every dispatch against the served
tool schemas found 23 that send arguments the tool does not accept or omit ones it
requires — three of them routed through the substring path, where the harness's own defect
can be recorded as a known HMC limitation.

The validator that must judge this evidence runs offline, in `just capability-inventory`,
with no HMC, no network and no secret material available to it.

## Decision

Promotion binds to a **committed evidence artifact the runner emitted**, not to fields a
human typed into the catalog.

The runner mints one `RunMetadata` per invocation carrying a run id, RFC 3339 start and
finish timestamps, the implementation revision, the deployed revision, a
`build_identity_verified` flag that is true only when those revisions agree and `src` and
`scripts` are clean, the ADR 0126 implementation fingerprint, and six required operator
environment fields. It writes a redacted, format-versioned evidence artifact beside its
results document. Committing one under `docs/capabilities/evidence/` is a human act; the
runner never writes there.

`maturity.json` moves to format 2, whose provenance admits a second kind,
`live-artifact`, referencing `evidence/<name>.json#<row-id>`. The validator resolves that
reference as a direct child of the evidence directory and requires the observation to
equal the named row on operation, scope, scenario, result, assertions, cleanup,
environment, both revisions and the fingerprint. `promotion.eligible` may be true only for
a current live pass whose row verifies, whose `build_identity_verified` is true, whose
recorded fingerprint still equals the one computed now, and whose scope is currently
implemented. Format 1 is replaced rather than dual-supported: these are pre-release
repository artifacts with no external consumer.

Non-promotion is the default, and is enforced by the shape of the API rather than by
review. `RunState.record` keeps its signature and produces a new `observed` result that
can never promote, so the 196 existing record sites — none of which assert anything —
remain honest without being edited. Only the new `record_verified`,
which requires a non-empty list of postconditions and a cleanup disposition, can reach
`passed`. Failures are classified structurally: HMC codes and HTTP status are matched as
whole tokens against the exception message alone, and an access-policy denial is
recognised by ADR 0038's closed template, since the concrete exception type does not
survive FastMCP's `ToolError` boundary. A workflow declares the outcomes it tolerates as
`ExpectedOutcome` records naming an error code or a denial; substring matching is removed.

Dispatch arguments are validated against the served input schema twice: statically, by
extending the #485 name guard in `tests/test_live_runner.py`, and at runtime before the
call is made. An invalid dispatch records `failed`, never `skipped`.

## Consequences

A promoting claim now requires a file a reviewer can read in the pull request beside the
observation that cites it, and a fabricated observation fails `just capability-inventory`
offline. Promotion decays automatically on any change under `src`, `scripts`,
`pyproject.toml` or `uv.lock`, which is the invalidation ADR 0126 specified and the cost of
its deliberately conservative fingerprint.

Small JSON evidence files accumulate in the repository. That is the point — they are the
reviewable substance of a claim — but they are public, so both the runner and the
validator enforce the redaction detector, and the patterns move to
`scripts/live_test/redaction.py` so one definition serves both.

Three costs are real. The six `LIVE_TEST_ENV_*` values are operator-supplied and unverified
against the HMC, so a mis-typed release scopes evidence wrongly; the artifact makes that
visible in review rather than preventing it. Recognising denials by message template
couples the runner to ADR 0038's wording, so a test provokes a real denial through the
composed application and fails if that wording drifts. And correcting the 23 broken
dispatches changes what the runner sends to a live HMC — those paths have never actually
executed, so their first real run is untested by construction.

## Considered & rejected

- **Hash-bound artifact kept outside Git.** judgment: the offline validator could then
  check only internal consistency, so a plain `just capability-inventory` would accept a
  fabricated observation and trust would be opt-in — the opposite of what ADR 0126 asked
  this issue to build.
- **A `live-asserted` provenance kind with structured assertion fields and no artifact.**
  judgment: still entirely hand-writable, so it would close ADR 0126's assignment in name
  while establishing no trust.
- **Sign the artifact.** judgment: this repository has no key management, no distribution
  path for a public key, and no service to hold a private one; a signature nobody can
  verify is worse than an unsigned file that is reviewed.
- **Change `record` to require assertions at every call site.** verified: `rg -c
  'state\.record' scripts/live_test/*.py` sums to 196 sites across twelve workflow modules
  (`ded24a77`); a required argument there is a mechanical edit whose omissions are
  invisible, whereas a non-promoting default cannot be got wrong.
- **Change `call` to return a three-element result.** verified: every workflow module
  unpacks it as `st, data = await state.call(...)`, so widening the arity edits every
  dispatch site to carry a value only two helpers read. Returning the classification as
  the failure `data` reaches the same helpers without touching the modules.
- **Keep substring matching and merely narrow the patterns.** verified: the match runs
  against `str(data).lower()` where `data` is the exception text plus
  `traceback.format_exc()` (`scripts/live_test_runner.py:475`), so any narrowing still
  matches file paths, line numbers and UUID fragments in frames the caller never
  considered.
- **Classify denials by exception type.** verified: `Client.call_tool` raises
  `fastmcp.exceptions.ToolError` for every server-side failure; a probe of the composed
  application returned `ToolError` for an argument-validation failure and for a
  configuration failure alike, so `ConnectionScopeError` and `TargetScopeError` are not
  observable at the client.
- **Fix only the `job_uuid` pair issue #623 names.** judgment: the argument guard this
  issue requires goes red on all 23, and this repository does not ship over a red
  guardrail.
- **Do nothing.** verified: ADR 0126's Decision section names issue #623 as the owner of
  the first trusted live-artifact validator, and its format 1 admits no promoting
  observation at all, so the maturity catalog cannot progress without it.
