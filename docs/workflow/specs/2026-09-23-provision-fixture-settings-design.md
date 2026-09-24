# Live-test provisioning takes its VLAN and disk size from `.env`

Issue #970. Part of #871.

## Problem

round2 ST13 (provision dry run) and ST14 (storage lifecycle and live provision) read two
values from lab fixtures that a freshly prepared lab does not have:

- **VLAN.** ST13 uses the test partition's first client network adapter PVID
  (`lp3_baseline["pvid"]`), else `test_vlan_id`. `test_vlan_id` is chosen by ST9 as an
  *unused* VLAN in `LIVE_TEST_VLAN_RANGE_*`, so it never has a `VirtualNetwork`, and
  `hmc_provision_lpar` refuses it. ST14 has no fallback at all.
- **Disk size.** ST14 uses `vdisk_size_mib`, which ST3 reads from an existing virtual disk
  named `LIVE_TEST_VDISK_NAME`. The `VolumeGroup` projection that `hmc_list_volume_groups`
  returns carries no disks (#967's spec), so this value is never set from a projection.

In the #879 window ST13 failed with `No VirtualNetwork with VLAN ID 3100 found` and ST14
stopped at its pre-flight with `Missing required context keys: ['pvid', 'vdisk_size_mib']`.

## Design

**Two required `.env` keys** in `LiveTestConfig._CONFIG_FIELDS`, so `from_env_file`
rejects a file without them, as it does every other scenario key:

| Key | Field | Validation |
|---|---|---|
| `LIVE_TEST_PROVISION_VLAN_ID` | `provision_vlan_id` | integer, 1–4094 |
| `LIVE_TEST_PROVISION_DISK_MIB` | `provision_disk_mib` | integer, positive multiple of 1024 |

The VLAN must already have a virtual network on `LIVE_TEST_SYSTEM_NAME`; the runner never
creates one. The 1024 rule is the one `build_virtual_disk_element` enforces (the HMC takes
whole GiB), moved to load time as #963 did for the media-repository sizes. Both keys end in
suffixes `from_env_file` already parses as integers (`_ID`, `_MIB`).

**ST13** sends `port_vlan_id=config.provision_vlan_id`. The `pvid` fallback chain and its
SKIP reason go. It still SKIPs when there is no VIOS UUID.

**ST14** sends `port_vlan_id=config.provision_vlan_id` and
`capacity_mib=config.provision_disk_mib`. `pvid` and `vdisk_size_mib` leave its pre-flight
list; `vios_uuid`, `vg_uuid`, `vios_slot` and `vios_pid` stay. `vg_uuid` still comes only
from `storage.configured_vg_uuid`, #967's resolver. No second volume-group lookup is added.

ST14's VLAN used to be the test partition's own, so it always had a virtual network. A
configured one may not, and `hmc_provision_lpar` checks it only after ST14 has deleted the
test partition and disk. So ST14's pre-flight now calls `hmc_list_virtual_networks` and
FAILs before any delete unless the configured VLAN is listed. A failed listing or an
unparsable VLAN identifier FAILs it too. The parsing is `network.listed_vlans(data)`,
extracted from ST2's `_unused_vlan` so both read VLANs one way.

**Dead path removed.** Nothing reads `artifacts.vdisk_size_mib` after this, so the field,
its entry in `_ARTIFACT_NULLABLE_INTS`, ST3's disk-capacity capture (`_virtual_disks`,
`_capture_disk_capacity`, the `parse virtual disk capacity` FAIL row) and their tests go.
ST3 keeps resolving the configured volume group.

**Preflight VLAN check.** When the selection includes `round2`, credentials resolved, and
`--skip-hardware` is absent, `live_test_preflight` opens an `HMCClient`, resolves
`LIVE_TEST_SYSTEM_NAME`, and calls `hmcpctl.operations.lpar.provision._check_vlan_exists`,
the predicate `hmc_provision_lpar` itself refuses on, so the two cannot disagree. It prints
one line under the `round2` row:

- `provision VLAN: <id> has a virtual network`
- `provision VLAN: no virtual network on VLAN <id> — ST13 and ST14 will fail; set
  LIVE_TEST_PROVISION_VLAN_ID to an existing VLAN`
- `provision VLAN: unknown (<exception type>)` for any other failure.

The line is advisory: the exit status does not change. Preflight's documented rule is that
configuration is blocking and hardware is advisory; this is a hardware fact.

**Docs.** `.env.example` and `docs/live-testing.md` list both keys and the preflight line.

## Failure model

1. **Actors and deployments:** a local operator running `live_test_preflight.py` and
   `live_test_runner.py` against one authorized HMC. CI runs the offline tests only.
2. **Invariants and assets at stake:** ST14 deletes and recreates the test partition and
   disk; it must not start that sequence unless the configured VLAN is listed on the managed
   system at ST14's own pre-flight. Preflight prints no `HMC_*` value.
3. **Accepted failure classes:**
   - A VLAN removed between ST14's pre-flight listing and its provision call: the provision
     FAILs after the delete. The window is seconds within one operator's run.
   - The test partition has no vSCSI client adapter: ST14 still stops at pre-flight on
     `vios_slot`. The issue reports only `pvid` and `vdisk_size_mib`; follow-up candidate.
   - A disk size larger than the volume group's free space: the create FAILs at run time.
     Preflight does not check it (exclusion: preflight checks beyond the planned VLAN one).
   - A results document written before this change no longer restores (its config and
     artifact field sets differ); the runner already prints a warning and starts fresh.
4. **Covered elsewhere:** VLAN or disk auto-creation — excluded by the operator.
   `LIVE_TEST_LPAR_NAME` — #879. The over-long default `vdisk_name` — separate follow-up.
   Live proof — the #879 window.

## Validation

| Contract | Mode | Evidence |
|---|---|---|
| `from_env_file` loads both keys; rejects a missing key, VLAN 0 or 4095, disk size 0 or not a multiple of 1024 | focused-test | `tests/test_live_runner.py` |
| ST13 sends the configured VLAN with no baseline PVID and no `test_vlan_id` | focused-test | `tests/test_live_runner.py` |
| ST14 runs its full sequence with no `pvid` baseline and no `vdisk_size_mib`, sending the configured VLAN and size | focused-test | `tests/test_live_runner.py` |
| ST14 pre-flight FAILs with no power-off or delete when the VLAN is not listed, the listing fails, or a VLAN is unparsable | focused-test | `tests/test_live_runner.py` |
| `listed_vlans` returns the parsed set and malformed values; ST2 still picks the first unused VLAN | focused-test | `tests/scripts/test_inventory.py` |
| ST3 no longer records disk capacity | focused-test | `tests/scripts/test_inventory.py` |
| Preflight reports present, missing and unknown VLAN, keeps exit 0, and makes no HMC call under `--skip-hardware` or for a non-round2 group | focused-test | `tests/scripts/test_live_test_preflight.py` |
| `.env.example` carries both keys with loadable values | focused-test | existing `test_live_config_reads_the_complete_example_and_ignores_exports`, red until the keys are added |
| Runbook text | task-test-not-applicable | operator prose; no executable consumer reads `docs/live-testing.md` |
| Real VLAN lookup and disk create on a live VIOS | task-test-not-applicable | no offline HMC; deferred to the #879 window |
