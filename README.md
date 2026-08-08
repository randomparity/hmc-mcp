# hmc-mcp

MCP server and CLI for the **IBM Hardware Management Console (HMC) REST API**
(`/rest/api/uom/...`). It lets you — or an AI agent over MCP — inventory
Power systems, inspect LPARs/VIOS, and submit jobs such as power on/off.

## Stack

- **Python ≥3.12**, managed with [uv](https://docs.astral.sh/uv/)
- **MCP server**: [FastMCP](https://gofastmcp.com/) (stdio or streamable HTTP)
- **CLI**: [Typer](https://typer.tiangolo.com/) + Rich tables
- **Transport**: httpx (async), XML parsed with defusedxml

## Install

```bash
cd ~/src/hmc-mcp
uv sync
```

## Configure

Credentials come from a `.env` file, environment variables, or CLI flags
(priority: flags > env > .env). Copy the example:

```bash
cp .env.example .env   # then edit
```

| Setting           | Env var         | CLI flag          | Default   |
|-------------------|-----------------|-------------------|-----------|
| HMC host / IP     | `HMC_HOST`      | `--host`          | —         |
| REST port         | `HMC_PORT`      | —                 | `12443`   |
| User              | `HMC_USER`      | `--user, -u`      | —         |
| Password          | `HMC_PASSWORD`  | `--password, -p`  | —         |
| Verify TLS        | `HMC_VERIFY_SSL`| `--verify-ssl`    | `false`   |

HMCs ship self-signed certificates, so TLS verification is off by default.

## CLI usage

```bash
hmc-mcp console info                 # connectivity check / HMC version
hmc-mcp systems list                 # table of managed systems
hmc-mcp systems show <uuid>
hmc-mcp lpars list                   # all LPARs
hmc-mcp lpars list --system <uuid>   # LPARs of one system
hmc-mcp lpars show mylpar            # by name or UUID (JSON)
hmc-mcp lpars state mylpar           # just "running", "not activated", ...
hmc-mcp lpars create web01 --system <uuid> --mem 8192 --vcpus 2
hmc-mcp lpars modify web01 --mem 16384 --procs 2.0   # assign resources
hmc-mcp lpars delete web01           # destroy (must be powered off)
hmc-mcp lpars power-on mylpar        # submits a PowerOn job (asks first)
hmc-mcp lpars power-off mylpar --immediate
hmc-mcp adapters list mylpar                    # network adapters (default type)
hmc-mcp adapters list mylpar --type VirtualSCSIClientAdapter
hmc-mcp adapters add-network mylpar --vlan 100  # add a NIC on VLAN 100
hmc-mcp adapters add-vscsi mylpar --vios-id 1 --vios-slot 5
hmc-mcp adapters add-vfc mylpar --vios-id 1 --vios-slot 6
hmc-mcp adapters delete mylpar --type ClientNetworkAdapter --uuid <adapter-uuid>
hmc-mcp vios list
hmc-mcp jobs show <job-uuid>
hmc-mcp raw get /rest/api/uom/VirtualSwitch   # escape hatch, prints XML
```

Add `--json` to list commands for machine-readable output. Every entry is the
parsed uom resource: `{UUID, title, link, ResourceType, Resource}` where
`Resource` is the flattened XML (namespace-stripped, HMC bookkeeping
attributes removed).

## MCP server

```bash
hmc-mcp serve            # stdio — what MCP clients/agents expect
hmc-mcp serve --http --host 127.0.0.1 --port 8000
```

Exposed tools:

**Read-only / inventory**

| Tool                  | Description |
|-----------------------|-------------|
| `hmc_console_info`    | HMC version/network info; cheap connectivity check |
| `hmc_list_systems`    | All managed systems |
| `hmc_get_system`      | One managed system by UUID |
| `hmc_list_lpars`      | All LPARs, or those of one system |
| `hmc_get_lpar`        | One LPAR by UUID |
| `hmc_find_lpar`       | Find an LPAR by exact name |
| `hmc_lpar_state`      | Quick PartitionState property |
| `hmc_list_vios`       | Virtual I/O Servers |
| `hmc_list_resources`  | Any uom resource type (VirtualSwitch, SharedMemoryPool, ...) |
| `hmc_get_job`         | Job status/result |

**Mutating / lifecycle**

| Tool                  | Description |
|-----------------------|-------------|
| `hmc_create_lpar`     | Create an LPAR on a system (memory, shared/dedicated CPU, type) |
| `hmc_modify_lpar`     | Change an LPAR's name / memory / CPU (DLPAR when running) |
| `hmc_delete_lpar`     | Destroy an LPAR (must be powered off; irreversible) |
| `hmc_power_on_lpar`   | Submit PowerOn job |
| `hmc_power_off_lpar`  | Submit PowerOff job (`immediate` flag) |
| `hmc_install_lpar_os` | Submit a NIM-based LPAR OS installation job (`lparnetboot`) — job |

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
| `hmc_create_virtual_disk` | Carve a Virtual Disk (logical volume) out of a VG |
| `hmc_map_storage_to_lpar` | Map a VirtualDisk/PhysicalVolume to an LPAR (vSCSI mapping) |

**Virtual media (ISO library)**

| Tool                          | Description |
|-------------------------------|-------------|
| `hmc_create_media_repository` | Create the Virtual Media Repository (VMLibrary) on a VG |
| `hmc_create_optical_media`    | Create a blank optical media (ISO container) |
| `hmc_delete_media_repository` | Delete the Virtual Media Repository from a VG |

**Virtual networking (switches / networks / bridges)**

| Tool                           | Description |
|--------------------------------|-------------|
| `hmc_list_virtual_switches`    | List VirtualSwitches (names, SwitchIDs, mode) |
| `hmc_list_virtual_networks`    | List Virtual Networks (VLANs) on a system |
| `hmc_create_virtual_network`   | Create a Virtual Network (VLAN) |
| `hmc_delete_virtual_network`   | Delete a Virtual Network |
| `hmc_list_network_bridges`     | List NetworkBridges (Shared Ethernet Adapters) |

**Template library**

| Tool                              | Description |
|-----------------------------------|-------------|
| `hmc_list_partition_templates`    | List partition templates |
| `hmc_get_partition_template`      | One template by UUID |
| `hmc_deploy_partition_template`   | Deploy a partition from a draft template — job |

**Live Partition Mobility (LPM)**

| Tool                            | Description |
|---------------------------------|-------------|
| `hmc_migrate_lpar`              | Migrate an LPAR to another system — job |
| `hmc_migrate_validate_lpar`     | Pre-check a migration — job |
| `hmc_migrate_abort_lpar`        | Abort an in-progress migration — job |
| `hmc_migrate_recover_lpar`      | Recover after a failed migration — job |
| `hmc_remote_restart_lpar`       | Remote-restart a failed LPAR — job |

**System / VIOS power**

| Tool                    | Description |
|-------------------------|-------------|
| `hmc_power_on_system`   | Power on a managed system — job |
| `hmc_power_off_system`  | Power off a managed system — job |
| `hmc_power_on_vios`     | Power on a VIOS — job |
| `hmc_power_off_vios`    | Power off a VIOS — job |

**Cluster / Shared Storage Pool (SSP)**

| Tool                            | Description |
|---------------------------------|-------------|
| `hmc_list_clusters`             | List Clusters (VIOS node sets sharing a pool) |
| `hmc_list_shared_storage_pools` | List SSPs (capacity, free space, logical units) |
| `hmc_get_shared_storage_pool`   | One SSP by UUID (PVs, logical units) |
| `hmc_create_logical_unit`       | Create a Logical Unit (file-backed disk) — job |
| `hmc_delete_logical_unit`       | Delete a Logical Unit by UDID — job |

**Performance & Capacity Monitoring (PCM)**

| Tool                        | Description |
|-----------------------------|-------------|
| `hmc_get_pcm_preferences`   | Read monitoring flags (LTM/aggregation/STM/energy) |
| `hmc_set_pcm_preferences`   | Enable/disable PCM collection for a resource |
| `hmc_get_processed_metric_links` | List processed metrics links (30s, ~2h retention) |
| `hmc_get_processed_metrics`     | Download most recent processed metrics doc |
| `hmc_get_aggregated_metric_links`| List aggregated metrics links (trend rollup) |
| `hmc_get_aggregated_metrics`    | Download most recent aggregated metrics doc |

> **PCM notes**: metrics are stored as *JSON*, reached via an Atom feed of
> links. The `_links` tools return the list of links; the fetch tools
> download the most recent document (or `{}` when none are in range). The CLI
> `metrics show` accepts `--fetch` to do both in one step. Long-term
> monitoring + aggregation must be enabled via `hmc_set_pcm_preferences`
> before processed/aggregated metrics accumulate. Categories include
> `ManagementConsole`, `ManagedSystem`, `LogicalPartition`,
> `VirtualIOServer`, `SharedStoragePool`, `Cluster`.

### End-to-end: give an LPAR a bootable disk

```bash
# 1. create the partition
hmc-mcp lpars create web01 --system <sys-uuid> --mem 8192 --vcpus 2

# 2. give it a vSCSI adapter paired to the VIOS (find IDs via `vios list`)
hmc-mcp adapters add-vscsi web01 --vios-id 1 --vios-slot 5

# 3. carve a virtual disk out of a VIOS volume group
hmc-mcp storage list-vgs <vios-uuid>                       # find the VG + free space
hmc-mcp storage create-disk <vios-uuid> --vg <vg-uuid> --name web01_root --size 51200

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

(or point your MCP client's env at `HMC_HOST`/`HMC_USER`/`HMC_PASSWORD`).

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
# Connected. 12 tools exposed:
#   - hmc_console_info
#   - hmc_list_systems
#   ...
```

### 3. Live check against a real HMC

With credentials configured (`.env` or flags), the cheapest end-to-end check
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
  config.py    # pydantic-settings config (env/.env/flags)
  xmlutil.py   # defusedxml Atom-feed -> dict parsing
  client.py    # async HMCClient: logon/logoff, uom resources, jobs
  templates.py # LogicalPartition create/modify XML documents
  jobs.py      # JobRequest XML templates (PowerOn/PowerOff/...)
  server.py    # FastMCP server + tool definitions
  cli.py       # Typer CLI
tests/         # pytest + respx, no real HMC needed
scripts/       # smoke/manual harnesses
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
