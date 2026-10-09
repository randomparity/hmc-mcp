# 0208: Bound parallel pytest by resources and owned process lifetime

## Status

Accepted on 2026-10-09 by the operator after review of the measured proposal.

## Context

Issue #1434 permits a measured GO or NO-GO. At 7f0dfe0d, native amd64/arm64
comparisons preserve 8,708 test identities and full per-file coverage while two
workers reduce wall time by 44.93–49.48%. The existing runner leaves two workers
after timeout. A constrained profile exposes tmpfs memory charges beyond process
RSS, and repeated runs can exhaust 2 GiB in either serial/parallel order.

## Decision

Keep the existing runner as owner and use at most two
pytest-xdist 3.8.0 loadfile workers when Linux lifecycle facilities, at least two
effective CPUs and at least 3 GiB remaining memory are visible. Otherwise run
serial; --serial explicitly selects the original path. The memory admission
uses host availability and visible cgroup ancestor limit-minus-current charge,
not physical RAM or maximum single-process RSS. No automatic test retry occurs.

Parallel mode owns a fresh pytest session and adopts/reaps orphan descendants.
Forward SIGINT once to that separate session, preserve the diagnostic window,
and bound TERM/KILL cleanup. Only owned unreaped children may be signalled; the
direct Popen child retains its own exit-status owner. Restore process-level state.
This extends ADR0130 for the parallel path without changing serial semantics.

## Consequences

Native resource-aware acceleration adds Linux-specific lifecycle and admission
code plus failure tests. The 3 GiB threshold is an acceleration condition, not a
mandatory hardware floor or a reservation against unrelated concurrent memory use.
Unknown facilities select serial. The operator approved the --serial/default and ownership choices.
Temporary evaluation artifacts are removed before final delivery.

## Considered & rejected

- **Use xdist with the current direct-child stop.** verified: the timeout probe at
  7f0dfe0d returned124 with two sleeping workers after three seconds, twice.
- **Only signal a new process group.** verified: existing subprocess tests create
  nested sessions; session-independent descendants require separate ownership.
- **Forward SIGINT in the original group.** verified: ADR0130 records duplicate
  delivery corrupting the diagnostic; a new owned session avoids that route.
- **Choose workers from visible CPU count alone.** verified: a one-CPU cgroup on
  the measured host still reports 48 affinity CPUs; CPU quota must constrain it.
- **Set memory eligibility from RSS.** verified: fresh parallel reached a cgroup
  peak1,995,755,520 bytes while sampled summed RSS was1,321,181,184 bytes;
  tmpfs/kernel charges are material, and retained fixtures caused later OOMs.
- **Home-grown test partitioning or a generic supervisor.** judgment: duplicates
  collection/coverage ownership or introduces machinery beyond this runner.
- **Serial default with an opt-in switch.** judgment: viable fallback design, but
  ordinary verify/CI would keep serial cost while still maintaining the new code.
- **Measured NO-GO.** judgment: not selected: the operator accepted the lifecycle and
  resource-policy cost after reviewing the measured performance benefit.
