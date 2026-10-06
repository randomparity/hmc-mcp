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

round2's provisioning subtasks (13 and 14) need two settings a fresh lab cannot
supply from its own partitions:

- `LIVE_TEST_PROVISION_VLAN_ID` — the VLAN for the provisioned partition's client
  adapter. It must already have a virtual network on `LIVE_TEST_SYSTEM_NAME`; the
  runner never creates one. Subtask 14 lists the virtual networks and stops before
  deleting anything when the VLAN is not there.
- `LIVE_TEST_PROVISION_DISK_MIB` — the size of the test disk subtask 14 recreates,
  in MiB and a multiple of 1024.

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

For round2 it also prints a `provision VLAN:` line. `no virtual network on VLAN
<id>` means subtasks 13 and 14 will fail: set `LIVE_TEST_PROVISION_VLAN_ID` to a
VLAN that has one before running. This line does not change the exit status
either.

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
| profiles | `uv run --no-sync python scripts/live_profiles.py` | subtasks 0, 4, 10 and 15 |

Each writes `test-results-<arm>.json`. Run one arm at a time: they share a
managed system, and a concurrent run makes the recovery check in step 4
ambiguous about which run stranded what.

`scripts/live_test_runner.py` is there for anything these do not cover; see its
`--help`.

### Reading the output

Rows print as they complete. **Row subtask ids go up to 36, while the ids you
can dispatch stop at 25.** That is not a bug: subtask 24 dispatches the whole
dedicated arm, and the arm records its internal phases as rows 26 through 34,
plus its io_slots scenario as row 36. A row numbered 31 is part of the arm you
asked for. Subtask 25 dispatches the
bare-cec arm, which records its own steps as row 35. It reuses the dedicated
arm's baseline, fixture-create and cleanup steps, so rows 29, 30 and 34 appear
in a bare-cec run too, with their dedicated-arm wording.

A SKIP is a result, not a failure. An arm SKIPs when a precondition is absent —
an out-of-envelope system, no unassigned slot, a capability the HMC refuses —
and that is the arm working.

### The io_slots scenario

After its reassign, the dedicated arm answers the #912 profile grammar
questions on its own fixture: row 36. It adds a second slot with
`is_required=1`, adds and removes a third through the assign and unassign
operations, removes the `is_required=1` slot with `//0`, then removes the last
and expects the profile to read `none`.

- It takes **two more slots** than preflight names: the first two, other than
  the arm's own, that no partition owns and no partition profile lists. Only the
  fixture's profile lists them, and only while the scenario runs.
- It SKIPs when `LIVE_TEST_DEDICATED_PCIE_DRC_INDEX` is set, because a pinned
  run mutates only the slot it names, and when fewer than two such slots exist.
- A failed step ends the scenario. The arm then removes the slots it added, and
  its cleanup deletes the fixture only when the profile is back at the baseline.
  Otherwise it prints a manual-recovery row, as for any other drift.

### The profiles arm

The profiles arm verifies the partition-property, profile, memory-pool and
affinity operations (#627). Subtask 10 changes each property it tests and
restores the value it read just before:

- the test partition's description;
- the first VIOS's `msp` flag;
- the processor compatibility mode of the test partition's default profile,
  unless ST0 read its `sync_curr_profile` as 1: the HMC refuses a profile change
  then, so the arm records the round trip as SKIP (subtask 15 skips its restore
  of that mode in every arm on the same condition);
- its `sync_curr_profile` setting, which it touches only while the partition is
  `Not Activated`.

It is the only arm that runs the system-wide profile backup and type-3
merge-restore. It backs up to `hmcpctl-live-st10`, a file in the HMC's
`/var/hsc/profiles/<serial>/` directory. That file stays there and is
overwritten by the next successful backup. The restore runs only when this
run's backup succeeded and both of its `lssyscfg` reads before the restore
passed; otherwise the arm records it as skipped.

The restore resets a not-activated partition's `resource_config` from 1 to 0,
even though it merges a backup taken moments earlier, so its observation fails
on that side effect. The arm then re-applies each such partition's current
profile, which leaves it `Not Activated`. A partition it cannot re-apply (no
current profile, a refused apply, or no `resource_config` in the read after
the restore) is a FAIL row marked `MANUAL RECOVERY REQUIRED` that names the
partition and the `chsyscfg ... -o apply` command to run.

If a run stops partway, restore by hand what it may have left changed. Use
the values in the run's baseline, `artifacts.lp3_baseline` in its results
document, which an interrupted run still writes:

- `chsyscfg -r lpar -m <system> -i "name=<lpar>,description=<original>"`
- `chsyscfg -r lpar -m <system> -i "name=<vios>,msp=<0|1>"`
- `chsyscfg -r prof -m <system> -i "name=<profile>,lpar_name=<lpar>,lpar_proc_compat_mode=<mode>"`
- `chsyscfg -r lpar -m <system> -i "name=<lpar>,sync_curr_profile=<0|1|2>"`
- `chsyscfg -r lpar -m <system> -o apply -p <lpar> -n <profile>`, for a
  not-activated partition whose `resource_config` the restore left at 0

If the restore itself failed or was interrupted, review the profiles before
anything else, then restore them with
`rstprofdata -m <system> -l 1 -f hmcpctl-live-st10`. Do not run the arm
again until the profiles are confirmed: its next backup overwrites that file.

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

### Observations

The SR-IOV and dedicated arms record their verified steps through the same
observation path as the bare-cec arm. Scenarios are `st23-sriov-logical-port`,
`st29-dedicated-pcie` and `st36-io-slots`. A run from a clean committed tree,
with both `LIVE_TEST_ENV_*` keys set, writes them to
`test-results-<arm>-observations.json`. Promote them by hand
(`docs/capabilities/README.md`, "Recording an observation").

An SR-IOV call that changed nothing records no observation. From a profile
that reads `none`, only the assign is observed. To observe the unassign as
well, start with the profile already listing the test port; the baseline check
admits that state. The reassign is never observed: the profile-only unassign
leaves the effective port assigned, so the reassign finds it assigned as asked
and changes nothing.

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

If the matrix opens with **PARTIAL run**, an exception or interrupt stopped the
run. The script still renders it, because the rows it recorded are attributable
to the commit, but the selection line reads `Subtasks selected (not all ran)`
and the time reads `Interrupted`. Cite it as an interrupted run, never as a
complete one, and keep the PARTIAL line when you paste it.

The matrix deliberately omits each row's `data` and `note` and the document's
`hmc` block. Those carry HMC-derived text — hostnames, account names, ISO
names, location codes — and are redacted only on FAIL rows. **Do not paste the
results JSON itself into a pull request, issue, or any public location.**

## 4. Confirm the system is clean

Always, after every arm, including after a run that looked fine. Pass the arm's
own results document:

```sh
uv run --no-sync python scripts/live_test_recovery.py --results test-results-dedicated.json
```

After the other arms pass `test-results-<arm>.json` the same way: `vmedia`,
`bare-cec`, `round2`, `sriov` or `profiles`.

The check reads the subtasks the run dispatched from the document, and witnesses
two sets of them:

| Subtasks | What it reads |
|---|---|
| 16–22 (vmedia) | the test partition left running, its pending boot string changed, the run's ISO still mounted to it, a VIOS vSCSI server adapter toward it with no mapping, and the media repository the run created |
| 24–25 (dedicated, bare-cec) | a partition carrying this run's marker, its dedicated slot still owned, its profile's `io_slots` off the baseline |

It also counts the server adapters after round2's subtask 14 provisions the test
partition. Every other dispatched subtask is printed as `NOT WITNESSED`.

| Exit | Meaning |
|---|---|
| 0 | every dispatched subtask is witnessed and nothing is left behind |
| 1 | something is stranded; the output names it and the command that clears it |
| 2 | some state could not be read, the run dispatched subtasks the check does not witness, or the run was interrupted (`run.partial`), even when something is also stranded — **this is not clean** |

Exit 2 is expected after round2, SR-IOV, profiles and `all` runs: they dispatch
subtasks the check does not witness. For those, check by hand:

- **round2**: the scratch and network-test partitions are gone, the test user is
  gone, no test VLAN or virtual network is left, the test partition's
  description and properties match the baseline, and the provisioned test
  partition and its disk exist.
- **SR-IOV**: the test logical port is no longer assigned to the test
  partition, and its profile no longer lists it.
- **profiles**: compare an independent `lssyscfg` read with one taken before
  the run. That read covers the test partition, every profile on the system
  (`lssyscfg -r prof -m <system>`) and the VIOS `msp` flag. It should match
  line for line as a set: the HMC reorders a partition's profiles after a
  restore. The backup file `hmcpctl-live-st10` is the one expected addition.

The check issues no mutating call. When it reports something stranded, run the
command it prints yourself, then run the check again.

Exit 2 still prints anything it had already confirmed, and every class it could
read, so treat its findings as real and each `NOT READ` line as unknown. A run
that ends on exit 2 has not been shown clean by anything — check the rest of the
system yourself.

The header names the run the document came from: its group, commit and finish
time. A run that an exception or interrupt stopped still writes its results
document, marked `"partial": true` in its `run` block. Its rows end where the
run stopped, so a call cut off mid-flight has no row. On such a document the
header starts with PARTIAL and says when the run was interrupted, the check
never prints CLEAN, and it exits 2 whatever it found. Check an interrupted run
by hand as well.

It identifies the PCIe arms' leftovers by the run marker recorded in the results
document. A partition sharing the fixture's name but carrying a different
marker is never attributed to your run, and never reported for you to delete.
Run the check from the run's tested commit. A partition that carries your marker
in an ownership stamp this checkout cannot parse, such as one written before the
`hmcpctl` rename, exits 2 rather than being reported clean.

The vmedia classes key on the configured test partition (`LIVE_TEST_LPAR_NAME`
as the run recorded it), not on a marker. An optical mapping of an ISO with the
run's name, or an unmapped server adapter toward that partition, is reported
whoever left it.

## If something goes wrong mid-run

Stop. The arm's cleanup refuses to mutate anything whose ownership it cannot
confirm, so an interrupted run leaves its traces in place rather than deleting
something it did not create. That is the safe outcome. A run that finishes,
even with failed rows, writes its results document, and step 4 is what tells you
what is there. A run you interrupt, or one an uncaught error ends, writes a
partial document (`"partial": true` in its `run` block) that holds the baseline
and run marker gathered before the stop. Its rows can miss the call that was in
flight, so also check what its arm changes by hand.

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

## Capturing an HMC's vocabulary

This is a separate, read-only procedure. It records what an HMC answers to every
read hmcpctl makes, so `just live-vocabulary` can check `src/` and `tests/`
against real answers instead of guesses (#1202). It creates, changes and deletes
nothing: a guard refuses any REST method but `GET` (logon and logoff excepted)
and any command not starting with `ls` before it is sent.

The output is raw HMC data: hostnames, serial numbers, location codes, account
names, and answers that name a secret keyword, which the default capture would
have blanked. Write it to a private directory **outside every repository** (the
sweep refuses one inside a git work tree), and never commit or paste it.

1. Sweep one HMC profile. `--system`, `--lpar` and `--vios` are optional; without
   them the sweep picks the first operating system, a running partition on it and
   a VIOS on it.

   ```sh
   uv run --no-sync python scripts/live_capture_sweep.py \
     --out ~/hmc-live-evidence/<date>-<profile> --profile <profile>
   ```

   It writes `sweep.capture.jsonl` and `tools.capture.jsonl`. A tool whose required
   parameter the sweep cannot supply is logged as a `skip` naming the parameter.

2. Tokenize. Pass every lab name, host prefix and site word that could appear in
   the output as `--private`. Collected names are tokenized first, so a partition
   named after a lab system keeps its system token (`sys-R1-lp3`); a `--private`
   match elsewhere replaces the whole word it sits in. The export fails, writing
   nothing, when a match survives or a `<REDACTED-PRIVATE>` token is left joined to
   the rest of a word, and does the same for any collected name, URL host, IP
   address, location code, device id, session value or SSH key.

   ```sh
   uv run --no-sync python scripts/live_capture_export.py tokenize \
     ~/hmc-live-evidence/<date>-<profile>/*.capture.jsonl \
     --out ~/hmc-live-evidence/<date>-<profile>/corpus.json --private '<lab-pattern>'
   ```

   The corpus is still private: it keeps every response body.

3. Derive the committed files, one vocabulary and one enum list per HMC release
   and system, named for both (`v11r2-p11-9824-42a`). `enums` needs the corpus to
   hold the `Enumerations.xsd` read the sweep makes.

   ```sh
   C=~/hmc-live-evidence/<date>-<profile>/corpus.json
   V=tests/fixtures/live/vocabulary
   P=v11r2-p11-9824-42a
   uv run --no-sync python scripts/live_capture_export.py enums "$C" \
     --firmware V11R2 --out "$V/enums-$P.json"
   uv run --no-sync python scripts/live_capture_export.py vocabulary "$C" \
     --enums "$V/enums-$P.json" --firmware "$P" \
     --source '<date> read-only sweep: HMC <release> managing a <family> <model>' \
     --out "$V/$P.json" --private '<lab-pattern>'
   ```

   `--fold <derived.json> --fold-source '<where it came from>'` adds the REST values
   and endpoints of a vocabulary derived earlier from the same pair.

   Read the vocabulary diff before committing it: name-bearing elements and fields
   keep only their shape (`<text>`, `<int>`, `<uuid-upper>`), so a literal that
   looks like a name is a bug in the exporter, not data.

4. Run `just live-vocabulary`. A new capture can retire allowlist entries, which
   the gate then reports as stale; delete them, or regenerate the list with
   `uv run --no-sync python scripts/check_live_vocabulary.py --write-allowlist`,
   which keeps the reasons of entries that still apply. Give every new entry a
   reason that cites #1202. A test value that is deliberately not an HMC answer
   is not allowlisted: end its line with `# live-vocabulary: allow <reason>`.
