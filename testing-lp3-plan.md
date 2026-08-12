# HMC MCP Live Testing Plan — ltczz386 System

## Overview

Exercise as many of the 103 HMC MCP tools as possible against a real HMC.
Three LPARs exist on the system: **ltczz386-lp1** and **ltczz386-lp2** are
powered off and **must not be touched**. **ltczz386-lp3** is running and is
fully authorized for modification, deletion, and recreation.

Where a mutating test requires a scratch LPAR we use ltczz386-lp3 directly or
create a temporary partition named **ltczz386-lp3-test** that is deleted at
the end of the relevant sub-task.

**ltczz386-lp3 baseline snapshot** is captured in Sub-Task 0 before any
mutation and restored in Sub-Task 14 at the end.

### Test constraints

| Constraint | Detail |
|---|---|
| HANDS-OFF | ltczz386-lp1, ltczz386-lp2 (powered off, must not be modified) |
| AUTHORIZED | ltczz386-lp3 (running; modify/destroy/recreate freely) |
| Scratch name | ltczz386-lp3-test (create and delete within tests) |

### Results tracking

Each sub-task records a **Results** table:

| Tool | Status | Notes |
|---|---|---|
| `tool_name` | ✅ PASS / ❌ FAIL / ⚠️ SKIP | Observed output / error |

Issues discovered are filed in GitHub and cross-referenced from the Results table.

---

## Sub-Task 0 — Capture ltczz386-lp3 Baseline Configuration

**Intent:** Record the complete current configuration of ltczz386-lp3 before
any mutating step touches it, so Sub-Task 14 can restore it faithfully.
This sub-task is purely read-only and produces a **Baseline Record** section
that is filled in with actual values during execution.

**Expected Outcomes:**
- All key configuration attributes of ltczz386-lp3 are documented.
- The record is sufficient for a human or tool to verify that the final state
  after Sub-Task 14 matches the pre-test state.

**Todo List:**
1. `hmc_lpars` with `lpar_name_or_uuid=ltczz386-lp3` — capture UUID, state,
   memory (min/desired/max), vCPU (min/desired/max), proc units, dedicated flag.
2. `hmc_lpar_summary` with `lpar_name_or_uuid=ltczz386-lp3` — capture OS info,
   RMC state, adapter list.
3. `hmc_get_lpar_description` — capture description string.
4. `hmc_get_lpar_msp` — capture MSP flag value.
5. `hmc_get_lpar_proc_compat` — capture current and pending proc compat mode.
6. `hmc_list_adapters` with `adapter_type=ClientNetworkAdapter` — list all CNAs
   (slot numbers, PVID, vswitch).
7. `hmc_run_command` with `cmd="lssyscfg -r lpar -m ltczz386 --filter lpar_names=ltczz386-lp3"` —
   full CLI attribute dump; paste raw output into Baseline Record.
8. Record all values in the **Baseline Record** table below.

**Relevant Context:**
- `src/hmc_mcp/server_composite.py`
- `src/hmc_mcp/server_cli.py`

**Status:** [ ] pending

### Baseline Record — ltczz386-lp3

| Attribute | Pre-Test Value |
|---|---|
| UUID | |
| State | |
| Memory min/desired/max (MiB) | |
| vCPU min/desired/max | |
| Proc units min/desired/max | |
| Dedicated procs | |
| Uncapped | |
| OS type | |
| RMC state | |
| Description | |
| MSP flag | |
| Proc compat mode (current) | |
| Proc compat mode (pending) | |
| Network adapters (slot → PVID) | |
| Raw lssyscfg output | *(paste below)* |

```
<paste lssyscfg output here>
```

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_lpars` (baseline) | | |
| `hmc_lpar_summary` (baseline) | | |
| `hmc_get_lpar_description` (baseline) | | |
| `hmc_get_lpar_msp` (baseline) | | |
| `hmc_get_lpar_proc_compat` (baseline) | | |
| `hmc_list_adapters` (baseline) | | |
| `hmc_run_command` (lssyscfg baseline) | | |

---

## Sub-Task 1 — Connectivity & Inventory (Read-Only)

**Intent:** Confirm HMC connectivity and exercise all pure inventory/read tools
before touching anything mutable.

**Expected Outcomes:**
- HMC console info returns version/network data
- Systems list includes the ltczz386 CPC
- LPAR list shows all three partitions with correct states
- VIOS list (if any VIOS exists) returns entries
- Capacity and placement reports return numeric data

**Todo List:**
1. Run `hmc_console_info` — baseline connectivity check.
2. Run `hmc_systems` (no args) — list all managed systems; note the UUID of ltczz386.
3. Run `hmc_systems` with `system_name_or_uuid=ltczz386` — single-system lookup.
4. Run `hmc_lpars` (no args) — list all LPARs; confirm lp1/lp2/lp3 visible.
5. Run `hmc_lpars` with `lpar_name_or_uuid=ltczz386-lp3` — single-LPAR lookup.
6. Run `hmc_vios` (no args) — list VIOSes.
7. Run `hmc_capacity_report` — per-system resource summary.
8. Run `hmc_find_placement` with `desired_memory_mb=1024` — placement candidate search.
9. Run `hmc_find_system` with `name=<system name from step 2>` — exact name lookup.
10. Run `hmc_list_resources` with `resource_type=LogicalPartition` — generic resource lister.
11. Run `hmc_recent_jobs` with `limit=10` — job history.
12. Run `hmc_system_summary` with `system_name_or_uuid=ltczz386` — composite summary.
13. Run `hmc_lpar_summary` with `lpar_name_or_uuid=ltczz386-lp3` — per-LPAR composite summary.

**Relevant Context:**
- `src/hmc_mcp/server_system.py` — inventory tools
- `src/hmc_mcp/server_composite.py` — summary tools
- `src/hmc_mcp/server_provision.py` — placement tool

**Status:** [ ] pending

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_console_info` | | |
| `hmc_systems` (list) | | |
| `hmc_systems` (single) | | |
| `hmc_lpars` (list) | | |
| `hmc_lpars` (single) | | |
| `hmc_vios` | | |
| `hmc_capacity_report` | | |
| `hmc_find_placement` | | |
| `hmc_find_system` | | |
| `hmc_list_resources` | | |
| `hmc_recent_jobs` | | |
| `hmc_system_summary` | | |
| `hmc_lpar_summary` | | |

---

## Sub-Task 2 — Network Inventory (Read-Only)

**Intent:** Exercise all virtual-networking read tools against the ltczz386 system.

**Expected Outcomes:**
- Virtual switches, virtual networks (VLANs), and network bridges are listed.
- FC port and SEA adapter lists return for the system.

**Todo List:**
1. Run `hmc_list_virtual_switches` with `system_name_or_uuid=ltczz386`.
2. Run `hmc_list_virtual_networks` with `system_name_or_uuid=ltczz386`.
3. Run `hmc_list_network_bridges` with `system_name_or_uuid=ltczz386`.
4. Run `hmc_list_fc_ports` with `system_name_or_uuid=ltczz386` (SSH/CLI).
5. Run `hmc_list_sea_adapters` with `system_name_or_uuid=ltczz386` (SSH/CLI).
6. Run `hmc_list_adapters` with `lpar_name_or_uuid=ltczz386-lp3` and `adapter_type=ClientNetworkAdapter`.

**Relevant Context:**
- `src/hmc_mcp/server_network.py`
- `src/hmc_mcp/server_cli.py` (SSH/CLI tools)

**Status:** [ ] pending

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_list_virtual_switches` | | |
| `hmc_list_virtual_networks` | | |
| `hmc_list_network_bridges` | | |
| `hmc_list_fc_ports` | | |
| `hmc_list_sea_adapters` | | |
| `hmc_list_adapters` (CNA) | | |

---

## Sub-Task 3 — Storage & SSP Inventory (Read-Only)

**Intent:** Exercise storage and cluster/SSP read tools.

**Expected Outcomes:**
- Volume groups listed for any VIOS on the system.
- SSP/Cluster lists return (empty is acceptable).
- I/O slots and memory pool lists return.

**Todo List:**
1. Run `hmc_vios` to obtain VIOS UUID(s) on ltczz386.
2. If a VIOS exists: run `hmc_list_volume_groups` with `vios_name_or_uuid=<uuid>`.
3. Run `hmc_list_clusters` (global).
4. Run `hmc_shared_storage_pools` (no args).
5. Run `hmc_list_io_slots` with `system_name_or_uuid=ltczz386`.
6. Run `hmc_list_memory_pools` with `system_name_or_uuid=ltczz386`.

**Relevant Context:**
- `src/hmc_mcp/server_storage.py`
- `src/hmc_mcp/server_cli.py`

**Status:** [ ] pending

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_list_volume_groups` | | |
| `hmc_list_clusters` | | |
| `hmc_shared_storage_pools` | | |
| `hmc_list_io_slots` | | |
| `hmc_list_memory_pools` | | |

---

## Sub-Task 4 — LPAR Properties & Profile Inventory (Read-Only, SSH/CLI)

**Intent:** Exercise the SSH/CLI read tools that inspect individual LPAR configuration.

**Expected Outcomes:**
- Description, MSP flag, processor compat mode return for ltczz386-lp3.
- Profile backup list, proc compat modes for system, vNIC list all return.

**Todo List:**
1. Run `hmc_get_lpar_description` with `system_name_or_uuid=ltczz386`, `lpar_name_or_uuid=ltczz386-lp3`.
2. Run `hmc_get_lpar_msp` with `system_name_or_uuid=ltczz386`, `lpar_name_or_uuid=ltczz386-lp3`.
3. Run `hmc_get_proc_compat_modes` with `system_name_or_uuid=ltczz386`.
4. Run `hmc_get_lpar_proc_compat` with `system_name_or_uuid=ltczz386`, `lpar_name_or_uuid=ltczz386-lp3`.
5. Run `hmc_list_vnics` with `system_name_or_uuid=ltczz386`, `lpar_name_or_uuid=ltczz386-lp3`.

**Relevant Context:**
- `src/hmc_mcp/server_cli.py`
- `src/hmc_mcp/server_profiles.py`

**Status:** [ ] pending

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_get_lpar_description` | | |
| `hmc_get_lpar_msp` | | |
| `hmc_get_proc_compat_modes` | | |
| `hmc_get_lpar_proc_compat` | | |
| `hmc_list_vnics` | | |

---

## Sub-Task 5 — Metrics & Templates (Read-Only)

**Intent:** Exercise PCM metrics read tools and partition template listing.

**Expected Outcomes:**
- PCM preferences return for the managed system.
- Partition template list returns (empty is acceptable).

**Todo List:**
1. Run `hmc_get_pcm_preferences` with `category=ManagedSystem` and `resource_name_or_uuid=ltczz386`.
2. Run `hmc_processed_metrics` with `category=ManagedSystem`, `resource_name_or_uuid=ltczz386`, `mode=list`.
3. Run `hmc_aggregated_metrics` with same args and `mode=list`.
4. Run `hmc_partition_templates` (no args).

**Relevant Context:**
- `src/hmc_mcp/server_metrics.py`
- `src/hmc_mcp/server_templates.py`

**Status:** [ ] pending

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_get_pcm_preferences` | | |
| `hmc_processed_metrics` (list) | | |
| `hmc_aggregated_metrics` (list) | | |
| `hmc_partition_templates` | | |

---

## Sub-Task 6 — User & Policy Inventory (Read-Only)

**Intent:** Exercise user-admin read tools without modifying any user accounts.

**Expected Outcomes:**
- User list returns; current HMC user visible.
- Password policy list returns.
- LDAP config returns (empty/disabled is acceptable).

**Todo List:**
1. Run `hmc_users` (no args).
2. Run `hmc_list_password_policies` (no args).
3. Run `hmc_get_ldap_config`.

**Relevant Context:**
- `src/hmc_mcp/server_users.py`

**Status:** [ ] pending

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_users` | | |
| `hmc_list_password_policies` | | |
| `hmc_get_ldap_config` | | |

---

## Sub-Task 7 — CLI Escape Hatch (SSH/CLI)

**Intent:** Verify the raw CLI escape hatch tool works via SSH.

**Expected Outcomes:**
- A safe, read-only HMC CLI command executes and returns output.

**Todo List:**
1. Run `hmc_run_command` with `cmd="lshmc -V"` — HMC version via CLI.
2. Run `hmc_run_command` with `cmd="lssyscfg -r sys"` — list managed systems via CLI.

**Relevant Context:**
- `src/hmc_mcp/server_system.py`

**Status:** [ ] pending

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_run_command` (lshmc -V) | | |
| `hmc_run_command` (lssyscfg -r sys) | | |

---

## Sub-Task 8 — LPAR Lifecycle on ltczz386-lp3 (Mutating)

**Intent:** Create, inspect, modify, power off, and delete a scratch LPAR to
exercise the full partition lifecycle. ltczz386-lp3 is the authorized scratch
LPAR; we will power it off and back on as needed, then restore it afterward.
We also create a second scratch LPAR named **ltczz386-lp3-test** and delete it
at the end.

**Expected Outcomes:**
- `hmc_create_lpar` creates ltczz386-lp3-test successfully.
- `hmc_modify_lpar` updates memory on ltczz386-lp3-test.
- `hmc_power_on_lpar` powers on ltczz386-lp3-test (may fail if no network/boot
  device; job submission success is the observable result).
- `hmc_power_off_lpar` powers off ltczz386-lp3-test.
- `hmc_delete_lpar` removes ltczz386-lp3-test.

**Todo List:**
1. Get the system UUID: `hmc_systems` with `system_name_or_uuid=ltczz386`.
2. Create scratch: `hmc_create_lpar` with `system_name_or_uuid=ltczz386`,
   `name=ltczz386-lp3-test`, `desired_memory=512`, `max_memory=1024`,
   `desired_vcpus=1`, `max_vcpus=2`.
3. List: `hmc_lpars` — confirm ltczz386-lp3-test visible.
4. Modify: `hmc_modify_lpar` with `lpar_name_or_uuid=ltczz386-lp3-test`,
   `desired_memory=768`, `max_memory=1536`.
5. Summary: `hmc_lpar_summary` with `lpar_name_or_uuid=ltczz386-lp3-test`.
6. Power on: `hmc_power_on_lpar` with `lpar_name_or_uuid=ltczz386-lp3-test`,
   `wait=True`. (Note outcome; failure to boot is expected without install.)
7. Power off: `hmc_power_off_lpar` with `lpar_name_or_uuid=ltczz386-lp3-test`,
   `immediate=True`, `wait=True`.
8. Delete: `hmc_delete_lpar` with `lpar_name_or_uuid=ltczz386-lp3-test`.
9. Confirm gone: `hmc_lpars` — ltczz386-lp3-test should not appear.

**Relevant Context:**
- `src/hmc_mcp/server_power.py`

**Status:** [ ] pending

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_create_lpar` | | |
| `hmc_modify_lpar` | | |
| `hmc_lpar_summary` (post-modify) | | |
| `hmc_power_on_lpar` | | |
| `hmc_power_off_lpar` | | |
| `hmc_delete_lpar` | | |

---

## Sub-Task 9 — Virtual Networking Mutations (Mutating)

**Intent:** Create a virtual network (VLAN) and then delete it, exercising the
mutable networking tools without touching existing adapters on lp1/lp2.

**Expected Outcomes:**
- A new virtual network is created with a test VLAN ID.
- A network adapter is added to ltczz386-lp3-test (within sub-task 8 lifecycle
  or as part of this task using a short-lived test LPAR).
- Virtual network is deleted.

**Todo List:**
1. Identify an unused VLAN ID from `hmc_list_virtual_networks`.
2. Identify a virtual switch ID from `hmc_list_virtual_switches`.
3. Run `hmc_create_virtual_network` with the unused VLAN ID and vswitch ID.
4. Confirm: `hmc_list_virtual_networks` — new entry visible.
5. Create a short-lived LPAR (ltczz386-lp3-nettest) for adapter tests.
6. Run `hmc_add_network_adapter` with `lpar_name_or_uuid=ltczz386-lp3-nettest`,
   the test VLAN ID, and vswitch ID.
7. Confirm: `hmc_list_adapters` with `adapter_type=ClientNetworkAdapter`.
8. Note adapter UUID from step 7.
9. Run `hmc_delete_adapter` to remove it.
10. Delete virtual network: `hmc_delete_virtual_network`.
11. Delete scratch LPAR: `hmc_delete_lpar`.

**Relevant Context:**
- `src/hmc_mcp/server_network.py`
- `src/hmc_mcp/server_storage.py`

**Status:** [ ] pending

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_create_virtual_network` | | |
| `hmc_add_network_adapter` | | |
| `hmc_list_adapters` (post-add) | | |
| `hmc_delete_adapter` | | |
| `hmc_delete_virtual_network` | | |

---

## Sub-Task 10 — LPAR Properties Mutations (Mutating, SSH/CLI)

**Intent:** Exercise the SSH/CLI mutating LPAR-property tools on ltczz386-lp3
(or a scratch LPAR), confirming changes are applied and can be read back.

**Expected Outcomes:**
- Description field set and read back matches.
- MSP flag toggled on/off.
- Processor compat mode set.
- Profile sync runs without error.
- Profile backup produces a file on the HMC.

**Todo List:**
1. Record current description: `hmc_get_lpar_description` for ltczz386-lp3.
2. Set description: `hmc_set_lpar_description` with a test string.
3. Read back: `hmc_get_lpar_description` — confirm change.
4. Restore original: `hmc_set_lpar_description` back to original value.
5. Record current MSP: `hmc_get_lpar_msp` for ltczz386-lp3.
6. Toggle MSP on: `hmc_set_lpar_msp` with `enabled=True` (or inverse of current).
7. Read back: `hmc_get_lpar_msp`.
8. Restore: `hmc_set_lpar_msp` back to original.
9. Get compat modes: `hmc_get_proc_compat_modes`.
10. Get current compat: `hmc_get_lpar_proc_compat` for ltczz386-lp3.
11. Set compat mode: `hmc_set_lpar_proc_compat` — use the system's default mode.
12. Sync profile: `hmc_sync_lpar_profile` with `system_name_or_uuid=ltczz386`, `lpar_name_or_uuid=ltczz386-lp3`.
13. Backup profiles: `hmc_backup_lpar_profiles` with `system_name_or_uuid=ltczz386`, `file_path=/tmp/lp3-profiles-test`.

**Relevant Context:**
- `src/hmc_mcp/server_cli.py`
- `src/hmc_mcp/server_profiles.py`

**Status:** [ ] pending

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_set_lpar_description` | | |
| `hmc_get_lpar_description` (verify) | | |
| `hmc_set_lpar_msp` | | |
| `hmc_get_lpar_msp` (verify) | | |
| `hmc_set_lpar_proc_compat` | | |
| `hmc_sync_lpar_profile` | | |
| `hmc_backup_lpar_profiles` | | |

---

## Sub-Task 11 — User Administration (Mutating & Cleanup)

**Intent:** Create a test HMC user, modify it, then delete it — exercising the
user-admin mutating tools without touching any existing production user.

**Expected Outcomes:**
- Test user `hmc-mcp-testuser` created successfully.
- User modified (description or role changed).
- User deleted, no longer visible in `hmc_users`.

**Todo List:**
1. Run `hmc_create_user` with `name=hmc-mcp-testuser`, `taskrole=viewer`,
   `password=<test-password>`, `description=MCP test user`.
2. Run `hmc_users` — confirm user visible.
3. Run `hmc_modify_user` with `name=hmc-mcp-testuser`,
   `description=MCP test user updated`.
4. Run `hmc_create_password_policy` with `policy_name=hmc-mcp-test-policy`,
   `min_length=10`.
5. Run `hmc_list_password_policies` — confirm policy visible.
6. Run `hmc_modify_password_policy` with `policy_name=hmc-mcp-test-policy`,
   `min_length=12`.
7. Run `hmc_delete_user` with `name=hmc-mcp-testuser`.
8. Run `hmc_delete_password_policy` with `policy_name=hmc-mcp-test-policy`.
9. Confirm both gone: `hmc_users` and `hmc_list_password_policies`.

**Relevant Context:**
- `src/hmc_mcp/server_users.py`

**Status:** [ ] pending

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_create_user` | | |
| `hmc_modify_user` | | |
| `hmc_create_password_policy` | | |
| `hmc_modify_password_policy` | | |
| `hmc_delete_user` | | |
| `hmc_delete_password_policy` | | |

---

## Sub-Task 12 — PCM Metrics Mutation & Job Monitoring

**Intent:** Toggle a PCM preference, then restore it; also exercise job-wait
tooling by submitting a job and polling it to completion.

**Expected Outcomes:**
- PCM preference toggled and readable.
- `hmc_get_job` and `hmc_wait_for_job` operate against a real job UUID.

**Todo List:**
1. Read current PCM prefs: `hmc_get_pcm_preferences` with `category=ManagedSystem`, `resource_name_or_uuid=ltczz386`.
2. Toggle long-term monitoring: `hmc_set_pcm_preferences` with `long_term_monitor=True` (or inverse).
3. Read back: confirm change.
4. Restore original setting.
5. From a prior power-on/off job UUID (from sub-task 8 results), run `hmc_get_job` with that UUID.
6. Run `hmc_wait_for_job` with a completed job UUID — should return immediately.
7. Run `hmc_recent_jobs` to confirm job history is populated.

**Relevant Context:**
- `src/hmc_mcp/server_metrics.py`
- `src/hmc_mcp/server_system.py`

**Status:** [ ] pending

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_set_pcm_preferences` | | |
| `hmc_get_job` | | |
| `hmc_wait_for_job` | | |
| `hmc_recent_jobs` (post-tests) | | |

---

## Sub-Task 13 — Provision Dry Run & Updates Check

**Intent:** Exercise the end-to-end provisioning tool in dry-run mode and
check HMC PTF/update status without applying any firmware changes.

**Expected Outcomes:**
- `hmc_provision_lpar` dry run completes without error.
- HMC console UUID available; PTF list returns (may be empty).
- Updates tools return expected structure without submitting actual update jobs.

**Todo List:**
1. Retrieve console UUID from `hmc_console_info` output.
2. Run `hmc_get_available_hmc_ptfs` with `console_uuid=<uuid>`.
3. Identify a VIOS UUID (from sub-task 3) for the dry-run provision call.
4. Run `hmc_provision_lpar` with `dry_run=True`, `system_name_or_uuid=ltczz386`,
   `name=ltczz386-lp3-dry`, `port_vlan_id=<test vlan>`, `vios_uuid=<uuid>`,
   `vios_partition_id=<id>`, `vios_slot=<slot>`, `storage_name=test-dry-disk`,
   `desired_memory=512`.

**Relevant Context:**
- `src/hmc_mcp/server_provision.py`
- `src/hmc_mcp/server_updates.py`

**Status:** [ ] pending

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_get_available_hmc_ptfs` | | |
| `hmc_provision_lpar` (dry_run) | | |

---

## Sub-Task 14 — Restore ltczz386-lp3 to Baseline

**Intent:** After all mutating sub-tasks have run, compare the current state of
ltczz386-lp3 against the Baseline Record captured in Sub-Task 0 and issue
corrective tool calls for any attribute that differs.

**Expected Outcomes:**
- Memory, CPU, description, MSP flag, and proc compat mode all match
  pre-test values.
- No extra adapters remain (any test adapters added in sub-tasks 9–10 were
  already cleaned up per those sub-tasks; this step is a final audit).
- A final `hmc_lpar_summary` confirms the restored state.

**Todo List:**
1. Run `hmc_lpar_summary` with `lpar_name_or_uuid=ltczz386-lp3` — compare
   against Baseline Record.
2. If memory differs: `hmc_modify_lpar` to restore min/desired/max values
   from baseline.
3. If CPU/proc units differ: `hmc_modify_lpar` to restore.
4. Run `hmc_get_lpar_description` — if differs, `hmc_set_lpar_description`
   with the baseline value.
5. Run `hmc_get_lpar_msp` — if differs, `hmc_set_lpar_msp` with baseline value.
6. Run `hmc_get_lpar_proc_compat` — if differs, `hmc_set_lpar_proc_compat`
   with baseline current mode.
7. Run `hmc_list_adapters` — confirm no unexpected extra adapters.
8. Run `hmc_sync_lpar_profile` to sync the running config back to the
   partition profile.
9. Run `hmc_run_command` with `cmd="lssyscfg -r lpar -m ltczz386 --filter lpar_names=ltczz386-lp3"` —
   final CLI dump; compare with baseline raw output in Sub-Task 0.
10. Final `hmc_lpar_summary` — record in Restored State table below.

**Relevant Context:**
- Sub-Task 0 Baseline Record
- `src/hmc_mcp/server_power.py`
- `src/hmc_mcp/server_cli.py`

**Status:** [ ] pending

### Restore Actions Taken

| Attribute | Baseline Value | Post-Test Value | Action Taken |
|---|---|---|---|
| Memory min/desired/max | | | |
| vCPU min/desired/max | | | |
| Proc units min/desired/max | | | |
| Description | | | |
| MSP flag | | | |
| Proc compat mode | | | |
| Adapters | | | |

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_lpar_summary` (post-test) | | |
| `hmc_modify_lpar` (restore, if needed) | | |
| `hmc_set_lpar_description` (restore, if needed) | | |
| `hmc_set_lpar_msp` (restore, if needed) | | |
| `hmc_set_lpar_proc_compat` (restore, if needed) | | |
| `hmc_sync_lpar_profile` | | |
| `hmc_run_command` (lssyscfg final) | | |
| `hmc_lpar_summary` (final confirm) | | |

---

## Issues Filed

| # | GitHub Issue | Tool | Summary |
|---|---|---|---|
| — | — | — | — |

---

## Final Status

- [ ] Sub-Task 0 — Capture ltczz386-lp3 Baseline Configuration
- [ ] Sub-Task 1 — Connectivity & Inventory
- [ ] Sub-Task 2 — Network Inventory
- [ ] Sub-Task 3 — Storage & SSP Inventory
- [ ] Sub-Task 4 — LPAR Properties & Profile Inventory
- [ ] Sub-Task 5 — Metrics & Templates
- [ ] Sub-Task 6 — User & Policy Inventory
- [ ] Sub-Task 7 — CLI Escape Hatch
- [ ] Sub-Task 8 — LPAR Lifecycle
- [ ] Sub-Task 9 — Virtual Networking Mutations
- [ ] Sub-Task 10 — LPAR Properties Mutations
- [ ] Sub-Task 11 — User Administration
- [ ] Sub-Task 12 — PCM Metrics & Job Monitoring
- [ ] Sub-Task 13 — Provision Dry Run & Updates Check
- [ ] Sub-Task 14 — Restore ltczz386-lp3 to Baseline
