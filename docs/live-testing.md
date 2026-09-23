# Running a live test against real HMC hardware

This is the whole procedure. Follow it without reading the runner's source.

A live run **creates, mutates and deletes real partitions and profiles on a
managed system**. Nothing here runs in CI, and no `just` recipe reaches an HMC:
every command below is one you run deliberately.

## Before you start

You need a host with network reach to the HMC, credentials for it, and this
repository checked out with `just setup` already run.

Credentials resolve in this order, highest first:

1. `HMC_*` environment variables already set
2. `config.toml` in the platform config directory — the documented profile
3. a local `.env` file

That directory is `~/.config/hmcpctl` on Linux (or `$XDG_CONFIG_HOME/hmcpctl`)
and `~/Library/Application Support/hmcpctl` on macOS. The runner resolves it
per platform; when it finds nothing it prints the path it looked in.

Scenario settings — every `LIVE_TEST_*` key — come **only** from `.env`. That
file is git-ignored and must stay that way; it names the managed system a run
will create and delete partitions on.
`.env.example` lists every required key. Size the scratch partition's
processing units, `LIVE_TEST_SCRATCH_CREATE_DESIRED_PROCS` and
`LIVE_TEST_SCRATCH_CREATE_MAX_PROCS`, for its vCPU counts on your platform:
the create sends them explicitly, and the HMC refuses too few units per
virtual processor.

## 1. Ask whether the run would start

```sh
uv run --no-sync python scripts/live_test_preflight.py --group dedicated
```

`--no-sync` is required on every command in this document. A bare `uv run`
prunes the `app` extra and the runner stops importing.

Preflight calls the same validators the runner gates on, so its configuration
verdict is the runner's. Exit 0 means the runner would start.

It also prints what each arm will touch:

```
predicted arms — the run decides; a RUNNABLE arm may still SKIP
  dedicated  RUNNABLE configured
    will mutate: managed system sys-R1
    will mutate: partitions named live-pcie-* (created, then deleted)
    will mutate: profile default io_slots (assigned, then restored)
    will mutate: dedicated slot 21010020
    envelope:    admitted (ADR 0053 envelope)
```

**Read that list before continuing.** It is the last point at which nothing has
been changed.

Two things preflight reports but does not block on: an unreachable HMC, and a
managed system outside the ADR 0053 envelope. Both make the arm SKIP, which is
correct behaviour, not a failure. Add `--skip-hardware` to predict from
configuration alone and contact no HMC.

Preflight predicts. Only the run decides — an arm can SKIP where preflight said
RUNNABLE, because it checks preconditions against hardware at dispatch.

## 2. Run one arm

| Arm | Command | Covers |
|---|---|---|
| round2 | `uv run --no-sync python scripts/live_round2.py` | subtasks 0–15 |
| vmedia | `uv run --no-sync python scripts/live_vmedia.py` | subtasks 16–22 |
| sriov | `uv run --no-sync python scripts/live_sriov.py` | subtask 23 |
| dedicated | `uv run --no-sync python scripts/live_dedicated.py` | subtask 24 |
| bare-cec | `uv run --no-sync python scripts/live_bare_cec.py` | subtask 25 |

Each writes `test-results-<arm>.json`. Run one arm at a time: they share a
managed system, and a concurrent run makes the recovery check in step 4
ambiguous about which run stranded what.

`scripts/live_test_runner.py` is there for anything these do not cover; see its
`--help`.

### Reading the output

Rows print as they complete. **Row subtask ids go up to 35, while the ids you
can dispatch stop at 25.** That is not a bug: subtask 24 dispatches the whole
dedicated arm, and the arm records its internal phases as rows 26 through 34.
A row numbered 31 is part of the arm you asked for. Subtask 25 dispatches the
bare-cec arm, which records its own steps as row 35. It reuses the dedicated
arm's baseline, fixture-create and cleanup steps, so rows 29, 30 and 34 appear
in a bare-cec run too, with their dedicated-arm wording.

A SKIP is a result, not a failure. An arm SKIPs when a precondition is absent —
an out-of-envelope system, no unassigned slot, a capability the HMC refuses —
and that is the arm working.

### The bare-cec arm

The bare-cec arm is the release path end to end. It creates a partition, assigns
it a dedicated slot, powers it on once with no profile and records what that does,
and activates it to SMS. It then reads its reference codes and console and runs
the PowerOff variants, including an `osshutdown` it expects the HMC to refuse.
Last, it unassigns the slot and deletes the partition.

- It reads the same four `LIVE_TEST_DEDICATED_PCIE_*` keys as the dedicated arm.
  With no DRC index configured it takes the first free slot, whatever its kind.
- It SKIPs unless `HMC_AUTHORIZE_POWER_OPERATIONS=true`, so its evidence covers
  the ownership-guarded power path.
- `LIVE_TEST_ACCEPT_PLATFORM_DUMP=true` lets it run `dumprestart`, which crashes
  the partition and takes a platform dump. Unset or `false` skips that one step.
- Run it on its own. In an `all` run the dedicated arm runs first, and bare-cec
  SKIPs rather than record a second fixture over the one the recovery check reads.

## 3. Produce the evidence

```sh
uv run --no-sync python scripts/live_test_evidence.py test-results-dedicated.json
```

This prints a Markdown matrix stamped with the commit the run executed on.
**Paste that, not a hand-copied table.** A transcribed matrix carries no commit,
so nothing marks it stale when the branch moves — which is how PR #869's matrix
survived two weeks and a merge that left the arm unable to import.

If it refuses with "cannot be attributed to a commit", the document has no
`run.tested_commit`. It was written by an older runner; re-run.

If the header says **tree was dirty**, `src/` or `scripts/` had uncommitted
changes, so the sha does not name the code that ran. Commit and re-run before
citing it.

The matrix deliberately omits each row's `data` and `note` and the document's
`hmc` block. Those carry HMC-derived text — hostnames, account names, ISO
names, location codes — and are redacted only on FAIL rows. **Do not paste the
results JSON itself into a pull request, issue, or any public location.**

## 4. Confirm the system is clean

Always, including after a run that looked fine:

```sh
uv run --no-sync python scripts/live_test_recovery.py --results test-results-dedicated.json
```

After a bare-cec run, pass `--results test-results-bare-cec.json`. The check reads
the same artifacts for both arms.

| Exit | Meaning |
|---|---|
| 0 | nothing carrying this run's marker survives |
| 1 | something is stranded; the output names it and the command that clears it |
| 2 | the state could not be read — **this is not clean** |

The check issues no mutating call. When it reports something stranded, run the
command it prints yourself, then run the check again.

Exit 2 still prints anything it had already confirmed before the read failed,
so treat its findings as real and the silence after them as unknown. A run that
ends on exit 2 has not been shown clean by anything — check the system yourself.

It identifies this run's leftovers by the run marker recorded in the results
document. A partition sharing the fixture's name but carrying a different
marker is never attributed to your run, and never reported for you to delete.
Run the check from the run's tested commit. A partition that carries your marker
in an ownership stamp this checkout cannot parse, such as one written before the
`hmcpctl` rename, exits 2 rather than being reported clean.

## If something goes wrong mid-run

Stop and run step 4. The arm's cleanup refuses to mutate anything whose
ownership it cannot confirm, so an interrupted run leaves its traces in place
rather than deleting something it did not create. That is the safe outcome, and
step 4 is what tells you what is there.

Never hand-delete a partition because its name looks like a fixture. Check the
marker first.

A bare-cec run interrupted after activation can leave its partition running.
The recovery check reports it, but deleting a running partition fails. Power it
off first with `chsysstate -m <system> -r lpar -n <partition> -o shutdown --immed`.
Then remove the slot and delete the partition with the commands the check prints.

## Recording the result

A live matrix is evidence **only for the commit it ran on**. When quoting one in
an ADR, a pull request, or an issue, quote the commit with it. Before trusting
an existing matrix, check whether the branch has moved since.

Redact before posting anywhere public: hostnames, IP addresses, serial numbers,
U-code location strings, usernames, internal domain suffixes. The evidence
script's output is already filtered for this; anything you add by hand is not.
