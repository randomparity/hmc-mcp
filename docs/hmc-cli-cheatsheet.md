# HMC CLI Cheatsheet

This cheatsheet covers the IBM HMC CLI commands used by hmc-mcp production code
and repository tooling.  It is a practical reference, not a reproduction of IBM
documentation.  For exhaustive syntax, options, and return codes see the
[IBM HMC commands reference](https://www.ibm.com/docs/en/power10/7063-CR1?topic=hmc-commands)
or run `<command> --help` on the HMC itself.

> **SSH execution** — every command here runs over SSH as `hscroot` (or the
> configured HMC user).  hmc-mcp opens one SSH connection per command via
> asyncssh; there is no persistent shell session.  `hmc_run_command` exposes
> this transport directly to callers.

> **CLI-name vs UUID resolution** — the HMC CLI accepts *CLI names*
> (`-m <system>`, `-p <partition>`), not REST UUIDs.  hmc-mcp resolves REST
> UUIDs to CLI names before issuing any SSH command: it tries the REST API
> first and falls back to `lssyscfg -r sys/lpar -F uuid,name` over SSH when
> the REST API is unreachable.

> **UUID field name** — the correct lower-case field name is `uuid` on both
> HMC V10 and V11.  `UUID` (upper-case) is rejected with *invalid attribute*.

---

## Read-only commands

These commands only query HMC or system state.  They are safe to run at any
time without risk of disrupting workloads.

### `lshmc` — HMC version and settings

```
lshmc -V          # firmware version string  (used by read_sriov_environment)
lshmc -v          # full VPD
lshmc -n          # network settings
```

**Repository use:** `read_sriov_environment` calls `lshmc -V` to obtain the
HMC release string that drives SR-IOV capability admission.  `scripts/live_test_runner.py`
calls it as a smoke-test sanity check.

---

### `lssyscfg` — list system, LPAR, and profile configuration

```
# list all managed systems with key identity fields
lssyscfg -r sys -F name,type_model,serial_num,ipaddr,state

# resolve a system UUID to its CLI name (used as REST→SSH fallback)
lssyscfg -r sys -F uuid,name

# list all LPARs on a system
lssyscfg -r lpar -m <system> -F name,lpar_id,lpar_env,state

# filter to one LPAR by name
lssyscfg -r lpar -m <system> --filter lpar_names=<lpar> -F name,uuid,state

# resolve an LPAR UUID to its CLI PartitionName
lssyscfg -r lpar -m <system> -F uuid,name

# read a single LPAR attribute
lssyscfg -r lpar -m <system> --filter lpar_names=<lpar> -F description
lssyscfg -r lpar -m <system> --filter lpar_names=<lpar> -F msp,lpar_env
lssyscfg -r lpar -m <system> --filter lpar_names=<lpar> \
    -F desired_lpar_proc_compat_mode,curr_lpar_proc_compat_mode

# list processor compatibility modes a system supports
lssyscfg -r sys -m <system> -F lpar_proc_compat_modes

# list all profiles for one LPAR
lssyscfg -r prof -m <system> --filter lpar_names=<lpar> \
    -F name,lpar_name,proc_mode,desired_procs,desired_mem

# read virtual SCSI adapters from a VIOS profile
lssyscfg -r prof -m <system> --filter lpar_names=<vios>,profile_names=<profile> \
    -F name,virtual_scsi_adapters

# look up an LPAR's currently active profile name
lssyscfg -r lpar -m <system> --filter lpar_names=<lpar> -F curr_profile

# SR-IOV: read LPAR state and RMC connectivity for admission checks
lssyscfg -r lpar -m <system> --filter lpar_names=<lpar> \
    -F name,lpar_id,state,rmc_state --header

# SR-IOV: read an LPAR profile's configured SR-IOV logical ports
lssyscfg -r prof -m <system> \
    --filter lpar_names=<lpar>,profile_names=<profile> \
    -F name,sriov_eth_logical_ports --header
```

**`-r` resource types used:** `sys` (managed system), `lpar` (partition), `prof` (partition profile).

**`-F` selects output columns** (delimiter-separated, no spaces).  Without
`-F` the HMC returns all attributes as `key=value` pairs on one line per
object.  With `--header` the first output line is the field names (required by
`parse_hmc_delimited_rows`).

**`--filter`** narrows the result set.  Format: `"filter_name=value1,value2"`.
Common filter names: `lpar_names`, `lpar_ids`, `profile_names`.

**Repository use:** UUID→name resolution (`_ssh_system_name`, `_ssh_lpar_name`);
LPAR description, MSP, proc-compat reads (`get_lpar_description`, `get_lpar_msp`,
`get_lpar_proc_compat`); SR-IOV LPAR-state and profile reads
(`read_sriov_lpar_state`, `read_sriov_profile_ports`); live-test baseline and
final inventory in `scripts/live_test_runner.py`.

---

### `lshwres` — list hardware resources

```
# processor resources at system level
lshwres -r proc -m <system> --level sys \
    -F installed_sys_proc_units,curr_avail_sys_proc_units

# processor resources per LPAR (fields: curr_proc_mode, curr_procs, run_procs, …)
lshwres -r proc -m <system> --level lpar

# memory at system level
lshwres -r mem -m <system> --level sys \
    -F installed_sys_mem,curr_avail_sys_mem,mem_region_size

# memory per LPAR
lshwres -r mem -m <system> --level lpar -F lpar_name,curr_mem

# physical I/O slots (all)
lshwres -r io --rsubtype slot -m <system>

# physical I/O slots — selected columns with header (used by dedicated-slot inventory)
lshwres -r io --rsubtype slot -m <system> \
    -F drc_index,description,lpar_name --header

# virtual Fibre Channel (NPIV) adapters — one row per adapter, all LPARs
lshwres -r virtualio --rsubtype fc --level lpar -m <system>

# virtual Fibre Channel — filtered to one LPAR
lshwres -r virtualio --rsubtype fc --level lpar -m <system> \
    --filter lpar_names=<lpar>

# Shared Ethernet Adapter (SEA) virtual Ethernet ports
lshwres -r virtualio --rsubtype eth --level lpar -m <system> \
    -F lpar_name,port_vlan_id,vswitch,state,trunk_priority

# vNIC (SR-IOV-backed Virtual NIC) adapters
lshwres -r virtualio --rsubtype vnic --level lpar -m <system> \
    --filter lpar_names=<lpar>

# shared memory pools (AMS/suspend-resume — not all systems support this)
lshwres -r mempool -m <system>

# SR-IOV adapters — identity and port counts
lshwres -r sriov --rsubtype adapter -m <system> \
    -F adapter_id,slot_id,config_state,functional_state,\
phys_ports,logical_ports,adapter_max_logical_ports,sriov_status --header

# SR-IOV physical ports (requires --level roce)
lshwres -r sriov --rsubtype physport -m <system> --level roce \
    --filter adapter_ids=<id> \
    -F adapter_id,phys_port_id,phys_port_type,state,\
config_logical_ports,phys_port_max_logical_ports,curr_eth_logical_ports --header

# SR-IOV configured logical ports (--level eth, filtered by adapter)
lshwres -r sriov --rsubtype logport -m <system> --level eth \
    --filter adapter_ids=<id> \
    -F config_id,lpar_name,lpar_id,lpar_state,adapter_id,logical_port_id,\
logical_port_type,phys_port_id,functional_state,capacity,max_capacity --header

# SR-IOV unconfigured logical port slots (no --level, returns key=value rows)
lshwres -r sriov --rsubtype logport -m <system>
```

**`--level`** selects the granularity or sub-view: `sys` (system totals),
`lpar` (per-partition), `slot`, `pool`, `roce` (SR-IOV physical ports),
`eth` (SR-IOV configured Ethernet logical ports).

**`--rsubtype`** further qualifies `-r io` and `-r virtualio`: `slot`, `bus`,
`fc`, `eth`, `vnic`, `scsi`, `serial`, `vswitch`, …; and `-r sriov`: `adapter`,
`physport`, `logport`.

**`-F`** selects output fields.  Without `-F` the HMC returns all attributes
as `key=value` pairs (parsed by `_parse_lshwres_output`).  With `--header` the
first line is the field names (required by `parse_hmc_delimited_rows`).

**`--filter`** filters rows.  Format: `"filter_name=value1,value2"`.  Common
filter names for `lshwres`: `lpar_names`, `lpar_ids`, `adapter_ids`.

**Repository use:** I/O slot and SR-IOV inventory (`list_io_slots`,
`list_dedicated_pcie_slot_rows`, `list_sriov_adapter_rows`,
`list_sriov_physical_port_rows`, `list_sriov_configured_logical_port_rows`,
`list_sriov_unconfigured_logical_port_rows`); virtual FC/Ethernet/vNIC adapters
(`list_fc_ports`, `list_sea_adapters`, `list_vnics`); shared memory pools
(`list_memory_pools`); HMC version + system model for admission
(`read_sriov_environment`); place_lpars tooling (`lshwres -r proc/mem --level sys`).

---

### `viosvrcmd` — run a command on a VIOS

```
viosvrcmd -m <system> -p <vios-name> -c "<ioscli-command>"
viosvrcmd -m <system> --id <vios-id> -c "<ioscli-command>"

# example: list VSCSI/NPIV mappings
viosvrcmd -m <system> -p <vios-name> -c "lsmap -all"
viosvrcmd -m <system> -p <vios-name> -c "lsmap -all -npiv"
```

**`-c`** takes an ioscli command *without* the `ioscli` prefix.  The command
string is run inside the VIOS shell by the HMC; do not prefix it with `ioscli`.

**Repository use:** `scripts/live_test_runner.py` uses `viosvrcmd` during
live-test cleanup to inspect VIOS state.

---

### `lsrefcode` — list partition or system reference codes

```
lsrefcode -r lpar -m <system> --filter lpar_names=<lpar> -n 10
lsrefcode -r sys  -m <system> -s p -n 20
```

**Repository use:** listed in `/usr/hmcrbin` inventory; not called by
production code today but available via `hmc_run_command`.

---

### `lslparmigr` — list Live Partition Mobility eligibility

```
lslparmigr -r lpar  -m <source> -t <target> -p <lpar>
lslparmigr -r sriov -m <source>
```

**Repository use:** listed in `/usr/hmcrbin` inventory; available via
`hmc_run_command`.

---

### `lsviosbk` — list VIOS backups on the HMC

```
# list one VIOS's backup catalog as explicit CSV
lsviosbk --filter "vios_uuids=<uuid>" -F name,type --header
```

**Repository use:** `hmc_list_vios_backups` resolves its VIOS selector to a UUID,
runs this command, and returns the catalog's `name` and `type` fields.

---

## State-changing commands

These commands alter HMC or partition state.  **Read the current state first**
(`lssyscfg`/`lshwres`) and confirm system and partition names before running.

### `mksyscfg` — create an LPAR or profile

```
# create an LPAR with all_resources (simplest form)
mksyscfg -r lpar -m <system> \
    -i "name=<lpar>,lpar_env=aixlinux,profile_name=default_profile,all_resources=1"

# create an LPAR with explicit shared-processor resources
mksyscfg -r lpar -m <system> \
    -i "name=<lpar>,lpar_env=aixlinux,profile_name=default_profile,\
min_mem=256,desired_mem=4096,max_mem=8192,\
proc_mode=shared,sharing_mode=uncap,\
min_proc_units=0.1,desired_proc_units=0.1,max_proc_units=2.0,\
min_procs=1,desired_procs=1,max_procs=2"

# create an LPAR with dedicated processors
mksyscfg -r lpar -m <system> \
    -i "name=<lpar>,lpar_env=aixlinux,profile_name=default_profile,\
min_mem=256,desired_mem=4096,max_mem=8192,\
proc_mode=ded,sharing_mode=keep_idle_procs,\
min_procs=1,desired_procs=1,max_procs=2"

# create a VIOS partition
mksyscfg -r lpar -m <system> \
    -i "name=<vios>,lpar_env=vioserver,profile_name=default_profile,all_resources=1"
```

**`-i`** takes a single attribute record: comma-separated `attribute=value`
pairs.  Commas, equals signs, and double quotes are record delimiters — they
must not appear inside values.  See [Attribute record grammar](#attribute-record-grammar).

**`lpar_env`** values: `aixlinux` (AIX or Linux), `vioserver` (VIOS),
`os400` (IBM i).

**Repository use:** `create_lpar_via_cli` in `ssh_commands.py`; REST 406
fallback in `hmc_provision_lpar`; place_lpars tooling.

---

### `chsyscfg` — change LPAR or profile configuration

```
# set LPAR description (no REST equivalent)
chsyscfg -r lpar -m <system> -i "name=<lpar>,description=<text>"

# set MSP flag on a VIOS partition
chsyscfg -r lpar -m <system> -i "name=<vios>,msp=1"

# set processor compatibility mode
chsyscfg -r lpar -m <system> \
    -i "name=<lpar>,lpar_proc_compat_mode=POWER10"

# sync running config back to the active profile
chsyscfg -r lpar -m <system> -i "name=<lpar>,sync_curr_profile=1"

# rename an LPAR
chsyscfg -r lpar -m <system> \
    -i "name=<current-name>,new_name=<new-name>"

# add a physical I/O slot to a profile
chsyscfg -r prof -m <system> \
    -i "name=<profile>,io_slots+=<drc_index>//0,lpar_name=<lpar>"

# remove a physical I/O slot from a profile
chsyscfg -r prof -m <system> \
    -i "name=<profile>,io_slots-=<drc_index>//0,lpar_name=<lpar>"

# clear all SR-IOV logical ports from a profile
chsyscfg -r prof -m <system> \
    -i "name=<profile>,lpar_name=<lpar>,sriov_eth_logical_ports=none"
```

**`io_slots+=`/`io_slots-=`** are list-append/remove operators: they add or
remove one DRC-index entry rather than replacing the full list.  The `//0`
suffix is the required "not required, not shared" flag.

**Repository use:** `set_lpar_description`, `set_lpar_msp`, `set_lpar_proc_compat`,
`sync_lpar_profile`, `assign_profile_io_slot`, `unassign_profile_io_slot`,
`unassign_sriov_logical_port_profile`; place_lpars tooling.

---

### `chhwres` — change hardware resources

```
# toggle an SR-IOV adapter between SR-IOV mode and dedicated mode
chhwres -r sriov -m <system> -o s --id <adapter_id> \
    -a "sriov_adapter_mode=sriov"
chhwres -r sriov -m <system> -o s --id <adapter_id> \
    -a "sriov_adapter_mode=dedicated"

# add a vNIC to an LPAR (SR-IOV-backed)
chhwres -r virtualio --rsubtype vnic -o a -m <system> \
    --filter lpar_names=<lpar> \
    -a "capacity=<pct>,vswitch_name=<vswitch>,port_vlan_id=<vid>"

# remove a vNIC from an LPAR
chhwres -r virtualio --rsubtype vnic -o r -m <system> \
    --filter lpar_names=<lpar> \
    -a "vnic_id=<id>"

# remove a shared memory pool
chhwres -r mempool -m <system> -o r -a <pool_name>

# dynamically assign an SR-IOV logical port to a running LPAR
chhwres -r sriov --rsubtype logport -m <system> -o a -p <lpar> \
    -a "adapter_id=<id>,phys_port_id=<pid>,logical_port_id=<lid>,\
logical_port_type=eth,capacity=<pct>"
```

**`-o`** is the operation: `a` (add), `r` (remove), `s` (set/modify), `m`
(move), `c` (create), `d` (delete).

**`-a`** takes an attribute string similar to `-i` in `chsyscfg`.

**Repository use:** `set_sriov_adapter_mode`, `add_vnic`, `remove_vnic`,
`remove_memory_pool`, `assign_sriov_logical_port_dynamic`; place_lpars `chhwres -r mem/proc`.

---

### `rmsyscfg` — remove an LPAR or profile

```
# delete a Not Activated LPAR by name
rmsyscfg -r lpar -m <system> -n <lpar-name>

# delete by partition ID
rmsyscfg -r lpar -m <system> --id <lpar-id>
```

**Safety:** the HMC refuses to delete a running LPAR; the partition must be in
*Not Activated* state.  Close any open virtual terminal (`rmvterm`) before
deleting.

**Repository use:** place_lpars cleanup loops; available via `hmc_run_command`.

---

### `bkprofdata` — back up LPAR profiles to an HMC file

```
bkprofdata -m <system> -f <hmc-local-path>
bkprofdata -m <system> -f <hmc-local-path> --force   # overwrite existing
```

**`-f`** is a path on the **HMC filesystem**, not the local machine.  There is
no automatic retrieval; use `cpfile` or `scp` to copy the file off the HMC
afterwards.

**Repository use:** `backup_lpar_profiles` (`ssh_commands.py`); exposed as
`hmc_backup_lpar_profiles`.

---

### `rstprofdata` — restore LPAR profiles from an HMC file

```
rstprofdata -m <system> -l 1 -f <hmc-local-path>   # full restore from file
rstprofdata -m <system> -l 2 -f <hmc-local-path>   # merge, backup wins
rstprofdata -m <system> -l 3 -f <hmc-local-path>   # merge, current wins
rstprofdata -m <system> -l 4                        # initialize (wipe)
```

**`-l`** is the restore type: `1` full, `2` merge-backup-wins, `3`
merge-current-wins, `4` initialize (no `-f` needed).

**Warning:** `-l 1` and `-l 4` overwrite the current profile configuration.

**Repository use:** `restore_lpar_profiles` (`ssh_commands.py`); exposed as
`hmc_restore_lpar_profiles`.

---

### `mkviosbk` — create a VIOS backup

```
mkviosbk -t viosioconfig -m <system> --uuid <vios-uuid> -f <backup-file>
mkviosbk -t vios         -m <system> --uuid <vios-uuid> -f <backup-file>
mkviosbk -t ssp          -m <system> --uuid <vios-uuid> -f <backup-file>
```

**`-t`** backup type: `viosioconfig` (I/O config), `vios` (full VIOS),
`ssp` (Shared Storage Pool).  The backup file is stored on the HMC.

**Repository use:** `hmc_backup_vios` resolves its VIOS selector to a UUID and
uses this command with the requested backup type.

```python
hmc_backup_vios(
    "server-name",
    "00000000-0000-0000-0000-000000000003",
    backup_name="nightly-vios",
    backup_type="vios",
)
```

`backup_name` is keyword-only. A direct system name plus VIOS UUID is SSH-ready;
a system UUID requires REST to resolve its unique MTMS identity and has no
`lssyscfg` fallback.

The required managed-system and VIOS selectors are both authorization targets.
Narrow access-policy grants for `hmc_backup_vios` must include matching
`managed_system` and `vios` entries; existing VIOS-only grants must add the
managed-system target before using the replacement interface.

---

### `rstviosbk` — restore a VIOS backup

```
rstviosbk -t viosioconfig -m <system> --uuid <vios-uuid> -f <backup-file>
rstviosbk -t ssp          -m <system> --uuid <vios-uuid> -f <backup-file> -r
```

**`-r`** restarts the VIOS if required after restore.  Only `viosioconfig` and
`ssp` types are restorable; a full `vios` restore requires NIM/reinstall.

**Repository use:** `hmc_restore_vios` resolves its VIOS selector to a UUID and
uses this command; `-r` is included only when `restart_if_required` is true.

```python
hmc_restore_vios(
    "server-name",
    "00000000-0000-0000-0000-000000000003",
    "nightly-vios",
    backup_type="viosioconfig",
    restart_if_required=True,
)
```

`backup_type` is required and keyword-only. Restore uses the same selector-routing
rules as backup: only a direct system name plus VIOS UUID bypasses REST.

---

### `mkvterm` — open a virtual terminal

```
mkvterm -m <system> -p <lpar-name>
mkvterm -m <system> --id <lpar-id>
```

**Repository use:** place_lpars tooling; available via `hmc_run_command`.

---

### `rmvterm` — close a virtual terminal

```
rmvterm -m <system> -p <lpar-name>
rmvterm -m <system> --id <lpar-id>
```

Must be run before `rmsyscfg` when an open virtual terminal is attached to the
LPAR.

**Repository use:** place_lpars cleanup loops; `scripts/live_test_runner.py`
cleanup; available via `hmc_run_command`.

---

### `chsysstate` — change partition or system power state

```
# activate (power on) an LPAR
chsysstate -r lpar -m <system> -n <lpar> -o on

# shut down an LPAR immediately
chsysstate -r lpar -m <system> -n <lpar> -o shutdown --immed

# shut down and restart
chsysstate -r lpar -m <system> -n <lpar> -o shutdown --immed --restart

# shut down a managed system
chsysstate -r sys  -m <system> -o off
```

**`-o`** operation options for `-r lpar`: `on`, `onstandby`, `off`, `shutdown`,
`osshutdown`, `dumprestart`, `rebuild`, `recover`, …

**Repository use:** place_lpars tooling; available via `hmc_run_command`.

---

### `migrlpar` — Live Partition Mobility

```
# validate a migration
migrlpar -o v -m <source> -t <target> -p <lpar>

# perform a live migration
migrlpar -o m -m <source> -t <target> -p <lpar>
```

**Repository use:** available via `hmc_run_command`; `lslparmigr` checks
eligibility first.

---

## Attribute record grammar

`mksyscfg -i` and `chsyscfg -i` accept a single argument containing a
comma-separated list of `attribute=value` pairs:

```
"name=my-lpar,lpar_env=aixlinux,desired_mem=4096"
```

Three characters are structure delimiters and **must not appear in values**:

| Character | Role |
|-----------|------|
| `,` | separates one attribute from the next |
| `=` | separates an attribute name from its value |
| `"` | opens a quoted region (IBM's own escaping) |

hmc-mcp's `build_attribute_record` enforces this rule and raises
`HMCCLIError` for any value that contains these characters, a control
character, or a NUL.  The resulting record is then wrapped in
`shlex.quote` to protect it from the remote shell.

---

## Common option quick-reference

| Option | Meaning |
|--------|---------|
| `-m <system>` | Target managed system (CLI name, not UUID) |
| `-r <resource>` | Resource type: `sys`, `lpar`, `prof`, `proc`, `mem`, `io`, `sriov`, `virtualio`, `mempool`, … |
| `--rsubtype` | Sub-type under `-r`: `slot`, `fc`, `eth`, `vnic`, `adapter`, `physport`, `logport`, … |
| `--level` | Granularity: `sys`, `lpar`, `slot`, `roce`, `eth` |
| `--filter "…"` | Row filter: `"attr=val1,val2"` or `"attr1=val,attr2=val"` |
| `-F [fields]` | Select output columns (comma-separated, no spaces) |
| `--header` | Print field names as the first output line (with `-F`) |
| `-i "…"` | Attribute record for `mksyscfg`/`chsyscfg` |
| `-a "…"` | Attribute string for `chhwres` |
| `-o <op>` | Operation: `a` add, `r` remove, `s` set, `m` move, … |

---

## Processor compatibility modes by generation

Validated against live P9, P10, and P11 managed systems on HMC V10R3M1060 and HMC V11R2M1120:

| System type | Example model | Modes supported |
|------------|---------------|----------------|
| POWER9 | 9008-22L, 9009-42G | `default`, `POWER7`, `POWER8`, `POWER9`, `POWER9_base` |
| POWER10 | 9105-22A, 9824-42A | `default`, `POWER8`, `POWER9`, `POWER9_base`, `POWER10` |
| POWER11 | 9043-MRU | `default`, `POWER9`, `POWER9_base`, `POWER10`, `POWER11` |

---

## Further reading

- [IBM HMC commands reference (Power10)](https://www.ibm.com/docs/en/power10/7063-CR1?topic=hmc-commands)
- [`src/hmc_mcp/ssh_commands.py`](../src/hmc_mcp/ssh_commands.py) — all SSH command construction
- [`src/hmc_mcp/server_vios.py`](../src/hmc_mcp/server_vios.py) — VIOS backup commands
- [`scripts/live_test_runner.py`](../scripts/live_test_runner.py) — live-test uses of HMC CLI
