# Bounded pytest evaluation (#1434)

Decision: adopt bounded execution with the reviewed resource and lifecycle policy,
explicitly approved by the operator on 2026-10-09.

## Provenance and candidate

The operator invoked the #1430–#1435 campaign and approved this evaluation's
scope. [Issue #1434](https://github.com/randomparity/hmc-mcp/issues/1434) permits
adoption or a measured NO-GO; [the frozen charter](https://github.com/randomparity/hmc-mcp/issues/1434#issuecomment-6081889875)
retains its exclusions. The integrated base is
`64a2619c45801efd98b6c9618207a7652ae614c8`; the admitted timing/RSS comparisons below use
`7f0dfe0dcad9df1b11f06a2d42c82bd1547a0363`.

The candidate is pytest-xdist 3.8.0, exactly two workers,
`--dist=loadfile --max-worker-restart=0`. Both modes use the same locked app/dev
environment, including the xdist plugin. Pytest is 9.1.1, pytest-cov 7.1.0 and
coverage 7.15.4. The dependency was checked against its primary release source
before installation. Serial execution remains the comparison reference.

Every admitted timing/RSS comparison reports the same 8,708 passed test identities
and identical per-file coverage summaries: 17,079 statements, 4,540 branches and 4,152 covered
branches (91.45374449%). Combined statement-plus-branch coverage is 95.30968130%,
above the unchanged configured 90.5% combined floor. Those 8,708
include eight temporary observer tests. The six sharing-mode serialization cases
were preserved and sorted to make worker collection deterministic; no case was
removed. Independent complete collection under hash seeds 1 and 2 retained the
same identities before/after that correction and became identically ordered.

## Method

The temporary harness at the measured commit invokes the configured full pytest
suite through the installed interpreter. It records JUnit and coverage JSON,
monotonic command-through-cleanup wall time, and samples the sum of descendant
RSS every 100 ms. This sum includes shared pages once per process and can miss
short peaks; it is not unique physical memory or maximum single-process RSS.
The observer's own RSS is excluded. Cgroup accounting, where recorded, includes
all charged processes, tmpfs and kernel memory, so it answers a different question.

The harness runs through `uv run --no-sync`, preserving installed console-script
PATH and the locked app extras. It strips ambient pytest/coverage overrides.
The Linux observer adopts/reaps orphan descendants, and contains failures after
recording them. Its cleanup is not evidence that the production runner cleans
workers. The separate lifecycle probe observes survivors before containment.

Each sample runs `python -m pytest -q` with JUnit and coverage JSON outputs; the
parallel command adds exactly the three options above. The configured coverage
gate stays enabled. Wall includes observer teardown. All package/setup caches
are retained; these are warm development comparisons. Hosted jobs use their
normal setup/cache path. Native jobs run serial, parallel, parallel, serial on
one runner per architecture; no queue delay is included in sample wall time.

Local: x86_64 Fedora 44, Linux 7.2.7, Python 3.11.15, 48 affinity CPUs and
approximately 250.92 GiB physical RAM. Normal local cgroup limits are unlimited.
The constrained scope enforces one CPU quota, 2 GiB memory and zero swap for the
whole driver/observer/test process tree; affinity remains 48 and is not a CPU
quota. Local `/tmp` is tmpfs. Native: Ubuntu 24.04 on amd64 and arm64,
Python 3.11.16 and four affinity CPUs each. Their recorded immediate cgroup
limits are unlimited; physical RAM and ancestor limits were not captured.

## Eligible samples

RSS values are decimal MB (1 MB = 1,000,000 bytes).

| Profile / pair | Serial wall (s) | Two-worker wall (s) | Serial RSS (MB) | Two-worker RSS (MB) |
|---|---:|---:|---:|---:|
| Normal local, first eligible | 472.164 | 246.312 | 790.8 | 1,362.1 |
| Normal local, reversed confirmation | 469.769 | 243.986 | 793.2 | 1,339.9 |
| Native amd64, first | 409.411 | 215.557 | 810.1 | 1,378.4 |
| Native amd64, reversed | 388.810 | 214.137 | 806.1 | 1,381.9 |
| Native arm64, first | 558.248 | 282.042 | 810.7 | 1,346.6 |
| Native arm64, reversed | 533.661 | 278.412 | 802.7 | 1,344.6 |

The native pairs reduce wall time by 44.93–49.48% and increase sampled summed
RSS by 66.11–71.42%. The first eligible local pair reduces wall by 47.83% and
increases sampled summed RSS by 72.24%. The explicitly authorized final reversed
normal pair reduces wall by 48.06% and increases RSS by 68.91%. Serial walls differ
by 0.51% and parallel walls by 0.95% (ranges relative to their two-run means).
The initial invalid pair remains counted: three normal attempts produced two admitted pairs, with no
further normal repetition authorized. These are measured comparisons, not
promised multipliers or sums of earlier component savings.

[The native evaluation run](https://github.com/randomparity/hmc-mcp/actions/runs/37871628038)
completed all eight samples on the exact PR head. Its normalized artifacts hash
test/file identifiers and retain statuses/coverage without publishing raw logs.
[Ordinary CI at that head](https://github.com/randomparity/hmc-mcp/actions/runs/37871627994)
also passed eight native verification, eight wheel and two library legs plus the
report; one conditional support-drift job was skipped. This is experiment-head
verification, not final-branch proof.

## Failures and limits

The original normal attempt is rejected: serial 459.448 s / 793.7 MB returned
nonzero because a direct-interpreter driver omitted the console-script PATH and
the observer deferred adopted-zombie reaping. The original parallel command
returned a six-case collection-order mismatch after 14.987 s. Neither is admitted
as a successful performance result. Corrected invocation, active adopted-child
reaping and sorted parameter order were verified before the admitted samples.

The first native attempt was cancelled on both architectures. Its retained arm64
serial artifact (585.901 s / 837.4 MB, nonzero) also used a merge-preview tree
rather than the local candidate and had 8,811 identities. It is rejected; no
cross-tree comparison is made. The corrected temporary workflow explicitly
checks out the same immutable PR-head SHA for both native architectures.

The first constrained serial run passed in 530.629 s / 791.8 MB with the same
identities and coverage. The following parallel command was terminated by the
kernel memory controller around 45% progress. The scope returned 143 with
`Result=oom-kill`, exact kernel memory peak 2 GiB and zero swap. No final candidate
JUnit, coverage, elapsed-time or RSS report exists; none is invented.
At that event, kernel accounting showed about 809.6 MB anonymous memory,
1,252.9 MB tmpfs/shared-memory charge and 84.8 MB kernel memory. The preceding
serial fixture directory retained about 649 MB, and the failed parallel one
about 512 MB. Both commands shared one scope, so retained temporary files make
this an order-dependent resource result, not proof that parallel process RSS
alone exceeded 2 GiB. The authorized reversed confirmation used a fresh scope:
parallel passed in 504.502 s / 1,321.2 MB sampled RSS, with exact identities and
coverage. Its actual cgroup peak was 1,995,755,520 bytes with zero max/OOM events.
After pytest exited, 787,968,000 bytes remained charged, including 720,486,400
bytes shmem. The subsequent serial command then also OOM-stopped (143, no final
sample), with about 626.1 MB anonymous, 1,422.6 MB shmem and 98.5 MB kernel
memory at the event. The scope again peaked at exactly 2 GiB. This reversal
confirms that retained tmpfs can kill the second mode in either order. It does
not identify an intrinsic fresh parallel-only memory failure. No third constrained
pair ran. Successful fresh serial/parallel observations come from different
attempts; they are not presented as a complete successful paired repetition.

Separate production-runner timeout probes reproduced two surviving xdist workers
three seconds after runner exit, twice; the serial controls had none. The runner
only terminates its direct child. Fixture containment subsequently removes the
survivors and receives no credit for production cleanup. Owned sessions can
address worker signalling, but nested-session descendants additionally require
an ownership/reaping design before production adoption.

## Supplemental memory accounting

Normal-local and native timing samples above report sampled summed RSS; they do
not establish a kernel high-water value. The normal-local actual charged-memory
observation is complete; both native parallel observations remain pending under
the bounded memory-only grant.
The constrained kernel peak remains valid. Supplemental results use the production
runner at their own immutable source and do not enter the earlier timing dataset.

At source ffc1f774, the normal-local production observation passed 8,762 cases with
two workers and exact complete identity/per-file coverage equality. Its kernel
memory.peak was 1,998,241,792 bytes; all memory events were zero, no descendants
remained, and the unit was removed. Execution cost 236.641 s is not a timing comparison.
The first native amd64/arm64 observations at the same source returned runner exit 0 but
selected serial because fresh user scopes lacked cpu.max. Their rejected serial
peaks were 1,650,221,056/1,610,764,288 bytes and costs 511.662/529.780 s. Neither supplies
parallel evidence or admitted complete identity/coverage proof. Both units were
removed with no descendants or memory events. Both native system-manager preflights
then passed at source 050f4107: actual
resource/ownership selectors chose two, original constraints/affinity were preserved,
exit 0/7 and timeout controls passed, and every owned unit was removed. These are
cheap fixture results, not full-suite memory samples. Exactly one additional full
native candidate per architecture is now allocated; results remain pending.
