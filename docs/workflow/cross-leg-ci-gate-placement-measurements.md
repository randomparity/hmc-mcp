# Cross-leg CI gate-placement evidence — issue #1435

## Evaluation boundary

This is a NO-GO contract evaluation, not a before/after relocation benchmark.
Repository inspection used `d520a1ff2acae51e94493bda6be6b8dea8ddb6de`.
No candidate workflow, positive-selector trial, failed/skipped/cancelled candidate
run, branch-protection mutation or post-#1434 timing was performed. Normal PR CI
for these records is validation of the retained workflow, not candidate evidence.

## Read-only configuration observations

On 2026-10-09 UTC, canonical GitHub reads for `randomparity/hmc-mcp` returned:

| Command (with `GH_HOST=github.com`) | Observed result |
| --- | --- |
| `gh api repos/randomparity/hmc-mcp/branches/main --jq '{protected,sha:.commit.sha}'` | `protected: false`; SHA `d520a1ff2acae51e94493bda6be6b8dea8ddb6de`; exit 0 |
| `gh api 'repos/randomparity/hmc-mcp/rulesets?per_page=100'` | `[]`; exit 0 |
| `gh api repos/randomparity/hmc-mcp/branches/main/protection` | HTTP 404, `Branch not protected`; exit 1 |

The explicit unprotected response agrees with the branch metadata; it is not an
inference from an arbitrary API error. These mutable observations expire when settings
change. They establish no configured required checks at inspection, not a proof that
an administrator could never enable them. No job is renamed by this change.

Locally, `uv run --no-sync prek run --help` on pinned prek 0.5.0 exited 0 and
listed `[HOOK|PROJECT]...`. Positive multiple-hook selection is available; partition
correctness and target equivalence were not tested here. Current sources show all
fourteen hooks in each native producer and `verify-runtime` before wheel upload.

## Historical cost evidence and limits

The [#1430 baseline](verification-timing-baseline-1430.md) measured three warm local
runs at `cd005939a85897308d96e5351a9d2e7e2ad948b8`: secret scanning took
64.137/64.376/66.711 seconds. Formatting and workflow auditing were about 0.02 and
0.05 seconds. That Fedora 44 x86_64/Python 3.11.15 environment is not a hosted runner;
its resource/cache context is in the source report, and it predates later optimizations.

The [#1431 report](ci-gates-once-measurements.md) retains successful hosted
[run 37854277531](https://github.com/randomparity/hmc-mcp/actions/runs/37854277531)
at `082b9962777debebc2ef3dc762cf0c737440c09a`: 900 seconds creation-to-final-status
elapsed, 5,764 summed seconds over 19 executed jobs and 3 seconds until first job
start; one conditional skipped job is excluded. These different-commit historical
observations identify repeated cost, not savings attributable to #1435.

Counterfactual: three once-per-workflow hooks would reduce their invocation count
from 24 to 3, while eleven hooks remained in eight producers. Invocation counts are
not runner seconds or latency. A dedicated prerequisite can also lengthen the critical
path through provisioning, queueing or serialization. No numerical savings are inferred.
Comparable candidate elapsed time, aggregate runner time, queue delay and variance are
unmeasured; that limit remains explicit in the NO-GO decision.
