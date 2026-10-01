# Recovery check witnesses every arm's test-partition residue (#1249)

## Problem

`scripts/live_test_recovery.py` is run after every live run, but it witnesses only the
dedicated PCIe arm. Given any other arm's results document, `inputs_from_document` returns
`None` and `main` exits 0 — "that run created nothing to recover" — a clean verdict on a
system nobody looked at. The vMedia arm changes the configured test partition's power state
and pending boot string, mounts optical media to it, can leave a VIOS vSCSI server adapter with
no mapping (#1237), and creates a media repository. None of that is read.

## Decision

Every arm's results document already records what this check needs, in structured form:

- `config.system_name` and `config.lp3_name` — the run's configured test partition. This is
  "the run's configured test LPAR" the issue keys on; no new flag or artifact field is needed.
- `artifacts.vios_uuid`, `artifacts.vios_partition_id`, `artifacts.vg_uuid` — the VIOS and
  volume group the vMedia arm used.
- `run.subtasks` — every subtask the run dispatched, written unconditionally (ADR 0162).
- `results` rows — `{subtask, tool, status, data}`; a row's `tool` label opens with the MCP
  tool name, and a `SKIP` row is a call never made.

The check reads those, decides which residue classes the run could have left, and reads each
one live through the existing read-only call path.

| Class | Applies when the document has | Clean when the live read shows |
|---|---|---|
| test partition running | a non-`SKIP` ST20 `hmc_power_on_lpar` row | state `Not Activated` |
| boot string drift | a non-`SKIP` ST20 `_SET_BOOT_ORDER_STEP` row | pending boot string equals ST20's baseline row |
| optical mapping left | a non-`SKIP` ST19/ST20 `hmc_mount_optical_media` row | no optical mapping to the test partition backed by the run's ISO |
| unmapped server adapter | a non-`SKIP` ST19/ST20 `hmc_mount_optical_media` row | no more vSCSI server adapters toward the test partition than mapped ones |
| media repository left | a `PASS` ST16/ST17 `hmc_create_media_repository` row | no repository in the run's volume group |

The baseline is the `data.pending_boot_string` of the ST20 row labelled
`hmc_read_lpar_boot_order (baseline)`; `vmedia.py` gains a constant for that label so the
check imports it rather than respelling it, as it already imports `_SET_BOOT_ORDER_STEP`.
The run's ISO names are `config.iso_media_name` and `artifacts.vmedia_iso_name` when set.

The adapter check runs the #1237 listing,
`lshwres -r virtualio --rsubtype scsi -m <system> --level lpar --filter lpar_ids=<vios id>
-F slot_num,remote_lpar_name`, and counts rows naming the test partition against the distinct
server adapters (`id` before `/`) in `hmc_list_storage_mappings` for it. Mappings carry an
adapter name, not a slot, so a count is the honest comparison; the finding names every slot
and the listing to compare by hand. Capturing the REST shape that would join them is #1250.

**Coverage is explicit.** Subtasks 16–22 are witnessed by the table above and 24–25 by the
existing PCIe checks. Every other dispatched subtask is listed as `NOT WITNESSED`, and the
runbook says what each of those arms changes and how to check it by hand.

**Exit codes.** 0: every dispatched subtask is witnessed and read clean. 1: something is
stranded; each finding prints its clearing command. 2: a read failed, an applicable class
lacks the document field it needs, or the document records no `run.subtasks`. 3: everything
witnessed read clean, but the run dispatched subtasks this check does not witness. A finding
outranks a not-witnessed list (1 over 3); an unreadable class outranks both (2), and still
prints the findings already confirmed.

**Read-only guard.** `_READ_ONLY_TOOLS` gains `hmc_read_lpar_boot_order`,
`hmc_list_optical_mappings`, `hmc_list_storage_mappings` and `hmc_get_media_repository`, each
`effect="read"`. `hmc_run_command` is admitted only for commands opening with `lssyscfg ` or
`lshwres `, and only when the command carries none of `; | & $ \` < > ( )` or a newline: the
listing now interpolates a system name from a results document, which the operator does not
author by hand, so the guard refuses a command that could chain a second one.

Remedies are printed, never run (ADR 0162): `hmcpctl lpars power-off`, `hmcpctl lpars
set-boot-order`, `hmcpctl storage unmount-optical-media`, `hmcpctl storage delete-media` then
`delete-media-repo`, and `chhwres -r virtualio --rsubtype scsi -o r --id <vios id> -s <slot>`
(`docs/refs/hmc-commands-p10/commands/chhwres.md:34`) after comparing slots with mappings.

## Failure model

1. **Actors and deployments** — one operator at a terminal on a host with HMC reach, after a
   live run, holding that run's results document. Never CI (AGENTS.md).
2. **Invariants and assets at stake** — the script never mutates the managed system; exit 0
   is never printed for state nobody read; a results document is trusted to be the one the
   runner wrote, but its strings never reach a command unquoted.
3. **Accepted failure classes** — a foreign partition's or operator's optical mapping backed
   by an ISO of the same name is reported (the test partition is configured for the run;
   reporting is not acting). A pre-existing unmapped server adapter toward the test
   partition is reported as residue (it is residue either way, and the operator decides).
   Boot-string or power changes by a run that recorded no ST20 row are not judged (no row,
   no call was made).
4. **Covered elsewhere** — remediation: unowned, excluded. Joining adapters to mappings by
   REST shape: #1250. Residue of round2, SR-IOV, and ad hoc windows: reported as
   `NOT WITNESSED`; extending coverage is unowned.

## Threat model

1. **Boundaries** — widened: the read-only guard on `hmc_run_command` now admits a second
   command family. Added: a command string built from results-document values (system name,
   VIOS partition id).
2. **Actors** — the local operator, who chose the document; the document's writer, the
   runner, from `.env` the operator controls. No remote or tenant actor reaches the script.
3. **Controls** — the system name is `shlex.quote`d and the VIOS id must be an `int`; the
   guard refuses any command not opening with `lssyscfg ` or `lshwres `, or carrying a shell
   metacharacter, before it is sent; tools stay on an allowlist whose members are `read`.
4. **Out of scope** — a hostile results document beyond what the guard refuses: the operator
   wrote it with their own run.

## Considered & rejected

- **A `--lpar` flag.** judgment: the document already records the configured partition the
  run used; a second source can disagree with it.
- **New `LiveTestArtifacts` fields recording each change.** verified: `_decode_artifacts`
  (`scripts/live_test_runner.py:1325-1335`) rejects a document whose fields differ, so every
  existing results document would stop restoring; the rows already record each call.
- **Absolute expectations (test partition always off).** verified: ST14 provisions it with
  `power_on=True` (`scripts/live_test/provisioning.py:186`), so a clean round2 run would read
  as stranded.
- **Exit 2 for not-witnessed subtasks.** judgment: every round2 run would exit 2, and exit 2
  would stop meaning "a read failed".
- **Narrowing the runbook only.** judgment: the issue prefers the checks; the honest-interim
  text is kept for what the checks still do not witness.

## Validation

Focused tests in `tests/scripts/test_live_test_recovery.py`, all through the guarded stub
call path:

| Contract | Mode | Case |
|---|---|---|
| each class detected, and clean when not | focused-test | one stranded and one clean case per class |
| class applies only on its trigger row | focused-test | a `SKIP` trigger row makes no read |
| unreadable or missing input is exit 2 | focused-test | failed read, missing `vios_uuid`, missing baseline |
| exit codes 0/1/2/3 and precedence | focused-test | `main` over documents for vmedia, round2, dedicated, no `run` |
| guard admits the new reads only | focused-test | new tools pass; `lshwres` passes; metacharacters refused |
| new tools are `read` and served | focused-test | registry effect check; existing served-composition test |
| PCIe behaviour unchanged | focused-test | existing cases stay green |
| runbook invocation | focused-test | existing `tests/test_live_testing_doc.py` flags test |
