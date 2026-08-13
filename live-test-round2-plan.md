# HMC MCP Live Functional Test — Round 2

## Overview

Re-run the full live functional test suite against the real HMC defined in `.env`
after closing the issues found in Round 1 (August 2026). The primary focus is
verifying that the flagship `hmc_provision_lpar` tool performs a **real (non-dry-run)**
end-to-end LPAR provisioning cycle against the live host, including destroying and
recreating the `ltczz386-lp3` virtual disk as part of the storage lifecycle.

### Scope

- **System:** `ltczz386` (POWER9, managed by V10R3 HMC)
- **Protected LPARs:** `ltczz386-lp1`, `ltczz386-lp2` — read-only; must not be touched
- **Test LPAR:** `ltczz386-lp3` — authorized for modification, deletion, and recreation
- **Scratch LPAR names (created + deleted within tests):**
  - `ltczz386-lp3-test` — used in LPAR lifecycle sub-task
  - `ltczz386-lp3-nettest` — used in virtual networking sub-task
- **Test runner:** `scripts/live_test_runner.py` (in-process FastMCP client via `.env`)
- **Results file:** `test-results-round2.json`

### Known storage configuration

- lp3 runs RHEL and boots from a 48 GB virtual disk named **VG1-lp3** inside
  the VIOS volume group `VG1`.
- Sub-Task 3 captures the VG UUID and the virtual disk details at runtime.
- Sub-Task 14 deletes the existing VG1-lp3 logical volume via `viosvrcmd` +
  `rmvlog` on the VIOS (the HMC REST API has no standalone delete-disk endpoint),
  then creates a new 48 GB virtual disk with the same name via
  `hmc_create_virtual_disk`, and finally calls `hmc_provision_lpar` to map and
  boot. If the REST `VolumeGroup` POST returns 406, that step is skipped and the
  LV must be recreated manually on the VIOS before re-running ST14.

### What changed since Round 1

| Issue | Tool(s) | Fix |
|---|---|---|
| #100 | `hmc_set_lpar_description` | ASCII validation added before SSH call |
| #101 | `hmc_set_lpar_proc_compat` | Corrected chsyscfg field name |
| #102 | `hmc_set_lpar_msp` | Partition-type pre-check (VIOS only) |
| #103 | `hmc_backup_lpar_profiles` | `force=True` parameter added |
| #95 | `hmc_recent_jobs` | Graceful HTTP 400 fallback (returns error sentinel) |
| #96 | `hmc_create_lpar` / `hmc_create_virtual_network` | HTTP 406 diagnostic + `HMC_SCHEMA_VERSION=V1_0` support |
| #99 | User/policy/LDAP tools | REST000E detection + `X-HMC-Schema-Version` header |

### HMC_SCHEMA_VERSION

`HMC_SCHEMA_VERSION=V1_0` **must** be set in `.env` before running the test
runner. The pre-run script enforces this and exits with a clear error if it is
absent.

**Important:** this variable only affects `GET` requests — it pins the
`X-HMC-Schema-Version` request header on read paths. It has **no effect** on
write-path HTTP 406 errors. The fix for the Round 1 HTTP 406 failures
(`hmc_create_lpar`, `hmc_create_virtual_network`, storage and adapter PUT/POST)
is that the client now omits `X-HMC-Schema-Version` from all write paths
(`PUT`/`POST`) entirely — regardless of what `HMC_SCHEMA_VERSION` is set to.
See [`src/hmc_mcp/client.py`](src/hmc_mcp/client.py) (`include_schema_version=False`).

### Test constraints

| Constraint | Detail |
|---|---|
| HANDS-OFF | `ltczz386-lp1`, `ltczz386-lp2` — powered off, must not be modified |
| AUTHORIZED | `ltczz386-lp3` — running RHEL; modify/destroy/recreate freely |
| Scratch names | `ltczz386-lp3-test`, `ltczz386-lp3-nettest` — create and delete within tests |
| Storage | `VG1-lp3` (48 GB LV in VG1) — delete and recreate as part of ST14 |

### lp3 Restore Contract

Sub-Task 0 captures the full `ltczz386-lp3` configuration. Sub-Task 14 powers
off and deletes lp3, deletes the existing VG1-lp3 virtual disk, creates a new
48 GB virtual disk with the same name, then calls `hmc_provision_lpar` to
recreate lp3 end-to-end. Sub-Task 15 restores any attributes not set by
provision (description, proc-compat mode) and syncs the profile. The final
state must match the Sub-Task 0 baseline.

---

## Sub-Task 0 — Capture ltczz386-lp3 Baseline

**Intent:** Record the complete current configuration of `ltczz386-lp3` before
any mutation. This snapshot drives Sub-Tasks 14 and 15. This sub-task is
purely read-only.

**Expected Outcomes:**
- UUID, state, memory bounds, vCPU/proc-unit bounds, description, MSP flag,
  proc-compat modes, all CNA adapters (PVID/vswitch/slot), all vSCSI client
  adapters (VIOS partition ID, VIOS server slot), and VIOS UUID all captured
  in `CTX["lp3_baseline"]`.
- `CTX["vios_uuid"]` and `CTX["vios_partition_id"]` populated.
- Raw `lssyscfg` output stored.

**Todo List:**
1. `hmc_lpars` with `lpar_name_or_uuid=ltczz386-lp3` — capture UUID, state, memory, vCPU.
2. `hmc_lpar_summary` with `lpar_name_or_uuid=ltczz386-lp3` — capture OS info, RMC state, adapter count.
3. `hmc_get_lpar_description` — capture description string.
4. `hmc_get_lpar_msp` — capture MSP flag.
5. `hmc_get_lpar_proc_compat` — capture current and desired compat mode.
6. `hmc_list_adapters` with `adapter_type=ClientNetworkAdapter` — capture all CNAs (slot, PVID, vswitch).
7. `hmc_list_adapters` with `adapter_type=VirtualSCSIClientAdapter` — capture vSCSI adapters; extract VIOS partition ID and VIOS server slot for use in ST14 provision call.
8. `hmc_vios` — capture VIOS UUID and numeric PartitionID.
9. `hmc_run_command` with `lssyscfg -r lpar -m ltczz386 --filter lpar_names=ltczz386-lp3` — full CLI attr dump.
10. Store all values in `CTX["lp3_baseline"]`.

**Relevant Context:**
- `src/hmc_mcp/server_composite.py`
- `src/hmc_mcp/server_cli.py`
- `scripts/live_test_runner.py` — `subtask_0()`, `CTX` dict

**Status:** [ ] pending

---

## Sub-Task 1 — Connectivity & Inventory (Read-Only)

**Intent:** Confirm HMC connectivity and exercise all pure inventory/read tools.
Same as Round 1 but now with corrected error handling for `hmc_recent_jobs`.

**Expected Outcomes:**
- `hmc_console_info` returns version/network data; console UUID captured.
- `hmc_systems` lists `ltczz386`; system UUID captured.
- `hmc_lpars` shows all three LPARs; lp1 and lp2 are powered off.
- `hmc_vios` returns VIOS entry with UUID and PartitionID.
- `hmc_capacity_report`, `hmc_find_placement`, `hmc_system_summary`, `hmc_lpar_summary` all return structured data.
- `hmc_recent_jobs` either returns job entries or returns a graceful error sentinel (`"type":"error"` dict) instead of raising.

**Todo List:**
1. `hmc_console_info` — capture console UUID.
2. `hmc_systems` (no args) — list all; capture `ltczz386` UUID.
3. `hmc_systems` with `system_name_or_uuid=ltczz386` — single lookup.
4. `hmc_lpars` (no args) — list all; confirm lp1/lp2/lp3 visible.
5. `hmc_lpars` with `lpar_name_or_uuid=ltczz386-lp3` — single lookup; capture UUID.
6. `hmc_vios` — capture VIOS UUID and PartitionID (if not already set from ST0).
7. `hmc_capacity_report`.
8. `hmc_find_placement` with `desired_memory_mb=1024`.
9. `hmc_find_system` with `name=ltczz386`.
10. `hmc_list_resources` with `resource_type=LogicalPartition`.
11. `hmc_recent_jobs` with `limit=10` — verify graceful fallback is working.
12. `hmc_system_summary` with `system_name_or_uuid=ltczz386`.
13. `hmc_lpar_summary` with `lpar_name_or_uuid=ltczz386-lp3`.

**Relevant Context:**
- `src/hmc_mcp/server_system.py` — graceful HTTP 400 fallback at lines 225–243
- `src/hmc_mcp/server_composite.py`

**Status:** [ ] pending

---

## Sub-Task 2 — Network Inventory (Read-Only)

**Intent:** Exercise all virtual-networking read tools. Also pick an unused VLAN
ID in the 3000–3099 range for mutation sub-tasks.

**Expected Outcomes:**
- Virtual switches, VLANs, network bridges listed.
- An unused VLAN ID captured in `CTX["test_vlan_id"]`.
- FC port and SEA adapter lists return.

**Todo List:**
1. `hmc_list_virtual_switches` — capture switch ID (SwitchID=0 expected).
2. `hmc_list_virtual_networks` — enumerate existing VLANs; pick unused test VLAN in 3000–3099.
3. `hmc_list_network_bridges`.
4. `hmc_list_fc_ports`.
5. `hmc_list_sea_adapters`.
6. `hmc_list_adapters` with `lpar_name_or_uuid=ltczz386-lp3`, `adapter_type=ClientNetworkAdapter`.

**Relevant Context:**
- `src/hmc_mcp/server_network.py`
- `src/hmc_mcp/server_cli.py`

**Status:** [ ] pending

---

## Sub-Task 3 — Storage & SSP Inventory (Read-Only)

**Intent:** Exercise storage read tools; capture volume group UUID and virtual
disk name for use in ST14. The VG1-lp3 virtual disk inside VG1 will be deleted
and recreated in ST14.

**Expected Outcomes:**
- Volume groups listed for VIOS; VG1 UUID captured.
- `VG1-lp3` virtual disk entry confirmed; disk size captured (~49152 MiB for 48 GB).
- VG UUID stored in `CTX["vg_uuid"]` and disk name in `CTX["vdisk_name"]`.
- Cluster/SSP lists return (empty is fine).
- I/O slot and memory pool lists return.

**Todo List:**
1. `hmc_list_volume_groups` with `vios_name_or_uuid=<vios_uuid>` — capture VG1 UUID; confirm VG1-lp3 disk present; capture disk size.
2. Store VG UUID in `CTX["vg_uuid"]`, virtual disk name `VG1-lp3` in `CTX["vdisk_name"]`, size in `CTX["vdisk_size_mb"]`.
3. `hmc_list_clusters`.
4. `hmc_shared_storage_pools`.
5. `hmc_list_io_slots` with `system_name_or_uuid=ltczz386`.
6. `hmc_list_memory_pools` with `system_name_or_uuid=ltczz386`.

**Relevant Context:**
- `src/hmc_mcp/server_storage.py`

**Status:** [ ] pending

---

## Sub-Task 4 — LPAR Properties & Profile Inventory (Read-Only)

**Intent:** Exercise SSH/CLI read tools on lp3.

**Expected Outcomes:**
- Description, MSP flag, proc-compat mode return correctly.
- System-level proc compat mode list returns.
- vNIC list returns.

**Todo List:**
1. `hmc_get_lpar_description` for ltczz386-lp3.
2. `hmc_get_lpar_msp` for ltczz386-lp3.
3. `hmc_get_proc_compat_modes` with `system_name_or_uuid=ltczz386`.
4. `hmc_get_lpar_proc_compat` for ltczz386-lp3.
5. `hmc_list_vnics` for ltczz386-lp3.

**Relevant Context:**
- `src/hmc_mcp/server_cli.py`

**Status:** [ ] pending

---

## Sub-Task 5 — Metrics & Templates (Read-Only)

**Intent:** Exercise PCM and template read tools. Expected to fail on this HMC
(no PCM license, no template support) — test verifies errors are clear and
graceful rather than crashing.

**Expected Outcomes:**
- PCM/metrics tools fail with HTTP 406/403 and clear diagnostics.
- `hmc_partition_templates` fails with HTTP 406.

**Todo List:**
1. `hmc_get_pcm_preferences` with `category=ManagedSystem`, `resource_name_or_uuid=ltczz386`.
2. `hmc_processed_metrics` with `category=ManagedSystem`, `mode=links`.
3. `hmc_aggregated_metrics` with `category=ManagedSystem`, `mode=links`.
4. `hmc_partition_templates`.

**Relevant Context:**
- `src/hmc_mcp/server_metrics.py`
- `src/hmc_mcp/server_templates.py`

**Status:** [ ] pending

---

## Sub-Task 6 — User & Policy Inventory (Read-Only)

**Intent:** Re-exercise user-admin read tools with `HMC_SCHEMA_VERSION=V1_0`
now set. Round 1 these all failed HTTP 400 (REST000E). Verify either that the
header resolves the issue, or that the REST000E diagnostic is now clear.

**Expected Outcomes:**
- If schema version resolves the issue: `hmc_users` and `hmc_list_password_policies` return data.
- If not: tools fail with a descriptive REST000E message, not an opaque 400.

**Todo List:**
1. `hmc_users`.
2. `hmc_list_password_policies`.
3. `hmc_get_ldap_config`.

**Relevant Context:**
- `src/hmc_mcp/server_users.py`
- `src/hmc_mcp/client.py` — REST000E detection

**Status:** [ ] pending

---

## Sub-Task 7 — CLI Escape Hatch (SSH/CLI)

**Intent:** Verify the raw CLI escape hatch works. Read-only.

**Expected Outcomes:**
- Both commands return output.

**Todo List:**
1. `hmc_run_command` with `cmd="lshmc -V"`.
2. `hmc_run_command` with `cmd="lssyscfg -r sys"`.

**Relevant Context:**
- `src/hmc_mcp/server_system.py`

**Status:** [ ] pending

---

## Sub-Task 8 — LPAR Lifecycle (Scratch LPAR, Mutating)

**Intent:** Create, modify, power-cycle, and delete `ltczz386-lp3-test`. This
is the first mutating sub-task and the first real test of the `HMC_SCHEMA_VERSION=V1_0`
fix for `hmc_create_lpar` (issue #96). The job UUID from power operations is
captured for ST12.

**Expected Outcomes:**
- `hmc_create_lpar` succeeds and returns a UUID.
- `hmc_modify_lpar` updates memory.
- `hmc_power_on_lpar` job submitted (boot failure expected — no OS installed).
- `hmc_power_off_lpar` powers it off.
- `hmc_delete_lpar` removes it; confirm gone.

**Todo List:**
1. Ensure `system_uuid` in context; fetch if missing.
2. `hmc_create_lpar` — `name=ltczz386-lp3-test`, `desired_memory=512`, `max_memory=1024`, `desired_vcpus=1`, `max_vcpus=2`.
3. `hmc_lpars` — confirm visible; capture UUID.
4. `hmc_modify_lpar` — `desired_memory=768`, `max_memory=1536`.
5. `hmc_lpar_summary` — verify modified values.
6. `hmc_power_on_lpar` with `wait=True` — capture job UUID for ST12.
7. `hmc_power_off_lpar` with `immediate=True`, `wait=True`.
8. `hmc_delete_lpar`.
9. `hmc_lpars` — confirm gone.

**Relevant Context:**
- `src/hmc_mcp/server_power.py` — `_check_lpar_write_error()` for HTTP 406
- `src/hmc_mcp/client.py` — `X-HMC-Schema-Version` header

**Status:** [ ] pending

---

## Sub-Task 9 — Virtual Networking Mutations (Mutating)

**Intent:** Create a VLAN, add a network adapter to a short-lived LPAR, then
clean up. Also tests the HTTP 406 fix on `hmc_create_virtual_network`.

**Expected Outcomes:**
- `hmc_create_virtual_network` creates a new VLAN entry.
- `hmc_create_lpar` for `ltczz386-lp3-nettest` succeeds.
- `hmc_add_network_adapter` adds a CNA to the nettest LPAR.
- Adapter UUID captured; `hmc_delete_adapter` removes it.
- `hmc_delete_virtual_network` removes the test VLAN.
- `hmc_delete_lpar` removes the nettest LPAR.

**Todo List:**
1. Confirm `test_vlan_id` and `test_vswitch_id` in context (from ST2); skip if absent.
2. `hmc_create_virtual_network` — name `mcp-test-vlan<N>`, VLAN=`test_vlan_id`, `tagged=False`.
3. `hmc_list_virtual_networks` — confirm new entry; capture UUID.
4. `hmc_create_lpar` — `name=ltczz386-lp3-nettest`, `desired_memory=256`, `max_memory=512`.
5. `hmc_add_network_adapter` — `port_vlan_id=test_vlan_id`, `virtual_switch_id=test_vswitch_id`.
6. `hmc_list_adapters` with `adapter_type=ClientNetworkAdapter` — capture adapter UUID.
7. `hmc_delete_adapter` — remove adapter.
8. `hmc_delete_virtual_network` — remove test VLAN.
9. `hmc_delete_lpar` — remove nettest LPAR.

**Relevant Context:**
- `src/hmc_mcp/server_network.py`
- `src/hmc_mcp/server_storage.py`

**Status:** [ ] pending

---

## Sub-Task 10 — LPAR Properties Mutations (Mutating, SSH/CLI)

**Intent:** Exercise the SSH/CLI property-setting tools on lp3, verifying that
all Round 1 bugs are fixed: description ASCII validation (#100), MSP VIOS-only
rejection (#102), proc-compat correct field name (#101), backup force flag (#103).

**Expected Outcomes:**
- `hmc_set_lpar_description` with ASCII-only string succeeds; read-back matches; restored.
- `hmc_set_lpar_msp` on an AIX/Linux partition rejects with a clear VIOS-only error.
- `hmc_set_lpar_proc_compat` with current mode succeeds (correct field name).
- `hmc_sync_lpar_profile` succeeds.
- `hmc_backup_lpar_profiles` with `force=True` succeeds even if file exists.

**Todo List:**
1. `hmc_set_lpar_description` — ASCII-only test string.
2. `hmc_get_lpar_description` — verify change.
3. `hmc_set_lpar_description` — restore original.
4. `hmc_run_command` `lssyscfg -F lpar_env` — confirm lp3 is `aixlinux` not `vioserver`.
5. `hmc_set_lpar_msp` with `enabled=True` — expect VIOS-rejection error (this is a PASS).
6. `hmc_set_lpar_proc_compat` — set to lp3's current compat mode (idempotent).
7. `hmc_get_lpar_proc_compat` — verify.
8. `hmc_sync_lpar_profile`.
9. `hmc_backup_lpar_profiles` — `file_path=/tmp/mcp-lp3-profiles-r2`, `force=True`.

**Relevant Context:**
- `src/hmc_mcp/server_cli.py`
- `src/hmc_mcp/ssh.py`
- `src/hmc_mcp/server_profiles.py`

**Status:** [ ] pending

---

## Sub-Task 11 — User Administration (Mutating & Cleanup)

**Intent:** Re-try user/policy CRUD tools with `HMC_SCHEMA_VERSION=V1_0`. Round 1
these all failed HTTP 400 (REST000E). Test whether the header resolves the
issue; if not, verify the diagnostic is clear.

**Expected Outcomes:**
- Either all tools pass (schema version resolved the issue).
- Or all tools fail with a clear REST000E message (not an opaque 400).
- Any created test user/policy is deleted regardless of intermediate failures.

**Todo List:**
1. `hmc_create_user` — `name=hmc-mcp-testuser`, `taskrole=viewer`.
2. `hmc_users` — confirm visible.
3. `hmc_modify_user` — update description.
4. `hmc_create_password_policy` — `policy_name=hmc-mcp-test-policy`, `min_length=10`.
5. `hmc_list_password_policies` — confirm visible.
6. `hmc_modify_password_policy` — `min_length=12`.
7. `hmc_delete_user`.
8. `hmc_delete_password_policy`.
9. `hmc_users` and `hmc_list_password_policies` — confirm both gone.

**Relevant Context:**
- `src/hmc_mcp/server_users.py`
- `src/hmc_mcp/client.py` — REST000E detection and `X-HMC-Schema-Version` header

**Status:** [ ] pending

---

## Sub-Task 12 — PCM Metrics & Job Monitoring

**Intent:** Exercise PCM preference toggle and job polling. PCM expected to fail
(no license) — verify graceful errors. Job monitoring uses UUID from ST8.

**Expected Outcomes:**
- `hmc_get_pcm_preferences` / `hmc_set_pcm_preferences` fail gracefully with HTTP 406.
- `hmc_get_job` and `hmc_wait_for_job` succeed if a job UUID was captured in ST8.
- `hmc_recent_jobs` returns entries or graceful error sentinel.

**Todo List:**
1. `hmc_get_pcm_preferences` — expect graceful HTTP 406.
2. `hmc_set_pcm_preferences` — attempt toggle; expect HTTP 406.
3. If job UUID from ST8: `hmc_get_job`.
4. If job UUID: `hmc_wait_for_job` with `timeout_seconds=10`, `poll_interval=2`.
5. `hmc_recent_jobs` with `limit=20`.

**Relevant Context:**
- `src/hmc_mcp/server_metrics.py`
- `src/hmc_mcp/server_system.py` — graceful fallback

**Status:** [ ] pending

---

## Sub-Task 13 — Provision Dry Run

**Intent:** Run `hmc_provision_lpar` in dry-run mode with a valid VLAN (from ST2
or ST9's test VLAN if it still exists). All preconditions must pass for this
dry run to be meaningful.

**Expected Outcomes:**
- `hmc_provision_lpar` with `dry_run=True` passes all preconditions and returns
  all steps with `status="dry_run"`.
- No mutations made.

**Todo List:**
1. Determine the best VLAN ID: prefer the existing lp3 PVID (known valid), fall back to ST9 test VLAN if still present.
2. Confirm VIOS UUID in context.
3. `hmc_provision_lpar` with `dry_run=True`, using real VLAN, real VIOS, fake storage name.
4. Inspect `steps` — all should show `status="dry_run"`.

**Relevant Context:**
- `src/hmc_mcp/server_provision.py`

**Status:** [ ] pending

---

## Sub-Task 14 — Storage Lifecycle + Full Live Provision of ltczz386-lp3 (Flagship Test)

**Intent:** This is the primary goal of Round 2. It exercises the full
destructive + recreate lifecycle for both the LPAR and its storage:

1. Power off and delete `ltczz386-lp3`.
2. Delete the existing `VG1-lp3` virtual disk via `hmc_create_virtual_disk`'s
   inverse (`hmc_list_volume_groups` shows it; the disk is unmapped once lp3 is deleted).
3. Create a new `VG1-lp3` virtual disk of 48 GB (49152 MiB) in VG1.
4. Run `hmc_provision_lpar` (non-dry-run) to recreate lp3 end-to-end.

This exercises `hmc_power_off_lpar`, `hmc_delete_lpar`, `hmc_list_volume_groups`,
`hmc_create_virtual_disk`, and the full five-step provision workflow
(create → network → vSCSI → storage map → power-on).

**Expected Outcomes:**
- lp3 powered off and deleted successfully.
- VG1-lp3 virtual disk deleted from VG1 (confirmed via `hmc_list_volume_groups`).
- New VG1-lp3 disk created at 49152 MiB.
- `hmc_provision_lpar` returns `{"created": true}` with all five steps `"ok"`.
- `hmc_lpar_summary` on the new lp3 confirms it is running.

**Todo List:**
1. Pre-flight: confirm `CTX["lp3_baseline"]` has memory, PVID, VIOS slot, `CTX["vg_uuid"]`, `CTX["vdisk_name"]`, `CTX["vdisk_size_mb"]`.
2. `hmc_power_off_lpar` — `lpar_name_or_uuid=ltczz386-lp3`, `immediate=True`, `wait=True`.
3. `hmc_delete_lpar` — `lpar_name_or_uuid=ltczz386-lp3`.
4. `hmc_lpars` — confirm lp3 is gone.
5. `hmc_list_volume_groups` — verify VG1-lp3 disk appears unmapped; capture virtual disk UUID if present (for reference).
6. Note: the HMC REST API deletes virtual disks by removing them from the VolumeGroup child list (PUT VolumeGroup without the disk entry). Since there is no standalone `hmc_delete_virtual_disk` tool, use `hmc_create_virtual_disk` at the next step to create the replacement. If the old disk still appears after lp3 deletion, record that observation and proceed — the VIOS will have it as a free LV.
7. `hmc_create_virtual_disk` — `vios_name_or_uuid=<vios_uuid>`, `vg_uuid=<vg_uuid>`, `disk_name=VG1-lp3`, `capacity_mb=49152`.
8. `hmc_list_volume_groups` — confirm new VG1-lp3 disk appears.
9. `hmc_provision_lpar` — full live run with all baseline parameters:
   - `system_name_or_uuid=ltczz386`
   - `name=ltczz386-lp3`
   - `port_vlan_id=<lp3 baseline PVID from CTX>`
   - `vios_uuid=<vios_uuid>`
   - `vios_partition_id=<vios_partition_id>`
   - `vios_slot=<lp3 baseline VIOS server slot from CTX>`
   - `storage_name=VG1-lp3`
   - `storage_kind=VirtualDisk`
   - `vg_uuid=<vg_uuid>`
   - `min_memory`, `desired_memory`, `max_memory` from baseline
   - `desired_vcpus`, `max_vcpus` from baseline
   - `partition_type=AIX/Linux`
   - `power_on=True`
   - `dry_run=False`
10. Inspect `steps` array — all five should be `"ok"`.
11. `hmc_lpar_summary` on `ltczz386-lp3` — confirm running.

**Relevant Context:**
- `src/hmc_mcp/server_provision.py` — full workflow
- `src/hmc_mcp/server_storage.py` — `hmc_create_virtual_disk`, `hmc_list_volume_groups`
- `CTX["lp3_baseline"]`, `CTX["vg_uuid"]`, `CTX["vdisk_size_mb"]`

**Status:** [ ] pending

---

## Sub-Task 15 — Restore ltczz386-lp3 to Baseline

**Intent:** After ST14 recreates lp3 via `hmc_provision_lpar`, restore any
remaining attributes not set by provision (description, proc-compat mode) and
sync the profile. Confirm the final state matches Sub-Task 0 baseline.

**Expected Outcomes:**
- Description matches baseline (empty or original value).
- Proc-compat mode matches baseline.
- No unexpected extra adapters.
- Profile synced.
- Final `hmc_lpar_summary` matches pre-test state.

**Todo List:**
1. `hmc_lpar_summary` — compare with baseline.
2. `hmc_set_lpar_description` — restore baseline description (ASCII-safe).
3. `hmc_set_lpar_proc_compat` — restore baseline proc-compat mode.
4. `hmc_list_adapters` with `adapter_type=ClientNetworkAdapter` — final audit.
5. `hmc_sync_lpar_profile`.
6. `hmc_run_command` with `lssyscfg -r lpar -m ltczz386 --filter lpar_names=ltczz386-lp3` — final dump.
7. `hmc_lpar_summary` — final confirm.

**Relevant Context:**
- Sub-Task 0 Baseline Record
- `src/hmc_mcp/server_cli.py`
- `src/hmc_mcp/server_profiles.py`

**Status:** [ ] pending

---

## Implementation Notes

### live_test_runner.py changes required

The script at `scripts/live_test_runner.py` must be updated to match this plan:

1. **Pre-run step:** Check `.env` for `HMC_SCHEMA_VERSION=V1_0`; warn and add it if absent.
2. **ST0 additions:**
   - Add `hmc_list_adapters` with `adapter_type=VirtualSCSIClientAdapter` — capture VIOS partition ID and VIOS server slot.
   - Capture `CTX["vios_uuid"]` and `CTX["vios_partition_id"]` here rather than waiting for ST1.
3. **ST3 additions:**
   - After `hmc_list_volume_groups`, extract and store `CTX["vg_uuid"]` (VG1 UUID), `CTX["vdisk_name"]` (`"VG1-lp3"`), and `CTX["vdisk_size_mb"]` (actual disk size from API).
4. **New ST14 (Full Live Provision):**
   - Power off lp3, delete lp3, create new virtual disk, run `hmc_provision_lpar`, verify steps.
5. **ST14 → ST15 renumber:** Old ST14 restore becomes ST15; add proc-compat restore step.
6. **Results file:** Output to `test-results-round2.json`.
7. **Sub-task range:** SUBTASKS dict 0–15 (16 total).
8. **CTX additions:** `vg_uuid`, `vdisk_name`, `vdisk_size_mb` keys.

### Pre-run checklist

- [ ] `HMC_SCHEMA_VERSION=V1_0` added to `.env`
- [ ] `ltczz386-lp3` is powered on and running RHEL before ST0
- [ ] `ltczz386-lp1` and `ltczz386-lp2` are powered off and will not be touched
- [ ] VIOS has VG1 with VG1-lp3 disk present (48 GB LV)
- [ ] `uv run python scripts/smoke_mcp.py` passes

---

## Final Status

- [ ] ST0 — Capture ltczz386-lp3 Baseline
- [ ] ST1 — Connectivity & Inventory
- [ ] ST2 — Network Inventory
- [ ] ST3 — Storage & SSP Inventory
- [ ] ST4 — LPAR Properties & Profile Inventory
- [ ] ST5 — Metrics & Templates
- [ ] ST6 — User & Policy Inventory
- [ ] ST7 — CLI Escape Hatch
- [ ] ST8 — LPAR Lifecycle
- [ ] ST9 — Virtual Networking Mutations
- [ ] ST10 — LPAR Properties Mutations
- [ ] ST11 — User Administration
- [ ] ST12 — PCM Metrics & Job Monitoring
- [ ] ST13 — Provision Dry Run
- [ ] ST14 — Storage Lifecycle + Full Live Provision of ltczz386-lp3
- [ ] ST15 — Restore ltczz386-lp3 to Baseline
