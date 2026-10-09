# Bounded pytest execution (#1434)

## Status and authority

Accepted by the operator on 2026-10-09 after the bounded evaluation and review.
This specification replaces the experiment-only execution contract.
The frozen charter is issue #1434 / q1434-24622bc8, latest complete WORK:SCOPE
6079392765. Its exclusions and owners remain unchanged. Complexity M maps to
the unchanged 250-line denominator; this remains one PR in the full-spec lane.
The requested guarantees are resource-bounded workers, complete tests/coverage,
serial fallback, meaningful diagnostics and owned-descendant cleanup.
The operator explicitly approved the displayed automatic maximum-two default,
--serial fallback and owned-session/subreaper lifecycle.

## Evidence and alternatives

At immutable 7f0dfe0d, both native architectures passed two alternating pairs
with complete identity/per-file coverage equality and 44.93–49.48% less wall
for two workers. Normal local eligible pairs saved 47.83% and 48.06%; the approved
reversed confirmation is complete with the same identities and coverage.
Fresh constrained serial and parallel observations both passed. Running two
modes consecutively in one 2 GiB/no-swap scope caused the second mode to OOM
in either order because pytest temporary files remained charged to tmpfs.
Fresh parallel's actual cgroup peak was 1,995,755,520 bytes, substantially more
than its sampled process RSS. That evidence rules out an RSS-only memory policy.
The existing runner left two xdist workers alive after timeout, twice.

Accepted approach: automatic maximum two workers when the native
Linux resource checks below admit them, with an explicit --serial override.
It benefits existing just test/verify callers without editing their recipes.
Alternative: leave serial default and expose an opt-in --parallel; this avoids a
default change but adds tests/maintenance while ordinary CI keeps the serial cost.
Alternative: measured NO-GO; retain the deterministic six-case ordering correction
and report the measured speed/memory tradeoff without a new production lifecycle.
The operator selected automatic bounded execution after considering these alternatives.

## Resource admission and interfaces

Keep scripts/run_tests.py as the sole execution owner. Retain exact dev pin
pytest-xdist==3.8.0 and the existing locked app extras. Add no runtime dependency,
new script, production workflow edit, environment variable or worker-count knob.
The CLI accepts --serial alongside existing --timings, with abbreviation
disabled. --serial follows the existing serial path, arguments and output.

Automatic execution uses two workers only when all these checks succeed:
- Linux, the main Python thread, one active Python thread, default SIGCHLD
  disposition, readable direct-child inventory, and no pre-existing direct child.
- Readable host MemAvailable and a unified cgroup-v2 membership whose path resolves
  within /sys/fs/cgroup. Walk the current group and visible ancestors to that root.
  Read each existing root limit; root controller limits may legitimately be absent.
  Missing/malformed non-root CPU or memory controls cause serial fallback.
- Effective CPU capacity is min(affinity count, floor(quota/period) for each finite
  visible cpu.max), and is at least two. Never use an unbounded host CPU count.
- Remaining memory is min(MemAvailable, max(0, memory.max-memory.current) for each
  finite visible ancestor). Require at least 3 * 1024**3 bytes remaining.
- PR_GET_CHILD_SUBREAPER and PR_SET_CHILD_SUBREAPER are supported. Save the old
  process-level state; restore it in finally after ownership cleanup.

Unavailable/unsupported resource or process-ownership facilities select serial
before starting pytest. No fallback retries occur after tests start. The memory
threshold is a conservative admission budget, about 1.14 GiB above the measured
fresh constrained cgroup peak, not a reservation or a guarantee against concurrent
outside allocations. Resource visibility is the OS view available to the runner;
hidden container ancestor limits and changing limits are not inferred from host RAM.
The measured deployments are native Linux hosts, not nested container namespaces.
Other platforms and cgroup-v1-only environments retain serial execution.

Two-worker invocation adds -n 2 --dist=loadfile --max-worker-restart=0; all existing
coverage and timing behavior remains. Direct pytest and just test-verbose stay
serial as today. Document that distinction and the --serial invocation. On parallel
success append '; workers=2' to the existing success line so actual mode is visible.
Serial output stays byte-compatible. Failures replay captured bytes unchanged.

## Owned lifecycle

Only the standalone runner's new pytest subtree is owned. A pre-existing child,
non-default SIGCHLD reaper or additional Python thread prevents parallel entry;
tests of the parallel path execute in isolated subprocesses. Unit tests invoking
main in-process force serial selection unless they explicitly mock ownership APIs.

Enable subreaping and launch pytest with start_new_session=True. While waiting,
poll at 100 ms and reap terminated adopted direct children. Never waitpid the
live direct Popen child: Popen alone owns its exit status. Read direct children
from /proc/self/task/<main-pid>/children rather than scanning the whole host.
For an adopted PID, waitpid(pid, WNOHANG) proves parenthood; a zero result leaves
that child unreaped, so its PID cannot recycle before signalling. There is no
background reaper or signal handler consuming child status. Once Popen has reaped
its child, that historical PID is no longer excluded from the direct-child list.

Start a fresh session so terminal group SIGINT does not already reach pytest.
On the first KeyboardInterrupt, forward exactly one SIGINT to pytest's owned
process group if Popen has not reaped its leader, then retain ADR0130's 300-second
diagnostic window. Re-check returncode because Popen.wait briefly waits during
KeyboardInterrupt and may already have reaped the child. With no other reaper,
an unreaped leader prevents group-ID reuse before the signal. Group delivery and
PID-only delivery to the wrapper must both produce one forwarded interrupt.

At timeout, a second interrupt, or controller exit, clean the owned subtree.
Send TERM to the owned group only while the leader is unreaped. Also repeatedly
signal/reap direct adopted children; killing a parent adopts descendants even
when they created nested sessions. Allow three seconds for TERM, then repeatedly
KILL/reap for at most three seconds. A further interrupt during TERM escalates to
KILL; additional SIGINT during bounded KILL cleanup is deferred. No broad PID
scan or signal reaches unrelated processes. Restore the prior SIGINT handler
and subreaper state in finally, including launch and cleanup errors.

Cleanup completion requires the direct child reaped and the owned direct-child
inventory empty. A surviving child or ownership/restoration error returns nonzero
with an actionable diagnostic; it cannot report success. Kernel/SIGKILL termination
of the wrapper cannot execute Python cleanup and remains the enclosing job's duty.
Normal child status, signal-to-shell mapping, timeout124 and interrupt130 remain;
a cleanup failure instead reports failure and never hides the original diagnostic.
The legacy serial signal/diagnostic behavior remains unchanged.

## Failure model

- Actors and deployments: trusted contributors and native Linux CI executing the
  offline suite; serial execution remains available on other supported hosts.
- Invariants and assets: all original test identities, exact coverage denominator
  and floor, owned process status/cleanup, captured diagnostics, bounded workers.
- Accepted failure classes: sampled RSS misses brief/shared-page distinctions
  (actual cgroup evidence is separately reported); host/job SIGKILL cannot run
  user-space cleanup; outside allocations or resource-limit changes can invalidate
  a startup budget. None permits a false success or silently retried test.
- Covered elsewhere: product authorization/live-HMC behavior and sibling runtime
  optimizations retain their campaign owners. No live HMC operation is introduced.

## Validation and final state

Prove resource selection with boundary/ancestor/malformed-input cases and controlled
faults. Prove actual worker count, coverage combination, status propagation and
subtree cleanup with real subprocesses, including nested sessions, repeated SIGINT,
controller exit before descendants, nonzero status and state restoration. Observe
survivors before fixture containment; fixture cleanup cannot satisfy an assertion.
Retain the existing meaningful serial runner tests. Run full canonical just verify
and pinned all-files hooks, then unchanged eight native verify/eight wheel/two
library matrices. Production adoption requires its own end-to-end run showing the
selected mode; experiment timings are not relabelled as production-run timings.
Remove the temporary measurement workflow, script and its test module in a distinct
commit. Preserve the six-case collection correction. Keep only the approved minimal
production runner/tests/dependency/guidance and durable evidence/spec/ADR.
