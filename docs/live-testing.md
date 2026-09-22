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
2. `~/.config/hmc-mcp/config.toml` — the documented profile
3. a local `.env` file

Scenario settings — every `LIVE_TEST_*` key — come **only** from `.env`. That
file is git-ignored and must stay that way; it names the managed system a run
will create and delete partitions on.

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
    will mutate: dedicated slot 553713664
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

Each writes `test-results-<arm>.json`. Run one arm at a time: they share a
managed system, and a concurrent run makes the recovery check in step 4
ambiguous about which run stranded what.

`scripts/live_test_runner.py` is there for anything these do not cover; see its
`--help`.

### Reading the output

Rows print as they complete. **Row subtask ids go up to 34, while the ids you
can dispatch stop at 24.** That is not a bug: subtask 24 dispatches the whole
dedicated arm, and the arm records its internal phases as rows 26 through 34.
A row numbered 31 is part of the arm you asked for.

A SKIP is a result, not a failure. An arm SKIPs when a precondition is absent —
an out-of-envelope system, no unassigned slot, a capability the HMC refuses —
and that is the arm working.

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

| Exit | Meaning |
|---|---|
| 0 | nothing carrying this run's marker survives |
| 1 | something is stranded; the output names it and the command that clears it |
| 2 | the state could not be read — **this is not clean** |

The check issues no mutating call. When it reports something stranded, run the
command it prints yourself, then run the check again.

It identifies this run's leftovers by the run marker recorded in the results
document. A partition sharing the fixture's name but carrying a different
marker is never attributed to your run, and never reported for you to delete.

## If something goes wrong mid-run

Stop and run step 4. The arm's cleanup refuses to mutate anything whose
ownership it cannot confirm, so an interrupted run leaves its traces in place
rather than deleting something it did not create. That is the safe outcome, and
step 4 is what tells you what is there.

Never hand-delete a partition because its name looks like a fixture. Check the
marker first.

## Recording the result

A live matrix is evidence **only for the commit it ran on**. When quoting one in
an ADR, a pull request, or an issue, quote the commit with it. Before trusting
an existing matrix, check whether the branch has moved since.

Redact before posting anywhere public: hostnames, IP addresses, serial numbers,
U-code location strings, usernames, internal domain suffixes. The evidence
script's output is already filtered for this; anything you add by hand is not.
