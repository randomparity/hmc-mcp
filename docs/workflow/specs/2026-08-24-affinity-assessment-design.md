# NUMA affinity assessment design

**Branch:** `feat/assess-numa-affinity-317` from `main`  
**Decision:** [ADR 0088](../../adr/0088-affinity-assessment-contract.md)

## Goal

Provide one stable, read-only assessment of an LPAR's captured, current, predicted, and configured
minimum affinity evidence. The assessment explains one classification and recommends operator
actions without applying changes.

## Contract

`AffinityAssessmentInput` is a frozen value with `captured_score`, `current_score`,
`predicted_score`, `configured_minimum`, `captured_at`, `assessed_at`, `stale_after_seconds`,
`regression_threshold`, and `optimization_threshold`. Scores are integer percentages from 0 through
100. Thresholds are non-negative integers. Timestamps are timezone-aware and the stale window is a
positive integer.

`assess_affinity(input) -> AffinityAssessmentResult` returns `classification`, a complete evidence
mapping, `explanation`, and an ordered tuple of `recommended_actions`. Classification is one of
`regression`, `optimization-opportunity`, `policy-violation`, `unsupported-data`, or `none`.

When configured policy is absent, both caller thresholds are required. Missing scores, invalid or
future timestamps, evidence older than `stale_after_seconds`, or contradictory evidence return
`unsupported-data` with the precise reason and corrective action. Contradiction is representable
as a currently configured minimum that differs from the minimum captured in the snapshot; the
assessment refuses to choose which policy is authoritative.

With supported evidence, precedence is policy violation (`current < configured_minimum`),
regression (`captured - current >= regression_threshold` and positive), optimization opportunity
(`predicted - current >= optimization_threshold` and positive), then none. A configured minimum
supplies the policy boundary but does not invent regression or optimization thresholds; absent
thresholds disable those optional comparisons when policy is present.

## Surfaces and data flow

The pure contract lives in `affinity_assessment.py`. `assess_snapshot_affinity` validates a
version-1 snapshot, extracts the captured LPAR score and capture time, and combines them with
explicit current, predicted, and optional policy/threshold inputs. Python exports the values and
operations. MCP exposes local `snapshot.assess_affinity`; CLI exposes `snapshot assess-affinity`
and reads a bounded snapshot path. Both return the same JSON shape and perform no HMC I/O.

## Error handling

Structurally invalid arguments raise `ValueError` before assessment. Valid-but-unusable operational
evidence returns `unsupported-data`; callers therefore receive evidence and an explanation rather
than losing diagnostic context in an exception. Snapshot parsing retains existing bounded,
duplicate-aware validation errors.

## Testing

Behavior tests cover policy violation, regression, optimization opportunity, none, missing data,
stale data, unsupported policy data, contradictory prediction, threshold requirements, future
capture time, snapshot composition, MCP delegation, CLI output, and Python exports. Tests assert
that predictions are described as potential and recommendations never claim or perform mutation.

## Global constraints

- Python 3.11+ with the repository's pinned `uv`, Ruff, ty, pytest, and prek toolchain.
- Host x86_64; declared targets amd64, arm64, and ppc64le; relationship included.
- No dependency, migration, external write, universal threshold, or ADR-index edit.
- `just verify` and `uv run prek run --all-files` are hard gates.

## Resume facts

- Branch: `feat/assess-numa-affinity-317`
- Base branch: `main`
- Guardrails: `just verify`; `uv run prek run --all-files`
