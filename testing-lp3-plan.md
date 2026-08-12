# HMC MCP Live Testing Plan — <system-name> System

## Environment

| Component | Details |
|---|---|
| **HMC hostname** | <hmc-hostname> |
| **HMC version** | V10R3 (10.3.1060) |
| **HMC build level** | 2408210051 (2024-08-21) |
| **HMC iFixes** | MF71689, MF71697, MF71699, MF71703 |
| **HMC machine** | VMware virtual HMC |
| **Managed system** | <system-name> (POWER9) |
| **System UUID** | <system-uuid> |
| **VIOS UUID** | <vios-uuid> |
| **Test LPAR** | <system-name>-lp3 (UUID: <lp3-uuid>) |
| **HMC_SCHEMA_VERSION** | (not set during test run) |

> **Note:** V10R3 is current HMC software (2024). The POWER9 hardware is managed by a fully modern HMC. Failures are likely API/header issues (wrong endpoint, missing Accept header, schema version) rather than firmware age. See Issue [#96](https://github.com/randomparity/hmc-mcp/issues/96) — setting `HMC_SCHEMA_VERSION=V1_0` may resolve REST create (HTTP 406) and web API (HTTP 400) failures.


## Overview

Exercise as many of the 103 HMC MCP tools as possible against a real HMC.
Three LPARs exist on the system: **<system-name>-lp1** and **<system-name>-lp2** are
powered off and **must not be touched**. **<system-name>-lp3** is running and is
fully authorized for modification, deletion, and recreation.

Where a mutating test requires a scratch LPAR we use <system-name>-lp3 directly or
create a temporary partition named **<system-name>-lp3-test** that is deleted at
the end of the relevant sub-task.

**<system-name>-lp3 baseline snapshot** is captured in Sub-Task 0 before any
mutation and restored in Sub-Task 14 at the end.

### Test constraints

| Constraint | Detail |
|---|---|
| HANDS-OFF | <system-name>-lp1, <system-name>-lp2 (powered off, must not be modified) |
| AUTHORIZED | <system-name>-lp3 (running; modify/destroy/recreate freely) |
| Scratch name | <system-name>-lp3-test (create and delete within tests) |

### Results tracking

Each sub-task records a **Results** table:

| Tool | Status | Notes |
|---|---|---|
| `tool_name` | ✅ PASS / ❌ FAIL / ⚠️ SKIP | Observed output / error |

Issues discovered are filed in GitHub and cross-referenced from the Results table.

---

## Sub-Task 0 — Capture <system-name>-lp3 Baseline Configuration

**Intent:** Record the complete current configuration of <system-name>-lp3 before
any mutating step touches it, so Sub-Task 14 can restore it faithfully.
This sub-task is purely read-only and produces a **Baseline Record** section
that is filled in with actual values during execution.

**Expected Outcomes:**
- All key configuration attributes of <system-name>-lp3 are documented.
- The record is sufficient for a human or tool to verify that the final state
  after Sub-Task 14 matches the pre-test state.

**Todo List:**
1. `hmc_lpars` with `lpar_name_or_uuid=<system-name>-lp3` — capture UUID, state,
   memory (min/desired/max), vCPU (min/desired/max), proc units, dedicated flag.
2. `hmc_lpar_summary` with `lpar_name_or_uuid=<system-name>-lp3` — capture OS info,
   RMC state, adapter list.
3. `hmc_get_lpar_description` — capture description string.
4. `hmc_get_lpar_msp` — capture MSP flag value.
5. `hmc_get_lpar_proc_compat` — capture current and pending proc compat mode.
6. `hmc_list_adapters` with `adapter_type=ClientNetworkAdapter` — list all CNAs
   (slot numbers, PVID, vswitch).
7. `hmc_run_command` with `cmd="lssyscfg -r lpar -m <system-name> --filter lpar_names=<system-name>-lp3"` —
   full CLI attribute dump; paste raw output into Baseline Record.
8. Record all values in the **Baseline Record** table below.

**Relevant Context:**
- `src/hmc_mcp/server_composite.py`
- `src/hmc_mcp/server_cli.py`

**Status:** [x] done

### Baseline Record — <system-name>-lp3

| Attribute | Pre-Test Value |
|---|---|
| UUID | <lp3-uuid> |
| State | running |
| Memory min/desired/max (MiB) | see lssyscfg |
| vCPU min/desired/max | see lssyscfg |
| Proc units min/desired/max | see lssyscfg |
| Dedicated procs | false (shared) |
| Uncapped | see lssyscfg |
| OS type | AIX/Linux (linux) |
| RMC state | see lpar_summary |
| Description | (empty) |
| MSP flag | false |
| Proc compat mode (desired) | default |
| Proc compat mode (current) | POWER9_base |
| Network adapters (slot → PVID) | see lpar_summary |
| Raw lssyscfg output | see local test-results.json (not committed; contains raw API data) |

```
name=<system-name>-lp3,lpar_id=3,lpar_env=aixlinux,state=Running,
resource_config=1,os=linux,os_version=Unknown,
logical_serial_num=<lpar-serial>,default_profile=default_profile,
curr_profile=P1-C4-T2-VF,...
(full output in local test-results.json, not committed)
```

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_lpars` (baseline) | ✅ PASS | UUID: <lp3-uuid> |
| `hmc_lpar_summary` (baseline) | ✅ PASS | State: running, OS: linux |
| `hmc_get_lpar_description` (baseline) | ✅ PASS | Value: (empty) |
| `hmc_get_lpar_msp` (baseline) | ✅ PASS | Value: false |
| `hmc_get_lpar_proc_compat` (baseline) | ✅ PASS | desired=default curr=POWER9_base (fixed: was using invalid pend_ field) |
| `hmc_list_adapters` (baseline) | ✅ PASS | Adapters listed |
| `hmc_run_command` (lssyscfg baseline) | ✅ PASS | Full attr string captured |

---

## Sub-Task 1 — Connectivity & Inventory (Read-Only)

**Intent:** Confirm HMC connectivity and exercise all pure inventory/read tools
before touching anything mutable.

**Expected Outcomes:**
- HMC console info returns version/network data
- Systems list includes the <system-name> CPC
- LPAR list shows all three partitions with correct states
- VIOS list (if any VIOS exists) returns entries
- Capacity and placement reports return numeric data

**Todo List:**
1. Run `hmc_console_info` — baseline connectivity check.
2. Run `hmc_systems` (no args) — list all managed systems; note the UUID of <system-name>.
3. Run `hmc_systems` with `system_name_or_uuid=<system-name>` — single-system lookup.
4. Run `hmc_lpars` (no args) — list all LPARs; confirm lp1/lp2/lp3 visible.
5. Run `hmc_lpars` with `lpar_name_or_uuid=<system-name>-lp3` — single-LPAR lookup.
6. Run `hmc_vios` (no args) — list VIOSes.
7. Run `hmc_capacity_report` — per-system resource summary.
8. Run `hmc_find_placement` with `desired_memory_mb=1024` — placement candidate search.
9. Run `hmc_find_system` with `name=<system name from step 2>` — exact name lookup.
10. Run `hmc_list_resources` with `resource_type=LogicalPartition` — generic resource lister.
11. Run `hmc_recent_jobs` with `limit=10` — job history.
12. Run `hmc_system_summary` with `system_name_or_uuid=<system-name>` — composite summary.
13. Run `hmc_lpar_summary` with `lpar_name_or_uuid=<system-name>-lp3` — per-LPAR composite summary.

**Relevant Context:**
- `src/hmc_mcp/server_system.py` — inventory tools
- `src/hmc_mcp/server_composite.py` — summary tools
- `src/hmc_mcp/server_provision.py` — placement tool

**Status:** [x] done

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_console_info` | ✅ PASS | HMC version and network info returned |
| `hmc_systems` (list) | ✅ PASS | System UUID: <system-uuid> |
| `hmc_systems` (single) | ✅ PASS | Single system by name returned |
| `hmc_lpars` (list) | ✅ PASS | All 3 LPARs visible |
| `hmc_lpars` (single) | ✅ PASS | lp3 found by name |
| `hmc_vios` | ✅ PASS | VIOS UUID: <vios-uuid> PartitionID=100 |
| `hmc_capacity_report` | ✅ PASS | Memory/CPU report returned |
| `hmc_find_placement` | ✅ PASS | Placement candidates returned |
| `hmc_find_system` | ✅ PASS | System found by name |
| `hmc_list_resources` | ✅ PASS | LPARs listed via generic resource endpoint |
| `hmc_recent_jobs` | ❌ FAIL | HTTP 400: "Unrecognized root REST type of Job" — wrong endpoint for this HMC version |
| `hmc_system_summary` | ✅ PASS | Composite summary with LPARs + VIOS returned |
| `hmc_lpar_summary` | ✅ PASS | lp3 summary with adapters returned |

---

## Sub-Task 2 — Network Inventory (Read-Only)

**Intent:** Exercise all virtual-networking read tools against the <system-name> system.

**Expected Outcomes:**
- Virtual switches, virtual networks (VLANs), and network bridges are listed.
- FC port and SEA adapter lists return for the system.

**Todo List:**
1. Run `hmc_list_virtual_switches` with `system_name_or_uuid=<system-name>`.
2. Run `hmc_list_virtual_networks` with `system_name_or_uuid=<system-name>`.
3. Run `hmc_list_network_bridges` with `system_name_or_uuid=<system-name>`.
4. Run `hmc_list_fc_ports` with `system_name_or_uuid=<system-name>` (SSH/CLI).
5. Run `hmc_list_sea_adapters` with `system_name_or_uuid=<system-name>` (SSH/CLI).
6. Run `hmc_list_adapters` with `lpar_name_or_uuid=<system-name>-lp3` and `adapter_type=ClientNetworkAdapter`.

**Relevant Context:**
- `src/hmc_mcp/server_network.py`
- `src/hmc_mcp/server_cli.py` (SSH/CLI tools)

**Status:** [x] done

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_list_virtual_switches` | ✅ PASS | Switches listed; SwitchID=0 captured |
| `hmc_list_virtual_networks` | ✅ PASS | Existing VLANs enumerated; VLAN 3000 chosen as unused test VLAN |
| `hmc_list_network_bridges` | ✅ PASS | Network bridges listed |
| `hmc_list_fc_ports` | ✅ PASS | FC/NPIV ports returned via SSH/CLI |
| `hmc_list_sea_adapters` | ✅ PASS | SEA adapters returned via SSH/CLI |
| `hmc_list_adapters` (CNA) | ✅ PASS | CNAs on lp3 listed |

---

## Sub-Task 3 — Storage & SSP Inventory (Read-Only)

**Intent:** Exercise storage and cluster/SSP read tools.

**Expected Outcomes:**
- Volume groups listed for any VIOS on the system.
- SSP/Cluster lists return (empty is acceptable).
- I/O slots and memory pool lists return.

**Todo List:**
1. Run `hmc_vios` to obtain VIOS UUID(s) on <system-name>.
2. If a VIOS exists: run `hmc_list_volume_groups` with `vios_name_or_uuid=<uuid>`.
3. Run `hmc_list_clusters` (global).
4. Run `hmc_shared_storage_pools` (no args).
5. Run `hmc_list_io_slots` with `system_name_or_uuid=<system-name>`.
6. Run `hmc_list_memory_pools` with `system_name_or_uuid=<system-name>`.

**Relevant Context:**
- `src/hmc_mcp/server_storage.py`
- `src/hmc_mcp/server_cli.py`

**Status:** [x] done

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_list_volume_groups` | ✅ PASS | Volume groups on VIOS returned |
| `hmc_list_clusters` | ✅ PASS | Empty list (no clusters configured) |
| `hmc_shared_storage_pools` | ✅ PASS | Empty list (no SSP configured) |
| `hmc_list_io_slots` | ✅ PASS | Physical I/O slots listed via SSH/CLI |
| `hmc_list_memory_pools` | ✅ PASS | Memory pools listed via SSH/CLI |

---

## Sub-Task 4 — LPAR Properties & Profile Inventory (Read-Only, SSH/CLI)

**Intent:** Exercise the SSH/CLI read tools that inspect individual LPAR configuration.

**Expected Outcomes:**
- Description, MSP flag, processor compat mode return for <system-name>-lp3.
- Profile backup list, proc compat modes for system, vNIC list all return.

**Todo List:**
1. Run `hmc_get_lpar_description` with `system_name_or_uuid=<system-name>`, `lpar_name_or_uuid=<system-name>-lp3`.
2. Run `hmc_get_lpar_msp` with `system_name_or_uuid=<system-name>`, `lpar_name_or_uuid=<system-name>-lp3`.
3. Run `hmc_get_proc_compat_modes` with `system_name_or_uuid=<system-name>`.
4. Run `hmc_get_lpar_proc_compat` with `system_name_or_uuid=<system-name>`, `lpar_name_or_uuid=<system-name>-lp3`.
5. Run `hmc_list_vnics` with `system_name_or_uuid=<system-name>`, `lpar_name_or_uuid=<system-name>-lp3`.

**Relevant Context:**
- `src/hmc_mcp/server_cli.py`
- `src/hmc_mcp/server_profiles.py`

**Status:** [x] done

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_get_lpar_description` | ✅ PASS | Description returned (empty) |
| `hmc_get_lpar_msp` | ✅ PASS | MSP=false |
| `hmc_get_proc_compat_modes` | ✅ PASS | Modes listed (default, POWER8, POWER9, etc.) |
| `hmc_get_lpar_proc_compat` | ✅ PASS | desired=default curr=POWER9_base |
| `hmc_list_vnics` | ✅ PASS | vNIC list returned (none configured) |

---

## Sub-Task 5 — Metrics & Templates (Read-Only)

**Intent:** Exercise PCM metrics read tools and partition template listing.

**Expected Outcomes:**
- PCM preferences return for the managed system.
- Partition template list returns (empty is acceptable).

**Todo List:**
1. Run `hmc_get_pcm_preferences` with `category=ManagedSystem` and `resource_name_or_uuid=<system-name>`.
2. Run `hmc_processed_metrics` with `category=ManagedSystem`, `resource_name_or_uuid=<system-name>`, `mode=list`.
3. Run `hmc_aggregated_metrics` with same args and `mode=list`.
4. Run `hmc_partition_templates` (no args).

**Relevant Context:**
- `src/hmc_mcp/server_metrics.py`
- `src/hmc_mcp/server_templates.py`

**Status:** [x] done

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_get_pcm_preferences` | ❌ FAIL | HTTP 406 — PCM not licensed/enabled on this HMC |
| `hmc_processed_metrics` (links) | ❌ FAIL | HTTP 403 — user lacks PCM authority |
| `hmc_aggregated_metrics` (links) | ❌ FAIL | HTTP 403 — user lacks PCM authority |
| `hmc_partition_templates` | ❌ FAIL | HTTP 406 — partition templates not supported on this HMC version |

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

**Status:** [x] done

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_users` | ❌ FAIL | HTTP 400: "Unrecognized root REST type of HmcUser" — web API path not supported |
| `hmc_list_password_policies` | ❌ FAIL | HTTP 400: "Unrecognized root REST type of HmcPasswordPolicy" |
| `hmc_get_ldap_config` | ❌ FAIL | HTTP 400: "Unrecognized root REST type of HmcLdapServer" |

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

**Status:** [x] done

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_run_command` (lshmc -V) | ✅ PASS | HMC version returned via SSH |
| `hmc_run_command` (lssyscfg -r sys) | ✅ PASS | System list returned via SSH |

---

## Sub-Task 8 — LPAR Lifecycle on <system-name>-lp3 (Mutating)

**Intent:** Create, inspect, modify, power off, and delete a scratch LPAR to
exercise the full partition lifecycle. <system-name>-lp3 is the authorized scratch
LPAR; we will power it off and back on as needed, then restore it afterward.
We also create a second scratch LPAR named **<system-name>-lp3-test** and delete it
at the end.

**Expected Outcomes:**
- `hmc_create_lpar` creates <system-name>-lp3-test successfully.
- `hmc_modify_lpar` updates memory on <system-name>-lp3-test.
- `hmc_power_on_lpar` powers on <system-name>-lp3-test (may fail if no network/boot
  device; job submission success is the observable result).
- `hmc_power_off_lpar` powers off <system-name>-lp3-test.
- `hmc_delete_lpar` removes <system-name>-lp3-test.

**Todo List:**
1. Get the system UUID: `hmc_systems` with `system_name_or_uuid=<system-name>`.
2. Create scratch: `hmc_create_lpar` with `system_name_or_uuid=<system-name>`,
   `name=<system-name>-lp3-test`, `desired_memory=512`, `max_memory=1024`,
   `desired_vcpus=1`, `max_vcpus=2`.
3. List: `hmc_lpars` — confirm <system-name>-lp3-test visible.
4. Modify: `hmc_modify_lpar` with `lpar_name_or_uuid=<system-name>-lp3-test`,
   `desired_memory=768`, `max_memory=1536`.
5. Summary: `hmc_lpar_summary` with `lpar_name_or_uuid=<system-name>-lp3-test`.
6. Power on: `hmc_power_on_lpar` with `lpar_name_or_uuid=<system-name>-lp3-test`,
   `wait=True`. (Note outcome; failure to boot is expected without install.)
7. Power off: `hmc_power_off_lpar` with `lpar_name_or_uuid=<system-name>-lp3-test`,
   `immediate=True`, `wait=True`.
8. Delete: `hmc_delete_lpar` with `lpar_name_or_uuid=<system-name>-lp3-test`.
9. Confirm gone: `hmc_lpars` — <system-name>-lp3-test should not appear.

**Relevant Context:**
- `src/hmc_mcp/server_power.py`

**Status:** [x] done

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_create_lpar` | ❌ FAIL | HTTP 406 "Console Internal Error" — REST LPAR create rejected by this HMC; all downstream lifecycle tests cascaded as FAIL |
| `hmc_modify_lpar` | ❌ FAIL | cascade: scratch LPAR not created |
| `hmc_lpar_summary` (post-modify) | ❌ FAIL | cascade |
| `hmc_power_on_lpar` | ❌ FAIL | cascade |
| `hmc_power_off_lpar` | ❌ FAIL | cascade |
| `hmc_delete_lpar` | ❌ FAIL | cascade |

---

## Sub-Task 9 — Virtual Networking Mutations (Mutating)

**Intent:** Create a virtual network (VLAN) and then delete it, exercising the
mutable networking tools without touching existing adapters on lp1/lp2.

**Expected Outcomes:**
- A new virtual network is created with a test VLAN ID.
- A network adapter is added to <system-name>-lp3-test (within sub-task 8 lifecycle
  or as part of this task using a short-lived test LPAR).
- Virtual network is deleted.

**Todo List:**
1. Identify an unused VLAN ID from `hmc_list_virtual_networks`.
2. Identify a virtual switch ID from `hmc_list_virtual_switches`.
3. Run `hmc_create_virtual_network` with the unused VLAN ID and vswitch ID.
4. Confirm: `hmc_list_virtual_networks` — new entry visible.
5. Create a short-lived LPAR (<system-name>-lp3-nettest) for adapter tests.
6. Run `hmc_add_network_adapter` with `lpar_name_or_uuid=<system-name>-lp3-nettest`,
   the test VLAN ID, and vswitch ID.
7. Confirm: `hmc_list_adapters` with `adapter_type=ClientNetworkAdapter`.
8. Note adapter UUID from step 7.
9. Run `hmc_delete_adapter` to remove it.
10. Delete virtual network: `hmc_delete_virtual_network`.
11. Delete scratch LPAR: `hmc_delete_lpar`.

**Relevant Context:**
- `src/hmc_mcp/server_network.py`
- `src/hmc_mcp/server_storage.py`

**Status:** [x] done

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_create_virtual_network` | ❌ FAIL | HTTP 406 — same pattern as hmc_create_lpar; REST create rejected |
| `hmc_add_network_adapter` | ❌ FAIL | cascade: no scratch LPAR or network created |
| `hmc_list_adapters` (post-add) | ❌ FAIL | cascade |
| `hmc_delete_adapter` | ⚠️ SKIP | no adapter UUID captured |
| `hmc_delete_virtual_network` | ⚠️ SKIP | no network UUID captured |

---

## Sub-Task 10 — LPAR Properties Mutations (Mutating, SSH/CLI)

**Intent:** Exercise the SSH/CLI mutating LPAR-property tools on <system-name>-lp3
(or a scratch LPAR), confirming changes are applied and can be read back.

**Expected Outcomes:**
- Description field set and read back matches.
- MSP flag toggled on/off.
- Processor compat mode set.
- Profile sync runs without error.
- Profile backup produces a file on the HMC.

**Todo List:**
1. Record current description: `hmc_get_lpar_description` for <system-name>-lp3.
2. Set description: `hmc_set_lpar_description` with a test string.
3. Read back: `hmc_get_lpar_description` — confirm change.
4. Restore original: `hmc_set_lpar_description` back to original value.
5. Record current MSP: `hmc_get_lpar_msp` for <system-name>-lp3.
6. Toggle MSP on: `hmc_set_lpar_msp` with `enabled=True` (or inverse of current).
7. Read back: `hmc_get_lpar_msp`.
8. Restore: `hmc_set_lpar_msp` back to original.
9. Get compat modes: `hmc_get_proc_compat_modes`.
10. Get current compat: `hmc_get_lpar_proc_compat` for <system-name>-lp3.
11. Set compat mode: `hmc_set_lpar_proc_compat` — use the system's default mode.
12. Sync profile: `hmc_sync_lpar_profile` with `system_name_or_uuid=<system-name>`, `lpar_name_or_uuid=<system-name>-lp3`.
13. Backup profiles: `hmc_backup_lpar_profiles` with `system_name_or_uuid=<system-name>`, `file_path=/tmp/lp3-profiles-test`.

**Relevant Context:**
- `src/hmc_mcp/server_cli.py`
- `src/hmc_mcp/server_profiles.py`

**Status:** [x] done

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_set_lpar_description` | ❌ FAIL | HSCLC63B: "Partition description cannot contain non-ASCII characters" — test string contained em dash/special chars |
| `hmc_get_lpar_description` (verify) | ✅ PASS | read-back succeeded |
| `hmc_set_lpar_msp` | ❌ FAIL | "msp attribute only valid for VIOS partition or profile" — lp3 is AIX/Linux not VIOS |
| `hmc_get_lpar_msp` (verify) | ✅ PASS | read-back succeeded |
| `hmc_set_lpar_proc_compat` | ❌ FAIL | "lpar_proc_compat_mode is an invalid attribute" — chsyscfg uses different field name |
| `hmc_sync_lpar_profile` | ✅ PASS | Profile sync succeeded |
| `hmc_backup_lpar_profiles` | ❌ FAIL | File /tmp/mcp-lp3-profiles-test already exists — need --force flag or unique filename |

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

**Status:** [x] done

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_create_user` | ❌ FAIL | HTTP 400: "Unrecognized root REST type of HmcUser" — web API path not supported on this HMC version |
| `hmc_modify_user` | ❌ FAIL | same root cause |
| `hmc_create_password_policy` | ❌ FAIL | same root cause |
| `hmc_modify_password_policy` | ❌ FAIL | same root cause |
| `hmc_delete_user` | ❌ FAIL | same root cause |
| `hmc_delete_password_policy` | ❌ FAIL | same root cause |

---

## Sub-Task 12 — PCM Metrics Mutation & Job Monitoring

**Intent:** Toggle a PCM preference, then restore it; also exercise job-wait
tooling by submitting a job and polling it to completion.

**Expected Outcomes:**
- PCM preference toggled and readable.
- `hmc_get_job` and `hmc_wait_for_job` operate against a real job UUID.

**Todo List:**
1. Read current PCM prefs: `hmc_get_pcm_preferences` with `category=ManagedSystem`, `resource_name_or_uuid=<system-name>`.
2. Toggle long-term monitoring: `hmc_set_pcm_preferences` with `long_term_monitor=True` (or inverse).
3. Read back: confirm change.
4. Restore original setting.
5. From a prior power-on/off job UUID (from sub-task 8 results), run `hmc_get_job` with that UUID.
6. Run `hmc_wait_for_job` with a completed job UUID — should return immediately.
7. Run `hmc_recent_jobs` to confirm job history is populated.

**Relevant Context:**
- `src/hmc_mcp/server_metrics.py`
- `src/hmc_mcp/server_system.py`

**Status:** [x] done

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_set_pcm_preferences` | ❌ FAIL | HTTP 406 — PCM not licensed/enabled |
| `hmc_get_job` | ⚠️ SKIP | no job UUID available (hmc_recent_jobs failed) |
| `hmc_wait_for_job` | ⚠️ SKIP | no job UUID |
| `hmc_recent_jobs` (post-tests) | ❌ FAIL | HTTP 400: "Unrecognized root REST type of Job" |

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
4. Run `hmc_provision_lpar` with `dry_run=True`, `system_name_or_uuid=<system-name>`,
   `name=<system-name>-lp3-dry`, `port_vlan_id=<test vlan>`, `vios_uuid=<uuid>`,
   `vios_partition_id=<id>`, `vios_slot=<slot>`, `storage_name=test-dry-disk`,
   `desired_memory=512`.

**Relevant Context:**
- `src/hmc_mcp/server_provision.py`
- `src/hmc_mcp/server_updates.py`

**Status:** [x] done

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_get_available_hmc_ptfs` | ❌ FAIL | HTTP 400: "Unknown extended attribute group SoftwareUpdate" — not supported on this HMC version |
| `hmc_provision_lpar` (dry_run) | ❌ FAIL | No VirtualNetwork with VLAN 3000 (create failed in ST9); dry-run validation detected missing network correctly |

---

## Sub-Task 14 — Restore <system-name>-lp3 to Baseline

**Intent:** After all mutating sub-tasks have run, compare the current state of
<system-name>-lp3 against the Baseline Record captured in Sub-Task 0 and issue
corrective tool calls for any attribute that differs.

**Expected Outcomes:**
- Memory, CPU, description, MSP flag, and proc compat mode all match
  pre-test values.
- No extra adapters remain (any test adapters added in sub-tasks 9–10 were
  already cleaned up per those sub-tasks; this step is a final audit).
- A final `hmc_lpar_summary` confirms the restored state.

**Todo List:**
1. Run `hmc_lpar_summary` with `lpar_name_or_uuid=<system-name>-lp3` — compare
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
9. Run `hmc_run_command` with `cmd="lssyscfg -r lpar -m <system-name> --filter lpar_names=<system-name>-lp3"` —
   final CLI dump; compare with baseline raw output in Sub-Task 0.
10. Final `hmc_lpar_summary` — record in Restored State table below.

**Relevant Context:**
- Sub-Task 0 Baseline Record
- `src/hmc_mcp/server_power.py`
- `src/hmc_mcp/server_cli.py`

**Status:** [x] done

### Restore Actions Taken

| Attribute | Baseline Value | Post-Test Value | Action Taken |
|---|---|---|---|
| Memory min/desired/max | see baseline | unchanged (no memory mutations ran) | none needed |
| vCPU min/desired/max | see baseline | unchanged | none needed |
| Proc units min/desired/max | see baseline | unchanged | none needed |
| Description | (empty) | unchanged (set_description failed) | restore attempt made |
| MSP flag | false | false (set_msp failed, non-VIOS) | no change |
| Proc compat mode | desired=default, curr=POWER9_base | unchanged (set_proc_compat failed) | restore attempt made |
| Adapters | per baseline | unchanged | none needed |

### Results

| Tool | Status | Notes |
|---|---|---|
| `hmc_lpar_summary` (post-test) | ✅ PASS | lp3 still running, state unchanged |
| `hmc_modify_lpar` (restore, if needed) | ⚠️ SKIP | no memory values in baseline dict (memory not mutated) |
| `hmc_set_lpar_description` (restore, if needed) | ✅ PASS | description restored to empty |
| `hmc_set_lpar_msp` (restore, if needed) | ❌ FAIL | "msp only valid for VIOS" — same as ST10 |
| `hmc_set_lpar_proc_compat` (restore, if needed) | ❌ FAIL | "lpar_proc_compat_mode is an invalid attribute" — same as ST10 |
| `hmc_sync_lpar_profile` | ✅ PASS | Profile synced |
| `hmc_run_command` (lssyscfg final) | ✅ PASS | Final dump matches baseline |
| `hmc_lpar_summary` (final confirm) | ✅ PASS | lp3 running, no changes from baseline |

---

## Issues Filed

| # | GitHub Issue | Tool(s) | Summary |
|---|---|---|---|
| 1 | [#95](https://github.com/randomparity/hmc-mcp/issues/95) | `hmc_recent_jobs`, `hmc_get_job`, `hmc_wait_for_job` | GET /rest/api/uom/Job returns HTTP 400 "Unrecognized root REST type of Job" |
| 2 | [#99](https://github.com/randomparity/hmc-mcp/issues/99) | `hmc_users`, `hmc_list_password_policies`, `hmc_get_ldap_config`, `hmc_create_user`, all user/policy tools | GET/POST /rest/api/web/Hmc* returns HTTP 400 "Unrecognized root REST type" — web API not supported on this HMC version |
| 3 | [#96](https://github.com/randomparity/hmc-mcp/issues/96) | `hmc_create_lpar`, `hmc_create_virtual_network` | PUT/POST REST create calls return HTTP 406 "Console Internal Error" — REST write path not working |
| 4 | [#98](https://github.com/randomparity/hmc-mcp/issues/98) | `hmc_get_pcm_preferences`, `hmc_processed_metrics`, `hmc_aggregated_metrics`, `hmc_set_pcm_preferences` | PCM endpoints return HTTP 406/403 — PCM not licensed or user lacks authority |
| 5 | [#97](https://github.com/randomparity/hmc-mcp/issues/97) | `hmc_partition_templates` | HTTP 406 — template API not supported on this HMC version |
| 6 | [#102](https://github.com/randomparity/hmc-mcp/issues/102) | `hmc_set_lpar_msp` | "msp attribute only valid for VIOS" — tool should validate partition type before attempting |
| 7 | [#101](https://github.com/randomparity/hmc-mcp/issues/101) | `hmc_set_lpar_proc_compat` | chsyscfg rejects lpar_proc_compat_mode as invalid attribute — wrong chsyscfg field name |
| 8 | [#100](https://github.com/randomparity/hmc-mcp/issues/100) | `hmc_set_lpar_description` | Test string contained non-ASCII characters (em dash) — test string bug; also reveals no validation before SSH call |
| 9 | [#103](https://github.com/randomparity/hmc-mcp/issues/103) | `hmc_backup_lpar_profiles` | Fails if output file already exists — needs --force flag or unique filename handling |
| 10 | [#104](https://github.com/randomparity/hmc-mcp/issues/104) | `hmc_get_available_hmc_ptfs` | HTTP 400 "Unknown extended attribute group SoftwareUpdate" — not supported on this HMC version |

---

## Test Run Summary (2026-08-12)

| Category | Count |
|---|---|
| Total tool calls | 97 |
| ✅ PASS | 49 |
| ❌ FAIL | 43 |
| ⚠️ SKIP | 5 |

**Root cause breakdown of failures:**
- HTTP 400 "Unrecognized root REST type" — HMC web API endpoint not available (affects Job, HmcUser, HmcPasswordPolicy, HmcLdapServer, ManagementConsole SoftwareUpdate group): **13 failures**
- HTTP 406 on REST write path — LPAR/network/PCM create/update rejected: **8 failures** + **9 cascade failures**
- HTTP 403 PCM authority: **2 failures**
- SSH/CLI wrong attribute names (`lpar_proc_compat_mode`, `msp`): **5 failures**
- Backup file collision: **1 failure**
- Non-ASCII in test description string: **1 failure**
- Provision dry-run detected missing network (expected): **1 failure**

**lp3 state after testing:** ✅ Running, no changes from baseline. Profile synced.

---

## Final Status

- [x] Sub-Task 0 — Capture <system-name>-lp3 Baseline Configuration
- [x] Sub-Task 1 — Connectivity & Inventory
- [x] Sub-Task 2 — Network Inventory
- [x] Sub-Task 3 — Storage & SSP Inventory
- [x] Sub-Task 4 — LPAR Properties & Profile Inventory
- [x] Sub-Task 5 — Metrics & Templates
- [x] Sub-Task 6 — User & Policy Inventory
- [x] Sub-Task 7 — CLI Escape Hatch
- [x] Sub-Task 8 — LPAR Lifecycle
- [x] Sub-Task 9 — Virtual Networking Mutations
- [x] Sub-Task 10 — LPAR Properties Mutations
- [x] Sub-Task 11 — User Administration
- [x] Sub-Task 12 — PCM Metrics & Job Monitoring
- [x] Sub-Task 13 — Provision Dry Run & Updates Check
- [x] Sub-Task 14 — Restore <system-name>-lp3 to Baseline
