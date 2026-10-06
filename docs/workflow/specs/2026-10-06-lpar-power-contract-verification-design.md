# LPAR power and lifecycle contract verification (V2c)

Issue #1346 (split from #626, epic #620). Pattern: #627 / PR #1320, #1345 / PR #1366.

## Problem

The power and lifecycle operations below carry a bulk `operations.json` assignment
naming commands none of them issues (`chlparstate`, `migrlpar`, `lpar-netboot`, a
`ChangeDefaultProfileName` job). Live evidence comes only from the bare-cec arm (ST25,
rows 35), which proves the happy path of create, power on, power off, console and
delete. Nothing exercises create's refusals, delete's activated-state refusal,
power-on of a running partition, the composite `lpar.power`, `lpar.decommission`, or a
real `provision.lpar` (ST13 is a dry run; ST14 re-provisions the test partition and is
not this arm's to run).

## Operations in scope

Selector resolution, the ADR 0092 ownership read and the `change_location` read are
shared plumbing and are not bound, as in PR #1320 and PR #1366.

| Operation | Rows bound (issued requests) |
|---|---|
| `lpar.create` | `rest:managed-system/logical-partition` (PUT); fallback `cli:commands/mksyscfg`, `cli:commands/chsyscfg` (apply, ownership stamp), `cli:commands/lssyscfg` (CLI name) |
| `lpar.delete` | `rest:managed-system/logical-partition` (quick-property GET, DELETE) |
| `lpar.decommission` | `rest:managed-system/logical-partition` (GET, DELETE), `rest:managed-system/virtual-i-o-server` (mapping inventory), `rest:managed-system/logical-partition/client-network-adapter`, `…/virtual-scsi-client-adapter`, `…/virtual-fiber-channel-client-adapter`, `…/virtual-nic-dedicated` (adapter list and DELETE), `rest:jobs/logicalpartition-jobs/poweroff_logicalpartition-job` |
| `provision.lpar` | `lpar.create`'s rows, `…/client-network-adapter` (PUT), `rest:managed-system/virtual-i-o-server` (mapping POST), `rest:jobs/logicalpartition-jobs/poweron_logicalpartition-job`; `composite_reason` cleared |
| `lpar.power_on` | `rest:jobs/logicalpartition-jobs/poweron_logicalpartition-job`, `rest:managed-system/logical-partition-profile` (profile feed) |
| `lpar.power_off`, `lpar.dump_restart` | `rest:jobs/logicalpartition-jobs/poweroff_logicalpartition-job` |
| `lpar.capture_console` | `cli:commands/mkvterm`, `cli:commands/rmvterm`, `cli:commands/lssyscfg` |
| `system.power_on` / `system.power_off` | `rest:jobs/managedsystem-jobs/poweron_managedsystem-job` / `…/poweroff_managedsystem-job` |
| `lpar.power` | composite (ADR 0189, 0199); unchanged, no rows |

Task 1 confirms each row against the code before writing it; a request the code does
not issue is not bound. Delegates of `provision.lpar`'s assignment step
(`pcie.assign_dedicated_slot`, `sriov.assign_logical_port`, `vnic.add`) keep their
own records (#630).

## Design

1. **Arm `lpar-power`, subtask 41** (orchestrator-assigned). `SUBTASKS[41]`
   dispatches `exercise_lpar_power` in a new `scripts/live_test/lpar_power.py`;
   `SUBTASK_GROUPS["lpar-power"] = [41]`, not in `all`; the subtask SKIPs unless
   `state.group == "lpar-power"`. Wrapper `scripts/live_lpar_power.py`, tested by
   `tests/scripts/test_live_lpar_power.py`; arm behaviour in
   `tests/test_live_lpar_power_arm.py` over a scripted client. Rows are numbered 41,
   scenario `st41-lpar-power`.
2. **Run-owned names.** Prefix `hmcpctl-live-pwr-` (reserved; recovery reports any
   partition carrying it), suffix 8 hex: partition A `hmcpctl-live-pwr-<hex>`,
   provision partition P `…-<hex>-p`, caller token `lparpwr-<hex>`, logical volume
   `lppwr<hex>` (13 characters). The arm creates nothing while any prefixed partition
   or `lppwr`-prefixed volume exists. Every mutating call names a run partition by
   UUID once it has one; create, provision and decommission name the system.
3. **Baseline.** Before any write: `lssyscfg -r lpar -F name`, available processing
   units and memory (the #1345 reads, reused from `lpar_config._read_pools`), VIOS
   storage mappings, the configured volume group's volume names. After teardown the
   same reads are compared (one 30 s re-read on a difference) and recorded as
   `system baseline compare`. A failed baseline read SKIPs the arm.
4. **Create (A)** — observation `lpar.create`:
   `units-over-vcpus-refused` (desired 1.5 units, 1 virtual processor: refused with
   "virtual processor uses at most 1.0"), `memory-over-configurable-refused` (desired
   memory 64 TiB: refused naming "configurable memory"), `refused-creates-left-nothing`
   (neither name listed), `resources-read-back` (memory min/desired/max and shared
   units/virtual processors read from `hmc_get_lpar` equal the request),
   `ownership-stamped` (the description's caller token is the run's),
   `duplicate-name-refused` (a second create of A's name fails with "already exists"
   and A's UUID is unchanged). Resources as #1345's `RESOURCES`.
5. **Power on (A)** — observation `lpar.power_on`: `profile-activation-reached-firmware`
   (profile UUID from `AssociatedPartitionProfile`, `boot_mode=sms`, `wait=True`; job
   successful and state `open firmware` or `running`), `running-reported-without-job`
   (a second call with `boot_mode=of`: `already_running` true, `job` null, message names
   the unapplied boot mode, state unchanged), `current-configuration-reached-firmware`
   (after step 7's power-off: no profile, `boot_mode=of`, `operation_type=activate`,
   `keylock=norm`). No configuration change precedes either activation, so #1345's
   profile-discards-current-configuration finding does not affect them.
6. **Delete refused while activated, console.** `hmc_delete_lpar` on running A must
   fail with "must be 'not activated'" and leave A listed (feeds `lpar.delete`).
   `hmc_capture_lpar_console` (30 s, idle 30 s) — observation `lpar.capture_console`:
   `console-captured`, `console-released`.
7. **Power off (A)** — observation `lpar.power_off`: `delayed-shutdown-not-activated`
   (`immediate=False`, `wait=True`), `immediate-shutdown-not-activated` (after step 5's
   third activation).
8. **Composite (A)** — observation `lpar.power`, one `request_id` per call
   (`lparpwr-<hex>-<n>`), `wait_seconds=600`, then `hmc_operation_status` until
   `terminal` (bounded 10 polls, 30 s): `start-completed-activated`,
   `restart-immediate-completed-activated`, `stop-immediate-completed-not-activated`,
   `repeat-stop-already-in-state` (a new request id: `already_in_state` true, `job_id`
   null), `same-request-replays` (repeating the stop's arguments and request id returns
   its `operation_id`). Graceful stop needs RMC: a gap.
   Partition A's call order: create cases, profile activation, running re-call, delete
   refusal, console, delayed power-off, current-configuration activation, immediate
   power-off, composite, delete. A failed step that leaves A's state unknown skips the
   remaining A steps to teardown.
9. **Delete (A)** — observation `lpar.delete`: `activated-delete-refused`,
   `partition-kept`, `delete-call-succeeded`, `lpar-name-absent` (listing read twice,
   #1345 `_gone` rule).
10. **Provision (P).** Preconditions, each a SKIP of steps 10–11 naming why: exactly one
    VIOS lists the configured volume group (`LIVE_TEST_VDISK_VOLUME_GROUP_NAME`) with
    1 GiB free; `LIVE_TEST_PROVISION_VLAN_ID` has a virtual network. The arm creates the
    1 GiB volume (`hmc_create_virtual_disk`, a plain row: #1348 owns its record), then
    `hmc_provision_lpar` with P's name, the run token, `VirtualDisk`, the VLAN, small
    resources and `power_on=True`. PCIe argument: included only when the dedicated
    arm's four `LIVE_TEST_DEDICATED_PCIE_*` keys are set, its system is this system,
    and a fresh read shows the DRC index unowned (`lshwres -r io --rsubtype slot`) and
    named by no profile (`lssyscfg -r prof -F lpar_name,io_slots`); otherwise a SKIP row
    naming the gap. Observation `provision.lpar`: `workflow-completed` (every step `ok`),
    `ownership-stamped`, `network-adapter-on-vlan`, `storage-mapping-listed` (a VIOS
    mapping backed by the volume names P's UUID), `partition-activated` (state polled to
    an activated state), and `pcie-slot-owned` only when the argument was sent.
11. **Decommission (P)** — observation `lpar.decommission`: `dry-run-inventoried`
    (`dry_run=True`: `resource_deleted` false, every step `dry_run`, blast radius lists
    the network and vSCSI client adapters and the volume's mapping),
    `dry-run-changed-nothing` (state and adapter list unchanged). Then the arm detaches
    the VIOS mapping (`hmc_detach_storage_mapping`, a plain row) so the real call never
    orphans a VIOS mapping, and runs `immediate=True`: `resource-deleted`,
    `workflow-completed`, `lpar-name-absent`. The volume is then deleted
    (`hmc_delete_virtual_disk`, plain row) once no mapping is backed by it.
12. **Teardown.** Each run partition not confirmed deleted is powered off and deleted by
    UUID only while its description carries the run token (#1345 `_delete` shape); a
    mapping or volume still listed is detached or deleted when it is the run's.
    Anything left records one `MANUAL RECOVERY REQUIRED` row naming the commands.
    Observations are recorded after teardown at literal `record_verified` sites,
    cleanup `passed` only when every run object is confirmed gone and the baseline
    compare holds.
13. **Gaps, recorded as SKIP rows and in the table below, never as calls:**
    `lpar.dump_restart`, SR-IOV and vNIC provision arguments, graceful composite stop,
    system power.
14. **Recovery and preflight.** `live_test_recovery.py` witnesses subtask 41: a
    `hmcpctl-live-pwr-*` partition or an `lppwr*` volume (or a mapping backed by one) is
    stranded and prints the shutdown, `rmsyscfg` and volume-removal commands. Preflight
    lists the arm's mutations.
15. **Catalog.** Rebind the rows above; add maturity records for `lpar.power`,
    `lpar.decommission`, `provision.lpar`, `system.power_on`, `system.power_off`; copy
    each emitted observation unchanged (ADR 0126), replacing the bare-cec observation
    where one exists (one observation per operation); regenerate the runtime projection
    and `docs/tools/`; `CHANGELOG.md`; `docs/live-testing.md` arm section, arm table and
    recovery table.

Defects the run confirms in `src/hmcpctl/operations/lpar/` are fixed here; one in
`client/`, `documents/` or another package is reported to the orchestrator first.

## Live gaps (durable record)

| Case | Prerequisite | State |
|---|---|---|
| `lpar.dump_restart` | a partition with an OS that takes a dump, and operator approval of the platform dump | not run |
| `provision.lpar` SR-IOV and vNIC arguments | operator approval to consume shared SR-IOV adapter capacity | not run |
| `lpar.power` graceful stop / restart | an OS with an active RMC connection | not run |
| `lpar.power_on` network boot | #638 | not implemented |
| `system.power_on`, `system.power_off` | an operator window to power the whole system | not run |

## Success

- The operations in the table carry the rows shown and a maturity record each.
- The eight operations the arm observes carry one live observation each, emitted by a
  run at the branch's final `src/` closure and copied with its emitted result.
- After the run: the partition-name set, available units and memory, VIOS mappings
  and the volume group's volume names equal the pre-run reads; no prefixed partition
  or volume exists (recovery exit 0 for subtask 41).
- Unit tests over the scripted client pin: each assertion's pass and fail reading;
  every mutating call naming a run partition (UUID once known) or run volume; the
  PCIe gate's three refusals; teardown's token check and recovery rows.
- `just verify` and `uv run --no-sync prek run --all-files` pass.

## Failure model

1. **Actors and deployments:**
   - the operator dispatching `scripts/live_lpar_power.py` from the live-test host against
     the V10R3 HMC and the POWER9 boundary system;
   - CI, offline (unit tests with a scripted client).
2. **Invariants and assets:**
   - every partition other than A and P, including the VIOS and the test partition;
   - the VIOS's other volumes and mappings, the volume group, the media repository;
   - the processor and memory pools; I/O slot ownership;
   - `maturity.json` honesty: no observation from a SKIP, a transport success or a job
     submission.
3. **Accepted failure classes:**
   - an interrupted run leaves A, P, the volume or its mapping; cost bounded to one small
     partition and 1 GiB; recovery names them by prefix with the commands;
   - a concurrent change by another operator, or pool accounting that lags by more than
     one re-read, fails the compare; the run is re-run, never patched;
   - the volume-group read-modify-write (#936) and an unpaired server adapter after a
     failed map (#1237) are #1348's classes; this arm's compare reports them;
   - a VIOS server adapter left by the detach shows as a mapping-compare FAIL and a
     manual recovery row; the arm never removes VIOS adapters.
4. **Covered elsewhere:** configuration and DLPAR (#1345); dedicated-slot operations
   (#630); storage operation records (#1348); network boot (#638); migration and remote
   restart (#631); authorization semantics (ADR 0092, 0189).
