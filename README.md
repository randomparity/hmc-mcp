# hmc-mcp

MCP server and CLI for the **IBM Hardware Management Console (HMC) REST API**
(`/rest/api/uom/...`). It lets you — or an AI agent over MCP — inventory
Power systems, inspect LPARs/VIOS, and submit jobs such as power on/off.

## Stack

- **Python ≥3.11**, managed with [uv](https://docs.astral.sh/uv/). The project supports every
  stable, non-EOL CPython release at or above that floor.
- **MCP server**: [FastMCP](https://gofastmcp.com/) (stdio or streamable HTTP)
- **CLI**: [Typer](https://typer.tiangolo.com/) + Rich tables
- **REST transport**: httpx (async), XML parsed with defusedxml
- **CLI passthrough**: [asyncssh](https://asyncssh.readthedocs.io) — tools that
  shell out to HMC CLI commands (`lssyscfg`, `lshwres`, `chsyscfg`, ...) over
  SSH

## Contributing, security, and license

See [Contributing](CONTRIBUTING.md) for the development and pull-request path. Report suspected
vulnerabilities through the private channel in the [Security policy](SECURITY.md). This project is
available under the [License](LICENSE).

## Install

```bash
cd ~/src/hmc-mcp
uv sync
```

## Configure

Configuration priority (highest to lowest): **CLI flags > `HMC_*` env vars > TOML profile**.

### TOML profile (recommended for multi-HMC setups)

Create `~/.config/hmc-mcp/config.toml` (Linux / macOS `~/Library/Application Support/hmc-mcp/config.toml` / Windows `%APPDATA%/hmc-mcp/config.toml`):

```toml
default_profile = "prod"

[profiles.prod]
host = "hmc.example.com"
user = "admin"
password_env = "HMC_PROD_PASSWORD"   # resolved from the environment at runtime  # pragma: allowlist secret

[profiles.dev]
host = "hmc-dev.example.com"
user = "devadmin"
password = "devpassword"              # or store inline for non-production  # pragma: allowlist secret
```

Select a profile with `--profile <name>` or `HMC_PROFILE=<name>`.
`password_env` keeps secrets out of the file; `password` is accepted for convenience.

### Environment variables (single-HMC / MCP server)

| Setting           | Env var              | CLI flag          | Default   |
|-------------------|----------------------|-------------------|-----------|
| Profile           | `HMC_PROFILE`        | `--profile`       | —         |
| HMC host / IP     | `HMC_HOST`           | `--host`          | —         |
| REST port         | `HMC_PORT`           | —                 | `12443`   |
| User              | `HMC_USER`           | `--user, -u`      | —         |
| Password          | `HMC_PASSWORD`       | `--password, -p`  | —         |
| Verify TLS        | `HMC_VERIFY_SSL`     | `--verify-ssl`    | `false`   |
| HTTP timeout (s)  | `HMC_TIMEOUT`        | —                 | `60.0`    |
| SSH timeout (s)   | `HMC_SSH_TIMEOUT`    | —                 | `300.0`   |
| SSH key file      | `HMC_SSH_KEY_FILE`   | —                 | —         |
| Audit memento     | `HMC_AUDIT_MEMENTO`  | —                 | `hmc-mcp` |
| Schema version    | `HMC_SCHEMA_VERSION` | —                 | _(unset)_ |

See [`docs/environment-variables.md`](docs/environment-variables.md) for the
full reference, including descriptions and usage notes.

HMCs ship self-signed certificates, so TLS verification is off by default and
`hmc-mcp` warns on every logon while it stays off. To verify the HMC
certificate, install its CA locally and set `HMC_VERIFY_SSL=true`
(`--verify-ssl`) — otherwise the HMC credentials are at risk of
man-in-the-middle interception.

## HMC version compatibility

`hmc-mcp` targets **HMC V8 through V11** and all the POWER generations they
manage. All uom XML documents are written with `schemaVersion="V1_0"` — the
floor every supported HMC understands — so create/modify operations succeed
regardless of firmware age.

| HMC version | POWER generations managed | uom schema floor |
|-------------|--------------------------|------------------|
| HMC V8      | POWER6, POWER7, POWER8   | V1_0             |
| HMC V9      | POWER7, POWER8, POWER9   | V1_0             |
| HMC V10     | POWER8, POWER9, POWER10  | V1_0             |
| HMC V11     | POWER9, POWER10, POWER11 | V1_0             |

**`HMC_SCHEMA_VERSION` — leave this unset for normal operation.**
`hmc-mcp` omits the `X-HMC-Schema-Version` request header from all write
paths (`PUT`/`POST`) regardless of this setting — some HMC firmware versions
return HTTP 406 on every UOM write when that header is present. The variable
only affects `GET` requests. Set it only if you are debugging schema
negotiation on a specific HMC read path; it has no effect on LPAR creation,
adapter configuration, storage operations, or any other mutating call.
See [`docs/environment-variables.md`](docs/environment-variables.md) for all
supported variables.

### Firmware write-path compatibility

Some HMC V10 firmware builds return HTTP 406 for all UOM write paths — even
without the schema-version header — for child-resource endpoints such as
`ClientNetworkAdapter` and `VirtualSCSIClientAdapter` PUT. On those builds:

- **LPAR creation** (`hmc_create_lpar`, `hmc_provision_lpar`): automatically
  falls back to `mksyscfg` over SSH. `HMC_PASSWORD` (or `HMC_SSH_KEY_FILE`)
  must be set for SSH auth; the fallback is transparent to the caller.
- **Virtual adapter attachment** (`hmc_add_network_adapter`,
  `hmc_add_vscsi_adapter`): no automatic fallback. Configure adapter profiles
  via the HMC GUI, the HMC CLI (`chhwres`), or the opt-in `hmc_run_command`
  escape hatch if this affects your firmware.
- **Virtual disk creation** (`hmc_create_virtual_disk`): no automatic fallback.
  The disk can be created directly on the VIOS with `mkbdsp` and then mapped
  with `hmc_map_storage_to_lpar`.

## CLI usage

```bash
hmc-mcp console info                 # connectivity check / HMC version
hmc-mcp systems list                 # table of managed systems
hmc-mcp systems show <uuid>
hmc-mcp systems summary <uuid>       # one-call summary: state, MTMS, firmware, LPARs, free resources
hmc-mcp systems health               # exception-only fleet health; add --json for automation
hmc-mcp lpars list                   # all LPARs
hmc-mcp lpars list --system <uuid>   # LPARs of one system
hmc-mcp lpars show mylpar            # by name or UUID (JSON)
hmc-mcp lpars state mylpar           # just "running", "not activated", ...
hmc-mcp lpars summary mylpar         # one-call summary: state, RMC, memory, CPU, OS, adapters
hmc-mcp lpars create web01 --system <uuid> --mem 8192 --vcpus 2
hmc-mcp lpars modify web01 --mem 16384 --procs 2.0   # assign resources
hmc-mcp lpars delete web01           # destroy (must be powered off)
hmc-mcp lpars decommission web01 --system <uuid> --dry-run   # preview blast radius
hmc-mcp lpars power-on mylpar        # submits a PowerOn job (asks first)
hmc-mcp lpars power-off mylpar --immediate
hmc-mcp adapters list mylpar                    # network adapters (default type)
hmc-mcp adapters list mylpar --type VirtualSCSIClientAdapter
hmc-mcp adapters add-network mylpar --vlan 100  # add a NIC on VLAN 100
hmc-mcp adapters add-vscsi mylpar --vios-id 1 --vios-slot 5
hmc-mcp adapters add-vfc mylpar --vios-id 1 --vios-slot 6
hmc-mcp adapters delete mylpar --type ClientNetworkAdapter --uuid <adapter-uuid>
hmc-mcp vios list
hmc-mcp jobs list                    # recent jobs (default 20)
hmc-mcp jobs list -n 5               # last 5 jobs
hmc-mcp jobs show <job-uuid>
hmc-mcp raw get /rest/api/uom/VirtualSwitch   # escape hatch, prints XML
```

Add `--json` to list commands for machine-readable output. Composite workflows
such as `hmc-mcp lpars decommission` and `hmc-mcp lpars provision` return a
stable envelope with `workflow_completed`, ordered `steps`, `warnings`, and the
blast-radius or provisioning summary, which is friendlier for automation than
raw HMC payloads. Every entry is the parsed uom resource:
`{UUID, title, link, ResourceType, Resource}` where `Resource` is the flattened
XML (namespace-stripped, HMC bookkeeping attributes removed).

`hmc-mcp lpars decommission` enforces the ADR 0011 ownership token even for
`--dry-run`; use `--ownership-override` only after explicit operator approval.

## MCP server

```bash
hmc-mcp serve            # stdio — what MCP clients/agents expect
hmc-mcp serve --http --listen-host 127.0.0.1 --port 8000
# Explicitly enable the arbitrary-command MCP escape hatch when required:
hmc-mcp serve --enable-arbitrary-command
```

> **Security:** the streamable-HTTP transport is **unauthenticated**. It
> exposes enabled tools — including user administration — to anyone who can reach the
> port. Keep the default loopback bind. `serve --http` refuses a
> non-loopback `--listen-host` unless `--allow-remote` is passed; if you need remote
> access, put an authenticated reverse proxy (MCP gateway or HTTPS proxy with
> bearer-token auth) in front and never expose the port directly. The arbitrary
> `hmc_run_command` escape hatch is disabled unless the server starts with
> `--enable-arbitrary-command`.

Exposed tools are listed below. MCP clients also receive a rendered description for every tool
parameter, including fields nested inside structured inputs such as LPAR resources, password
policies, and update repositories.

**Read-only / inventory**

| Tool                          | Description |
|-------------------------------|-------------|
| `hmc_console_info`            | HMC version/network info; cheap connectivity check |
| `hmc_list_configured_hosts`   | List configured HMC profiles from the platform-native TOML config; returns name, host, user, port, TLS setting, default flag, and credential-presence booleans. No network calls. |
| `hmc_list_systems`            | All managed systems, optionally filtered by state |
| `hmc_get_system`              | One managed system by exact SystemName or UUID |
| `hmc_list_lpars`              | All LPARs, optionally filtered by system or state |
| `hmc_get_lpar`                | One LPAR by name or UUID |
| `hmc_get_lpar_state`          | Quick state lookup for one LPAR by name or UUID |
| `hmc_lpar_summary`            | One-call summary: state, RMC, memory/CPU, OS, adapter count, description |
| `hmc_system_summary`          | One-call system summary: state, MTMS, firmware, LPAR counts by state, free memory/CPU, VIOS count |
| `hmc_list_vios`               | Virtual I/O Servers, optionally filtered by system or state |
| `hmc_get_vios`                | Storage-detail mappings for one VIOS by name or UUID |
| `hmc_list_resources`          | Any uom resource type (VirtualSwitch, SharedMemoryPool, ...) |
| `hmc_get_job`                 | Job status/result |
| `hmc_list_recent_jobs`        | Recent HMC jobs list (limit=20) |
| `hmc_fleet_health`            | Exception-only estate health: systems, VIOS, LPAR RMC, and recent failed jobs |
| `hmc_capacity_report`         | Per-system: total/assigned/free memory (MiB) and CPU, LPAR counts |
| `hmc_find_placement`          | Systems with enough free memory + CPU to host a new LPAR |
| `hmc_wait_for_job`            | Poll until a terminal HMC state and return a normalized outcome (`status`, `timed_out`, nullable `error`, and last `job`); terminal states include completed, failed, exception, and canceled variants |

`hmc_fleet_health` and `systems health` return only exceptions across the whole
estate: non-operating systems, non-running VIOS partitions, LPARs with inactive
RMC, and recent failed jobs. This is not equivalent to composing N
`hmc_system_summary` calls or using `hmc_capacity_report`, which report per-system
inventory and capacity rather than individual unhealthy resources. On HMCs that
do not support global Job listing, the stable response keeps `failed_jobs` empty
and includes a warning that recent-job health is unavailable; that warning must
not be interpreted as a healthy job feed.

### Public parameter units and selectors

Storage quantities use binary-unit suffixes: `capacity_mib` for virtual disks,
`size_mib` for media repositories and optical media, and `lu_size_gib` for
Shared Storage Pool logical units. Numeric virtual-switch selectors are named
`virtual_switch_id`; the SSH vNIC tool uses `virtual_switch_name` because it
requires a name instead.

The NIM install tools distinguish `hmc_timeout_minutes` from the client-side
`wait_timeout_seconds`. With `wait=True` and no explicit client budget, the
client waits for the HMC timeout converted to seconds plus one polling interval,
so it can observe the terminal state at the HMC deadline. LPM's separate
`wait_time` value is an HMC migration/validation field measured in seconds.

**Mutating / lifecycle**

| Tool                  | Description |
|-----------------------|-------------|
| `hmc_provision_lpar`  | **End-to-end LPAR provisioning workflow**: create + network adapter + vSCSI adapter + storage mapping + power on in one call; validates name/VLAN/VG preconditions; `dry_run=True` checks preconditions only; per-step results with partial-failure reporting. LPAR creation falls back to `mksyscfg` over SSH if REST returns 406 (requires SSH credentials). |
| `hmc_decommission_lpar` | **End-to-end LPAR decommission workflow**: inventory the selected system-scoped target, enforce ADR 0011 ownership, report adapters and observed storage mappings, power off, detach adapters, and delete the LPAR; `dry_run=True` previews only. It does not delete storage mappings, backing storage, or perform rollback. |
| `hmc_create_lpar`     | Create an LPAR on a system (memory, shared/dedicated CPU, type); refuses if a partition with the same name already exists |
| `hmc_modify_lpar`     | Change an LPAR's memory / CPU resources; inspect ADR 0011 description ownership before mutation |
| `hmc_rename_lpar`     | Rename an LPAR; requires system selector and enforces ADR 0011 description ownership (`ownership_override` only with explicit approval) |
| `hmc_dlpar_proc`      | DLPAR processor hot-plug on a running LPAR |
| `hmc_dlpar_mem`       | DLPAR memory hot-plug on a running LPAR |
| `hmc_delete_lpar`     | Destroy an LPAR; requires system selector and enforces ownership |
| `hmc_power_on_lpar`   | Submit PowerOn job; returns stable `already_running`, nullable `job`, and nullable `message` fields (`force=True` overrides the running-state guard) |
| `hmc_power_off_lpar`  | Submit PowerOff job (`immediate` flag); optionally wait for a normalized outcome |
| `hmc_install_lpar_os` | Submit a NIM-based LPAR OS installation job (`hmc_timeout_minutes`); optionally wait for a normalized outcome |

**Virtual adapters (network / storage)**

| Tool                     | Description |
|--------------------------|-------------|
| `hmc_list_adapters`      | List an LPAR's adapters by type (network / vSCSI / vFC / vNIC) |
| `hmc_add_network_adapter`| Add a Virtual Ethernet NIC (VLAN PVID, vswitch, tagged, MAC) |
| `hmc_add_vscsi_adapter`  | Add a Virtual SCSI client adapter paired to a VIOS |
| `hmc_add_vfc_adapter`    | Add a Virtual Fibre Channel (NPIV) adapter paired to a VIOS |
| `hmc_delete_adapter`     | Remove an adapter from an LPAR by UUID |

**Virtual storage (Volume Groups / Virtual Disks / mappings)**

| Tool                      | Description |
|---------------------------|-------------|
| `hmc_list_volume_groups`  | List VIOS Volume Groups (free space, PVs, virtual disks) |
| `hmc_create_volume_group` | Create a Volume Group from physical volumes |
| `hmc_create_virtual_disk` | Carve a `capacity_mib` Virtual Disk (logical volume) out of a VG |
| `hmc_attach_disk_to_lpar` | Create a `capacity_mib` Virtual Disk, add its vSCSI adapter, and map it to an existing LPAR; supports `dry_run=True` and per-step failure reporting |
| `hmc_map_storage_to_lpar` | Map a VirtualDisk/PhysicalVolume to an LPAR (vSCSI mapping) |

**Virtual media (ISO library)**

| Tool                          | Description |
|-------------------------------|-------------|
| `hmc_create_media_repository` | Create a `size_mib` Virtual Media Repository (VMLibrary) on a VG |
| `hmc_create_optical_media`    | Create a blank `size_mib` optical media (ISO container) |
| `hmc_delete_media_repository` | Delete the Virtual Media Repository from a VG |

**Virtual networking (switches / networks / bridges)**

| Tool                           | Description |
|--------------------------------|-------------|
| `hmc_list_virtual_switches`    | List VirtualSwitches (names, SwitchIDs, mode) |
| `hmc_list_virtual_networks`    | List Virtual Networks (VLANs) on a system |
| `hmc_create_virtual_network`   | Create a Virtual Network (VLAN) using `virtual_switch_id` |
| `hmc_delete_virtual_network`   | Delete a Virtual Network |
| `hmc_list_network_bridges`     | List NetworkBridges (Shared Ethernet Adapters) |

> **SSH/CLI tools** — the `(SSH/CLI)` tools run HMC CLI commands over SSH.
> Their system/LPAR arguments (`system_name_or_uuid` / `lpar_name_or_uuid`)
> accept either a CLI name or a UUID. Names are used as-is; UUIDs are resolved
> to their CLI names via the REST API first, falling back to an `lssyscfg` name
> lookup over SSH when the REST API is unreachable. Resolution happens before
> the command runs, so a UUID that cannot be resolved surfaces as an error
> rather than being passed through to the CLI. The opt-in `hmc_run_command`
> tool is the exception — it runs whatever command you give it verbatim.

**VIOS administration**

| Tool                  | Description |
|-----------------------|-------------|
| `hmc_create_vios`     | Create a VIOS partition on a managed system |
| `hmc_delete_vios`     | Delete (destroy) a VIOS partition (must be powered off) |
| `hmc_install_vios`    | Submit a NIM-based VIOS installation job (`hmc_timeout_minutes`); optionally wait for a normalized outcome |
| `hmc_list_vios_backups` | List existing VIOS backups (SSH/CLI) |
| `hmc_backup_vios`     | Create a VIOS backup (SSH/CLI) |
| `hmc_restore_vios`    | Restore a VIOS from a named backup (SSH/CLI) |

**SR-IOV / vNIC & physical I/O (SSH/CLI)**

| Tool                       | Description |
|----------------------------|-------------|
| `hmc_list_vnics`           | List vNICs (SR-IOV-backed Virtual NICs) on an LPAR |
| `hmc_add_vnic`             | Add a vNIC to an LPAR using `virtual_switch_name` |
| `hmc_remove_vnic`          | Remove a vNIC from an LPAR |
| `hmc_list_fc_ports`        | List Virtual Fibre Channel (NPIV) adapters for a system |
| `hmc_list_sea_adapters`    | List Shared Ethernet Adapters for a system |
| `hmc_set_sriov_adapter_mode` | Toggle a physical SR-IOV adapter between SR-IOV and dedicated mode |

**Template library**

| Tool                              | Description |
|-----------------------------------|-------------|
| `hmc_list_partition_templates`    | All partition templates |
| `hmc_get_partition_template`      | One partition template by UUID |
| `hmc_deploy_partition_template`   | Deploy a partition from a draft template — job |

**Live Partition Mobility (LPM)**

| Tool                            | Description |
|---------------------------------|-------------|
| `hmc_migrate_lpar`              | Validate to a successful terminal result, then migrate an LPAR; `validate_first=False` opts into direct submission |
| `hmc_migrate_validate_lpar`     | Pre-check a migration — job |
| `hmc_migrate_abort_lpar`        | Abort an in-progress migration — job |
| `hmc_migrate_recover_lpar`      | Recover after a failed migration — job |
| `hmc_remote_restart_lpar`       | Remote-restart a failed LPAR — job |

**System / VIOS power**

| Tool                    | Description |
|-------------------------|-------------|
| `hmc_modify_system`     | Change a managed system's configuration (only passed fields) |
| `hmc_power_on_system`   | Power on a managed system — job |
| `hmc_power_off_system`  | Power off a managed system — job |
| `hmc_power_on_vios`     | Power on a VIOS — job |
| `hmc_power_off_vios`    | Power off a VIOS — job |

**Cluster / Shared Storage Pool (SSP)**

| Tool                            | Description |
|---------------------------------|-------------|
| `hmc_list_clusters`             | List Clusters (VIOS node sets sharing a pool) |
| `hmc_list_shared_storage_pools` | All SSPs (capacity, free space, logical units) |
| `hmc_get_shared_storage_pool`   | One SSP by UUID |
| `hmc_create_logical_unit`       | Create a Logical Unit (file-backed disk) — job |
| `hmc_delete_logical_unit`       | Delete a Logical Unit by UDID — job |

**Performance & Capacity Monitoring (PCM)**

| Tool                        | Description |
|-----------------------------|-------------|
| `hmc_get_pcm_preferences`   | Read monitoring flags (LTM/aggregation/STM/energy) |
| `hmc_set_pcm_preferences`   | Enable/disable PCM collection for a resource |
| `hmc_processed_metric_links`    | List processed metric documents (30s, ~2h retention) |
| `hmc_processed_metrics`         | Download the newest processed metric document |
| `hmc_aggregated_metric_links`   | List aggregated metric documents (trend rollup) |
| `hmc_aggregated_metrics`        | Download the newest aggregated metric document |

> **PCM notes**: metrics are stored as *JSON*, reached via an Atom feed of
> links. The `*_metric_links` tools return the link list, while the `*_metrics`
> tools download the most recent document (or `{}` when none are in range). The CLI
> `metrics show` accepts `--fetch` to do both in one step. Long-term
> monitoring + aggregation must be enabled via `hmc_set_pcm_preferences`
> before processed/aggregated metrics accumulate. Categories include
> `ManagementConsole`, `ManagedSystem`, `LogicalPartition`,
> `VirtualIOServer`, `SharedStoragePool`, `Cluster`.

**Users & access (HMC user administration)**

| Tool                          | Description |
|-------------------------------|-------------|
| `hmc_list_users`              | List HMC user accounts |
| `hmc_get_user`                | Get one HMC user account by username |
| `hmc_create_user`             | Create a new HMC local user account |
| `hmc_modify_user`             | Modify an HMC user account (only supplied fields) |
| `hmc_delete_user`             | Delete an HMC user account (irreversible) |
| `hmc_list_password_policies`  | List HMC password policies |
| `hmc_list_password_policy_status` | Get password-policy activation status |
| `hmc_create_password_policy`  | Create a password policy (max age, rules) |
| `hmc_modify_password_policy`  | Modify a password policy (only supplied fields) |
| `hmc_delete_password_policy`  | Delete a password policy (irreversible) |
| `hmc_get_ldap_config`          | Get the current HMC LDAP server configuration |
| `hmc_configure_ldap`          | Configure the HMC LDAP server integration |
| `hmc_remove_ldap_config`      | Remove a component of the LDAP configuration |

**Software updates (HMC / VIOS / firmware)**

| Tool                         | Description |
|------------------------------|-------------|
| `hmc_update_console_software` | Submit an HMC software update (kind=update, PTF install) or upgrade (kind=upgrade, full version) job |
| `hmc_get_available_hmc_ptfs` | Get available PTFs for the HMC software |
| `hmc_vios_update`            | Submit a VIOS software update (kind=update) or upgrade (kind=upgrade) job |
| `hmc_update_firmware`        | Submit a managed-system firmware update job |

**LPAR profiles (backup / restore)**

| Tool                         | Description |
|------------------------------|-------------|
| `hmc_backup_lpar_profiles`   | Backup all LPAR profiles on a system (`bkprofdata`) |
| `hmc_restore_lpar_profiles`  | Restore LPAR profiles from a backup file (`rstprofdata`) |
| `hmc_sync_lpar_profile`      | Sync an LPAR's running config back to its current profile |
| `hmc_assign_profile_io_slot` | Add a physical I/O slot DRC index to an LPAR's profile |

**LPAR / system properties (SSH/CLI)**

| Tool                        | Description |
|-----------------------------|-------------|
| `hmc_get_lpar_description`  | Get an LPAR's description field |
| `hmc_set_lpar_description`  | Set an LPAR's description field |
| `hmc_get_lpar_msp`          | Get the Migratable Service Partition flag |
| `hmc_set_lpar_msp`          | Set the MSP flag |
| `hmc_get_proc_compat_modes` | List processor compatibility modes a system supports |
| `hmc_get_lpar_proc_compat`  | Get an LPAR's current/pending proc-compat mode |
| `hmc_set_lpar_proc_compat`  | Set an LPAR's processor compatibility mode |
| `hmc_list_io_slots`         | List physical I/O slots on a system |
| `hmc_list_memory_pools`     | List shared memory pools on a system |
| `hmc_remove_memory_pool`    | Remove a shared memory pool from a system |

**Escape hatch**

| Tool                | Description |
|---------------------|-------------|
| `hmc_run_command`   | Run any HMC CLI command; available only with `serve --enable-arbitrary-command` |

### End-to-end: give an LPAR a bootable disk

```bash
# 1. create the partition
hmc-mcp lpars create web01 --system <sys-uuid> --mem 8192 --vcpus 2

# 2. give it a vSCSI adapter paired to the VIOS (find IDs via `vios list`)
hmc-mcp adapters add-vscsi web01 --vios-id 1 --vios-slot 5

# 3. carve a virtual disk out of a VIOS volume group
hmc-mcp storage list-vgs <vios-uuid>                       # find the VG + free space
hmc-mcp storage create-disk <vios-uuid> --vg <vg-uuid> --name web01_root --capacity-mib 51200

# 4. map the disk to the partition
hmc-mcp storage map <vios-uuid> --lpar web01 --disk web01_root

# 5. (optionally) network + power on
hmc-mcp adapters add-network web01 --vlan 100
hmc-mcp lpars power-on web01
```

> **Note on the storage model**: an LPAR's vSCSI/vFC *adapter* (added with
> `adapters add-vscsi` / `add-vfc`) is just plumbing — it pairs the partition
> with a VIOS server slot. The actual *disk* lives on the VIOS or in a Shared
> Storage Pool: carve it out of a Volume Group (`storage create-disk`) or a
> Cluster/SSP (`cluster create-lu`), then connect it with a mapping
> (`storage map`). Both the VIOS **Volume Group / Virtual Disk** model and the
> **Cluster / SSP Logical Unit** model are wrapped. Once a disk is mapped,
> partitioning it into filesystems is the guest OS's job (NIM, cloud-init,
> `mkfs`), not the HMC's.

### Use with Hermes Agent

```bash
hermes mcp add hmc -- uv run --directory ~/src/hmc-mcp hmc-mcp serve
```

## Testing

### 1. Unit tests (no HMC needed)

The client and XML parser are tested against an HMC mocked with
[respx](https://lundberg.github.io/respx/) — no real hardware required:

```bash
uv run pytest -q
# ...............                    [100%]
# 15 passed in 0.10s
```

### 2. MCP protocol smoke test (no HMC needed)

Verifies the server actually speaks MCP over stdio and lists every tool. It
connects with a real FastMCP client and prints the tools:

```bash
uv run python scripts/smoke_mcp.py
# Connected. <N> tools exposed:
#   - hmc_console_info
#   - hmc_list_systems
#   ...
```

### 3. Live check against a real HMC

With credentials configured (TOML profile, env vars, or flags), the cheapest end-to-end check
is `console info` — one Logon + one ManagementConsole GET:

```bash
hmc-mcp console info
hmc-mcp systems list      # should print a table of your Power servers
```

If `console info` prints the HMC version, auth, TLS and the session
lifecycle all work; everything else uses the same path.

## Layout

```
src/hmc_mcp/
  config.py      # pydantic-settings config (TOML profile + env vars + CLI flags)
  xmlutil.py     # defusedxml Atom-feed -> dict parsing
  errors.py      # HMCError (shared by client and its mixins)
  client.py      # async HMCClient: session, transport, uom helpers, jobs
  client_*.py    # per-domain mixins (users, systems, lpars, storage, pcm, ...)
  client_parse.py# defusedxml wrappers tagging failures with the HMC call
  common.py      # shared HMCClient/config helpers for tool definitions
  operations_*.py# workflows and policies shared by MCP and CLI presentations
  ssh.py         # transport-only asyncssh session and command execution
  ssh_commands.py# resource operations implemented with the HMC CLI
  documents.py   # XML request-document builders (LPAR, adapters, storage, users, ...)
  jobs.py        # JobRequest XML templates (PowerOn/PowerOff/...)
  pcm.py         # PCM metrics/preferences parsing + XML documents
  _app.py        # shared FastMCP instance, READ_ONLY/DESTRUCTIVE_TOOLS sets, entry points
  server.py      # thin aggregator importing every server_*.py tool module
  server_*.py    # resource-domain @mcp.tool definitions (systems, lpars, VIOS, ...)
  server_lpar_config.py      # SSH-only LPAR configuration handlers
  server_system_resources.py # SSH-only managed-system resource handlers
  cli.py         # thin aggregator importing every cli_*.py command module
  cli_app.py     # root Typer app, GlobalOpts/GLOBALS, shared CLI helpers
  cli_*.py       # per-domain CLI commands (systems, lpars, storage, ...)
tests/           # pytest + respx, no real HMC needed
scripts/         # smoke/manual harnesses
```

## Notes on the HMC API

- Auth: `PUT /rest/api/web/Logon` with a LogonRequest XML body returns an
  `X-API-Session` token, sent as a header on every subsequent call;
  `DELETE /rest/api/web/Logon` logs off.
- Resources are Atom feeds of vendor media type
  `application/vnd.ibm.powervm.uom+xml; type=<ResourceType>`.
- `.../quick/<Property>` returns a single property cheaply;
  `.../search/(<Property>==<Value>)` filters server-side.
- State changes are asynchronous **jobs**: POST a JobRequest to
  `/rest/api/uom/<Type>/<uuid>/do/<Operation>`, then poll
  `/rest/api/uom/Job/<job-uuid>`.
