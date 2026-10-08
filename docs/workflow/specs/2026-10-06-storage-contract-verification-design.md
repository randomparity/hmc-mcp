# Storage and cluster contract verification (V4b, #1348)

Part of #628 (epic #620). Pattern: #627 / PR #1320, #1347 / PR #1364.

## Problem

Twelve storage and cluster operations have no maturity record. Their
`operations.json` rows are a bulk family assignment naming SSP monitoring, reserved
storage pools, persistent-memory volumes and cluster jobs that none of them issues.

No live scenario exercises a virtual disk's lifecycle. ST14 creates one only as
setup for a full re-provision that deletes the test partition. It declares the
disk create's HTTP 406 an expected outcome, so a refused create reads as a SKIP.
That 406 predates the read-modify-write volume-group writes (#936); a live attach
on 2026-09-30 (#1030) created and mapped a disk through the same path. ST3 reads
volume groups, clusters and pools only through `state.record`, which never
promotes.

## Operations in scope

| Operation | Requests issued | Rows bound | Implemented variant |
|---|---|---|---|
| `storage.list_volume_groups` | VolumeGroup feed GET | `rest:virtual-storage-management/volume-group` | `by-vios` |
| `storage.create_volume_group` | VolumeGroup feed PUT (the feed GET only as a readback after a 5xx) | `rest:virtual-storage-management/volume-group` | `from-physical-volumes` |
| `storage.create_disk` | VolumeGroup GET, read-modify-write POST | `rest:virtual-storage-management/volume-group` | `whole-gib-in-volume-group` |
| `storage.delete_disk` | VIOS `ViosSCSIMapping` GET, VolumeGroup GET and POST | `rest:managed-system/virtual-i-o-server`, `rest:virtual-storage-management/volume-group` | `unmapped-disk` |
| `storage.map` | VIOS `ViosSCSIMapping` GET and POST | `rest:managed-system/virtual-i-o-server` | `virtual-disk`, `physical-volume` |
| `storage.detach_mapping` | VIOS `ViosSCSIMapping` GET and POST | `rest:managed-system/virtual-i-o-server` | `by-mapping-id` |
| `storage.attach_disk` | `create_disk`, then `map` | both rows above | `create-and-map-virtual-disk` |
| `cluster.list` | Cluster feed GET | `rest:cluster` | `console-wide` |
| `cluster.list_pools`, `cluster.get_pool` | SharedStoragePool feed or entry GET | `rest:cluster/shared-storage-pool` | `console-wide`, `by-uuid` |
| `cluster.create_logical_unit`, `cluster.delete_logical_unit` | Cluster `CreateLogicalUnit` / `DeleteLogicalUnit` job | `rest:jobs/cluster-jobs/createlogicalunit_cluster-job`, `…/deletelogicalunit_cluster-job` | `job` |

Selector resolution, the ownership guard's partition read and the `change_location`
read are shared plumbing and are not bound, as in PR #1320 and PR #1364.
`storage.list_mappings` is #1347's record; this change only reads through it.

## Design

1. **A `storage` arm.** `SUBTASK_GROUPS["storage"] = [0, 3, 40]`, dispatched by
   `scripts/live_storage.py`. ST0 resolves the VIOS and its partition id; ST3 reads
   the inventory; ST40 is the lifecycle. `all` is unchanged (`range(26)`), so ST40
   runs only in its own arm. ST14 is not part of it.
2. **ST3 promotions** (scenario `st3-storage-inventory`, cleanup `not-required`).
   - `storage.list_volume_groups`: `configured-group-listed`, `groups-match-vios`
     (the listed group names equal the names the VIOS itself reports through
     `viosvrcmd -m <system> --id <vios id> -c lsvg`, one per line), and
     `no-free-space-diagnostic` (no group's free space was discarded as exceeding
     its capacity). The parser already refuses an entry without a UUID or name, so
     no assertion restates that.
   - `cluster.list` and `cluster.list_pools` read every cluster and pool the HMC
     manages, on any system. With entries: `clusters-identified` (UUID and
     `ClusterName`), `pools-identified` (UUID and `StoragePoolName`). An empty
     feed proves no entry shape and stays a plain row, as an empty media listing
     does in the vmedia arm.
   - `cluster.get_pool` (scenario `st3-pool-read`): a freshly generated UUID is
     answered with no pool — null, or a failure naming 404 or not found:
     `absent-pool-not-returned`. When a pool is listed, the first one is read back
     too: `listed-pool-returned` (the same UUID).

   ST3 still runs in `round2`. The VIOS `lsvg` read needs `vios_partition_id`;
   without it the volume-group row stays plain.
3. **ST40 — disk lifecycle** (scenario `st40-disk-lifecycle`). The run creates one
   1 GiB logical volume named `hpctl<8 hex>` (13 characters; the VIOS limit is 15)
   in the configured volume group (`LIVE_TEST_VDISK_VOLUME_GROUP_NAME`). The name
   is stored in a new artifact, `storage_disk_name`, before the create call and
   cleared only when a listing shows the volume absent.

   Preconditions, each a SKIP naming why: the VIOS UUID and integer partition id;
   the configured group listed with at least 1 GiB free; the test partition not
   protected and `Not Activated`. A `storage_disk_name` restored from an earlier
   document is checked against the volume listing: still listed is a SKIP naming
   the residue; absent clears it.

   Baseline, all read before the create (a failed read SKIPs the scenario):
   - the group's free space (`hmc_list_volume_groups`);
   - the group's logical-volume names, independently through the VIOS CLI
     (`viosvrcmd -m <system> --id <vios id> -c 'lsvg -lv <group>'`; format
     captured 2026-09-30, #1030);
   - every VIOS storage mapping as (id, partition UUID, backing kind, backing name);
   - the vSCSI adapter rows of the VIOS and of the test partition (vmedia's
     `scsi_adapter_listing`).

   Steps:
   1. `hmc_create_virtual_disk`. `storage.create_disk`: `create-accepted`,
      `volume-listed`, `free-space-reduced` (by at least the capacity),
      `baseline-volumes-kept`. Cleanup `passed` only when step 5 restores the
      volume names and free space. No 406 or other refusal is declared expected: a
      refused create is a failed observation. Whatever the call returned, the
      volume listing decides what follows: listed continues (or cleans up),
      absent clears the artifact and ends the scenario, unreadable is a manual
      recovery row.
   2. `hmc_map_storage_to_lpar` (`VirtualDisk`, the run's volume, the test
      partition, `system_name_or_uuid`). Read the mappings. `storage.map`:
      `map-accepted`, `mapping-listed` (an entry backed by the volume names the
      partition's UUID), `baseline-mappings-kept`. Cleanup from step 4.
   3. `hmc_delete_virtual_disk` while mapped, only when step 2's `mapping-listed`
      held (otherwise the guard's view of the mapping is unproven and the step is
      skipped): refusal expected, and the volume still listed. The refusal is hmcpctl's guard, raised before any write; the
      assertion pins the guard's reading of the live mapping shape.
   4. `hmc_detach_storage_mapping` with the listed mapping id. Re-read mappings,
      adapters and volumes. `storage.detach_mapping`: `detach-accepted`,
      `mapping-absent`, `mappings-equal-baseline`, `adapters-equal-baseline`,
      `volume-survives`.
   5. `hmc_delete_virtual_disk`. `storage.delete_disk`: `refused-while-mapped`
      (only when step 2 mapped), `delete-accepted`, `volume-absent`,
      `volumes-equal-baseline`, `free-space-restored`.

   A mapping that cannot be confirmed absent, or adapters or mappings differing from
   the baseline after the detach, record `MANUAL RECOVERY REQUIRED` with the
   command that clears it and stop the scenario. A map that fails is followed by a
   mapping and adapter re-read; with nothing listed the volume is deleted. The arm
   never removes an adapter, and deletes the volume only after a listing shows no
   mapping backed by it (step 3 is the one deliberate exception, and only when the
   mapping is listed).
4. **ST40 — attach** (scenario `st40-attach-disk`). Runs only when the lifecycle
   left `storage_disk_name` clear, with a fresh name and the same baseline.
   `hmc_attach_disk_to_lpar` (1 GiB). `storage.attach_disk`: `workflow-completed`
   (both steps `ok`), `volume-listed`, `mapping-listed`. Then detach and delete
   as plain rows; cleanup `passed` only when volumes, mappings, adapters and free
   space equal the baseline. The tool reports a failed step as `error` rather than
   failing, so cleanup reads the volume and mapping listings, not the result: a
   created disk with no listed mapping is deleted.
5. **ST14.** The `_VOLUME_GROUP_POST_UNSUPPORTED` declaration is removed, so a
   refused create is a FAIL. ST14's old-disk removal is a follow-up, not changed
   here: it runs `rmvlog`, the VIOS virtual-log command, with `-vg`/`-lv` options,
   and passes the VIOS UUID to `viosvrcmd -p`, which takes a partition name. Its
   expected outcome matches "not found", so the step can never have removed a
   disk. Replacing it needs a live-verified destructive command in a scenario this
   arm does not run.
6. **Recovery.** A storage run (subtask 40 dispatched) is checked for:
   - `run disk mapping left`: a VIOS storage mapping backed by an `hpctl<8 hex>`
     volume;
   - `run disk left`: an `hpctl<8 hex>` volume in the configured group, read with the
     same `lsvg -lv` command. `guard_read_only` admits exactly that command shape
     (system and group names from a closed character set, an integer VIOS id);
   - `unmapped server adapter`: the existing class, now also applied to a storage run.

   Subtasks 0, 3 and 40 join the witnessed set (0 and 3 only read), so a clean
   storage run reports clean rather than unwitnessed.

   Names are matched by prefix, as the users arm does, so a run killed before it
   wrote its document is still found.
7. **Preflight** names the arm's mutations: per scenario one 1 GiB volume
   `hpctl<8 hex>` in the configured group, mapped to the test partition only while
   Not Activated (the HMC adds a vSCSI adapter pair), a delete while mapped expected
   refused, detached, deleted; no other volume or mapping is changed.
8. **Runner.** One nullable-string artifact, `storage_disk_name`.
9. **Catalog.** Rebind the rows as tabled; twelve maturity records with the variants
   above; copy the run's observations (ADR 0126). Regenerate the runtime projection
   and `docs/tools/`.

## Live authorization and snapshot

The operator authorized logical-volume and mapping mutations on the boundary VIOS
for objects the run creates (relayed by the campaign orchestrator, 2026-10-06). The
operator's own logical volume, its virtual target device, the volume group and the
media repository are never changed.

Before preflight and after the recovery check, one read-only session captures: the
VIOS's volume groups and their free space, each group's logical volumes, free
physical volumes, `lsmap -all`, the vSCSI adapter rows, both partitions' profile
vSCSI lists, and the test partition's state. The two captures are compared line for
line; the raw output stays private.

## Live gaps

| Case | Prerequisite |
|---|---|
| `storage.create_volume_group` | a free physical volume and an authorization to build a group on it |
| `storage.map` with `PhysicalVolume` | a free physical volume mapped whole to the test partition (not authorized) |
| `cluster.create_logical_unit`, `cluster.delete_logical_unit`; `cluster.list`, `cluster.list_pools` and a positive `cluster.get_pool` when the HMC manages no cluster | a shared storage pool; the boundary system's internal SAS disks are not cluster-aware and it has no FC adapter |
| map / detach on an activated partition | a running test partition (dynamic reconfiguration; not exercised) |

## Success

- Rows bind as tabled; `just capability-inventory` passes.
- Twelve maturity records; live ones carry the run's emitted observations, `failed`
  where an assertion failed. No SKIP or plain row becomes an observation.
- After the run the snapshot equals its pre-run read.
- Unit tests pin each precondition, the run-owned-name rule, the refusal and survive
  assertions, the stop on residue, the recovery classes and guard, and the
  preflight disclosure.
- `just verify` and `uv run --no-sync prek run --all-files` pass.

## Failure model

1. **Actors and deployments:** the operator running `live_storage.py` from the
   live-test host against the V10R3 mutation-boundary system; CI, offline.
2. **Invariants and assets:** the operator's volume, VTD, volume group and media
   repository; every other mapping on the VIOS; the VIOS's server adapters; both
   partitions' profiles; the test partition's state; catalog truth.
3. **Accepted failure classes:**
   - each disk create and delete rewrites the operator's whole VolumeGroup document
     (#936); the volume-name and free-space compare reports a lossy round trip;
   - a map that fails after the HMC added an adapter can leave one unpaired (#1237);
     the adapter compare and the recovery class report it with the `chhwres` remedy;
   - an interrupted run can leave the run's volume or mapping; recovery finds it by
     prefix and the operator removes it by its run-unique name;
   - the read-modify-write on the VIOS and volume group loses a concurrent writer's
     change (ADR 0079, 0171), accepted for a single-operator lab window.
4. **Covered elsewhere:** SSP lifecycle (#658); SSP PCM (#651); media (#1347);
   environment isolation (#461).

### Threat model

- Boundaries: tool arguments and CLI commands built from configuration, HMC
  listings and the run's generated name.
- Controls: `shlex.quote` on every interpolated value; the VIOS id is an integer;
  the volume name is generated hex; the recovery guard admits one exact `lsvg -lv`
  shape built from a closed character set.
- Out of scope: a hostile HMC.

## Live result (2026-10-07, V10R3 / POWER9 boundary system)

Run at `3d3f76d4`. Preflight passed, the run completed with 61 rows (59 PASS, 2 FAIL,
0 SKIP), and it emitted seven observations, all `passed`. The two FAIL rows are the
refusals the scenarios expect, and the observations assert both:

- the absent-pool read was answered HTTP 404 `REST029B`;
- the delete while mapped was refused by hmcpctl's guard before any write.

The before snapshot showed the configured group with well over 2 GiB free and the test
partition `Not Activated`. The VolumeGroup read-modify-write create succeeded, so ST14's
406 expectation was stale, and removing it is confirmed. Each mapping added a new adapter
pair, and the detach removed it again.

Recovery exited 0 (CLEAN). The before and after read-only snapshots were byte-identical.

| Operation | Result |
|---|---|
| `storage.list_volume_groups`, `storage.create_disk`, `storage.map`, `storage.detach_mapping`, `storage.delete_disk`, `storage.attach_disk` | passed |
| `cluster.get_pool` | passed (absent-pool branch only; a positive read is the gap above) |
| `cluster.list`, `cluster.list_pools` | unevidenced: the HMC manages no cluster or pool |
| `storage.create_volume_group`, `cluster.create_logical_unit`, `cluster.delete_logical_unit` | unevidenced (gaps above) |

## ST42 scratch volume-group extension (#1370, 2026-10-08)

Expected implementation size is 950–1150 changed lines (M250 unchanged). Actual
implementation is 1007 lines: required truthful-failure and destination-preservation
regressions corrected the earlier 800–950 estimate without expanding the contract.

The operator approved one create/delete on one released physical volume on
2026-10-07 and explicitly approved paired optional settings on 2026-10-08:
`LIVE_TEST_SCRATCH_PV_NAME` and `LIVE_TEST_SCRATCH_VG_NAME`. Both default empty;
ST42 SKIPs unless the PV matches `hdisk[0-9]+` and the group `hpvg[0-9a-f]{8}`.
Preflight names both exact values without creating state. The operator chooses a
fresh group name for the one run; that name is absent before creation. ST42 joins
only the storage arm (`[0, 3, 40, 42]`), with no other arm changed.

Ownership remains in the live harness: a new `storage_volume_group.py` composes
existing `vios_command`, `volume_group_names`, and `volume_names`. Product create
uses ADR 0136 unchanged; a product volume-group deletion operation is explicitly
excluded and unowned. No new product API or dependencies. Existing result decoders explicitly default
the new optional fields when reading pre-ST42 documents.

1. Snapshot VIOS `lspv -field pvname pvid vgname -fmt :`,
   `lspv -free -field pvname pvid size -fmt :`, and `lsvg`, plus REST group names.
   Parsers require unique complete rows; free-mode exact `":"` also means empty.
   REST/VIOS groups must agree; the PV must occur in both PV listings with the same
   real PVID, positive free size and VG `None`; scratch group absent. Missing settings,
   missing VIOS identity, previous outstanding artifact or valid refused preconditions
   SKIP before mutation. Failed reads remain FAIL; malformed authoritative snapshots
   record explicit validation FAIL without mutation. No disk selection, fallback or create retry.
2. Save `artifacts.storage_volume_group_name` before one
   `hmc_create_volume_group` using exactly `[scratch_pv_name]`. Re-read the
   snapshot regardless of the call status. Unknown poststate records manual
   recovery and stops. Absence in both group listings ends the scenario and
   compares the snapshot; a failed create never becomes a passed observation.
3. For a listed scratch group, cleanup requires the complete PV inventory to
   differ only by the selected PV joining this group with its original PVID,
   `lsvg -pv <group> -field pvname -fmt :` to name exactly that PV, and
   `lsvg -lv <group>` to name no logical volumes. Only then execute plain
   `reducevg <group> <pv>` through `viosvrcmd -m <system> --id <VIOS id> -c ...`.
   No `-rmlv`, force flag, shell chain or root mode. Unknown membership, extra
   PVs/LVs, or changed identity refuses cleanup with manual recovery.
4. Re-read all snapshot components. Cleanup passes only when REST and VIOS
   prove absence and the complete before/after snapshots match, including the
   raw lspv/free/lsvg strings. Clear the artifact only on that proof. Otherwise
   keep it and report manual recovery; never retry creation or guess a repair.
5. Record `storage.create_volume_group`, scenario `st42-scratch-volume-group`,
   with assertions `create-accepted`, `rest-group-listed`, `vios-group-listed`,
   `selected-pv-only`; cleanup is `passed` only on proven baseline restoration.
   Disabled/refused guard rows do not promote. Read-only recovery runs when ST42
   was dispatched or its artifact is pending, including after subset reruns. It
   reports every `hpvg<8 hex>` REST group on its VIOS and any pending unconfirmed
   restoration even when the REST group is absent, without deletion or repair.
   Subsets restore their existing results destination before legacy fallback reports.
   Strict configuration/HMC rejection returns 1 before dispatch or writes, preserving
   that destination byte-for-byte; incompatible runs require a separately named file.
   Actual runner-to-recovery regressions cover compatible pending state, disabled
   paired settings and HMC-identity mismatch so rejection cannot erase uncertainty.

The existing failure model extends to physical-volume identities and all groups.
Deployment remains the single-operator admitted V10R3/POWER9 lab window. Failed or
interrupted writes may leave residue; unknown state stops, and recovery reports
it. Metadata damage requires operator recovery, never an automatic repair.
Concurrent writers remain an accepted lab-window limitation. Local configuration
and results are untrusted command inputs: PV/group closed patterns, a plain
non-option system name, and a positive integer VIOS ID bound every command.

Offline tests cover every selection/identity/name guard, malformed listings,
failed/partial create, unsafe/unknown cleanup, snapshots, artifact lifetime,
nonpromoting SKIPs, configuration isolation, registry, preflight and recovery.
A controlled free-guard fault must fail its test before restoration. The live run
requires a separate campaign slot naming the pushed exact head and fresh proof
that the historically authorized PV still has the same PVID/size and is free.
Preflight → named storage arm → recovery always; the one create allowance
does not imply a second attempt. Capture raw snapshots privately; use the stamped
evidence generator and canonical maturity/projection/tool-document generators.

Primary CLI sources: local `docs/refs/hmc-commands-p10/commands/viosvrcmd.md:29`;
[IBM POWER9 lspv](https://www.ibm.com/docs/en/power9/9223-42S?topic=commands-lspv-command),
[lsvg](https://www.ibm.com/docs/en/power9/9223-22H?topic=commands-lsvg-command),
and [reducevg](https://www.ibm.com/docs/en/power9/9223-22H?topic=commands-reducevg-command).
The local corpus has no reducevg entry; IBM documents deletion when the last PV
is removed, without the data-deleting `-rmlv` flag.
