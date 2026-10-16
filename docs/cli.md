# CLI guide

[Documentation index](index.md) · [CLI quick start](../README.md#cli-quick-start)

Configure a connection first using the [configuration guide](configuration.md).
Use `hmcpctl --help` and `hmcpctl <group> --help` to discover commands and options.
The [HMC CLI cheatsheet](hmc-cli-cheatsheet.md) covers the underlying IBM commands
used over SSH; this guide covers the `hmcpctl` application.
The [bare-CEC LPAR recipe](recipes/bare-cec-lpar.md) walks a dedicated-I/O partition from
create to delete.

## CLI usage

```bash
hmcpctl console info                 # connectivity check / HMC version
hmcpctl systems list                 # table of managed systems
hmcpctl systems show <uuid>
hmcpctl systems summary <uuid>       # one-call summary: state, MTMS, firmware, LPARs, free resources
hmcpctl systems health               # issue-only fleet health; add --json for automation
hmcpctl report utilization --csv fleet.csv   # CPU/memory/disk/adapter allocation, every profile
hmcpctl report utilization --html fleet.html # the same survey as one offline HTML page
hmcpctl lpars list                   # all LPARs
hmcpctl lpars list --system <uuid>   # LPARs of one system
hmcpctl lpars show mylpar --system <uuid> --json  # scoped name or UUID
hmcpctl lpars state mylpar --system <uuid> # just "running", "not activated", ...
hmcpctl lpars summary mylpar         # one-call summary: state, RMC, memory, CPU, OS, adapters
hmcpctl lpars get-minimum-affinity-policy mylpar sys1 --json
hmcpctl lpars system-memopt-score sys1
hmcpctl lpars plan-memopt-scores sys1 --prioritize-name web --exclude-name batch
hmcpctl lpars plan-system-memopt-score sys1 --prioritize-id 3 --exclude-id 9 --json
hmcpctl lpars create web01 --system <uuid> --mem 8192 --vcpus 2 --procs 0.2
hmcpctl lpars modify web01 --mem 16384 --procs 2.0   # assign resources
hmcpctl lpars delete web01 --system <uuid> # destroy (must be powered off)
hmcpctl lpars decommission web01 --system <uuid> --dry-run   # preview blast radius
hmcpctl lpars power-on mylpar        # submits a PowerOn job (asks first)
hmcpctl lpars power-off mylpar --immediate
hmcpctl adapters list mylpar                    # network adapters (default type)
hmcpctl adapters list mylpar --type VirtualSCSIClientAdapter
hmcpctl adapters add-network mylpar --vlan 100  # add a NIC on VLAN 100
hmcpctl adapters add-vscsi mylpar --vios-id 1 --vios-slot 5   # skip before storage map: it creates the pair
hmcpctl adapters add-vfc mylpar --vios-id 1 --vios-slot 6
hmcpctl adapters delete mylpar --type ClientNetworkAdapter --uuid <adapter-uuid>
hmcpctl vios list
hmcpctl jobs show <job-id>
hmcpctl raw get /rest/api/uom/VirtualSwitch   # escape hatch, prints XML
```

Add `--json` to inventory commands for their full JSON result. The inventory
commands below default to lossless TSV when redirected and tables on a terminal. Composite workflows
such as `hmcpctl lpars decommission` and `hmcpctl lpars provision` return a
stable envelope with `workflow_completed`, ordered `steps`, `warnings`, and the
blast-radius or provisioning summary, which is friendlier for automation than
raw HMC payloads. Every entry is the parsed uom resource:
`{UUID, title, link, ResourceType, Resource}` where `Resource` is the flattened
XML (namespace-stripped, HMC bookkeeping attributes removed).

The memory-affinity planning commands are read-only `lsmemopt` calculations.
Repeat `--prioritize-name`, `--prioritize-id`, `--exclude-name`, or `--exclude-id`
to describe a scenario, using names or IDs consistently. Calculated scores are
predictions, not guarantees of placement, and these commands never start optimization.

The minimum-affinity policy command is also read-only. It first checks whether the managed
system advertises `POWER11` processor compatibility, then explicitly requests
`min_affinity_score` and `min_affinity_score_action` through `lssyscfg`. The score is validated
as an integer from 0 through 100 and the action as `none`, `warn`, or `fail`. Systems without
that capability remain usable and return an actionable `capability-unavailable` reason. This
surface does not provide a setter; portable snapshots record the policy only when supported.

Portable snapshots capture one named LPAR profile together with source identity and separate
timestamped placement and affinity observations:

```bash
hmcpctl snapshot capture sys1 aix1 default --output aix1.snapshot.json
hmcpctl snapshot validate aix1.snapshot.json
hmcpctl snapshot inspect aix1.snapshot.json
```

Capture refuses to overwrite an existing local file. Validation and inspection are local and
perform no HMC I/O. Snapshots do not expose a replay command; observation data is diagnostic and
never part of the replayable profile configuration.

A REST create gives the partition a current configuration, so no apply is needed and the
`apply_profile` step reports `skipped`. When the HMC creates the partition through `mksyscfg` (its
REST create was refused with HTTP 406 or a 400 `REST0001` payload rejection, or no memory or
processor value was given), `lpars create` then applies the new `default_profile` with `chsyscfg -o apply`, without powering
the partition on, and reports an `apply_profile` step. Until a profile is applied or the partition
is activated, it has no current configuration and REST adapter writes fail. `--no-apply` skips
the apply. Adapter changes made through REST after the apply live only in the current
configuration unless the partition's `CurrentProfileSync` is `On`. The adapter and mapping
commands print which after their result. `lpars power-on --partition-profile` warns once for
each current virtual SCSI, Fibre Channel or Ethernet client adapter the profile lacks, because
activating that profile removes it; the job is still submitted (#981).

A bounded console capture reads an LPAR's virtual console without sending it input:

```bash
hmcpctl lpars capture-console aix1 --system sys1 --duration 30 --output aix1.console.log
```

It stops at `--duration` seconds (at most 3600), `--max-bytes` (at most 1048576), or
`--idle-timeout` seconds of silence, writes the raw bytes to `--output` or to a redirected stdout,
and refuses an existing file or a terminal stdout. One stderr line reports the stop reason, byte
count and `released`. Exit codes ([ADR 0175](adr/0175-capture-console-exit-codes.md)): `0` the
capture finished and the console was released; `1` a lookup, SSH or HMC failure, a console held
by another session, a capture stopped by an error, or bytes that could not be written; `2` a
usage error; `3` the release was not proven, so run `rmvterm` on the HMC before another capture,
unless the line says another client ended the hold (`ConsoleHoldLostError`): leave that session.

`hmcpctl lpars decommission` enforces the ADR 0011 ownership token even for
`--dry-run`; use `--ownership-override` only after explicit operator approval.

### Running as a Python module

`python -m hmcpctl` runs the same program as the `hmcpctl` command, wherever the `app` extra is
installed, and takes the same arguments. Its usage lines name `python -m hmcpctl` instead of
`hmcpctl`. One difference matters for security: `-m` puts the current working directory first
on `sys.path`, so a `hmcpctl/` package or a module such as `typer.py` in the launch directory is
imported before the installed package, in a process that holds profile passwords and, for
`serve`, a granted access policy. Nothing inside the package can guard against this. Prefer
`hmcpctl` for a served process, and when you launch the module from a directory you do not
control, use `python -P -m hmcpctl` or set `PYTHONSAFEPATH=1`
([ADR 0128](adr/0128-l5-module-entry-point-launch.md)).

### End-to-end: give an LPAR a bootable disk

For the complete ISO provisioning and optical-boot lifecycle, see the [LPAR ISO installation recipe](recipes/lpar-iso-install.md).

```bash
# 1. create the partition
hmcpctl lpars create web01 --system <sys-uuid> --mem 8192 --vcpus 2 --procs 0.2

# 2. carve a virtual disk out of a VIOS volume group
hmcpctl storage list-vgs <vios-uuid>                       # find the VG + free space
hmcpctl storage create-disk <vios-uuid> --vg <vg-uuid> --name web01_root --capacity-mib 51200

# 3. map the disk to the partition (the HMC creates the vSCSI adapter pair)
hmcpctl storage map <vios-uuid> --lpar web01 --name web01_root

# 4. (optionally) network + power on
hmcpctl adapters add-network web01 --vlan 100
hmcpctl lpars power-on web01
```

> **Note on the storage model**: an LPAR's vSCSI/vFC *adapter* is just
> plumbing — it pairs the partition with a VIOS server slot. `storage map`
> creates its own vSCSI client/server adapter pair, so do not run
> `adapters add-vscsi` first; an adapter added that way is left unpaired.
> `lpars provision` and `storage attach-disk` rely on that pair too. The
> actual *disk* lives on the VIOS or in a Shared
> Storage Pool: carve it out of a Volume Group (`storage create-disk`) or a
> Cluster/SSP (`cluster create-lu`), then connect it with a mapping
> (`storage map`). Both the VIOS **Volume Group / Virtual Disk** model and the
> **Cluster / SSP Logical Unit** model are wrapped. Once a disk is mapped,
> partitioning it into filesystems is the guest OS's job (NIM, cloud-init,
> `mkfs`), not the HMC's.

## Inventory output and migration

These commands accept `--format table|tsv|json` and the JSON alias `--json`/`-j`:
`systems list`, `show`, `summary`, `health`, `capacity`, `find-placement`;
`lpars list`, `show`, `summary`, `refcodes`; `vios list`; and
`storage list-vgs`, `get-media-repo`, `list-optical-media`, `list-mappings`.
Selection defaults to `table` when stdout is a terminal, `tsv` otherwise.
An explicit format wins. `--json --format json` agrees; combining `--json` or
`-j` with `table` or `tsv` fails with exit 2 before connecting. As with other
single-value CLI options, repeating `--format` uses its last value.

```bash
hmcpctl lpars list > lpars.tsv                 # full, unwrapped identifiers
hmcpctl lpars list --format table             # explicitly formatted for people
hmcpctl lpars show mylpar --json > lpar.json   # full original document
hmcpctl storage list-mappings vios1 -j > mappings.json
```

`lpars show --no-json` has been removed: it previously had no effect. Replace it
with `--format table` or `--format tsv`. Scripts that relied on implicit JSON
from `lpars show` or `systems show` must add `--json`. Scripts scraping Rich
borders should consume TSV or explicitly request `--format table`; tables can
still fold identifiers at narrow widths. The JSON mode keeps existing documents,
typed projections, and `entries`/`unreadable_systems` envelopes; it does not add a
second compact JSON schema. Existing JSON capture recipes already select `--json`.

TSV uses the following headers in this order, with literal tabs separating columns:

| Command | Columns |
| --- | --- |
| `systems list` | Name, UUID, State, MTMS, IP Address |
| `systems capacity` | System, UUID, Total Mem (MiB), Assigned Mem (MiB), Free Mem (MiB), Total Procs, Assigned Procs, Free Procs, Running LPARs, Total LPARs |
| `systems find-placement` | System, UUID, Free Mem (MiB), Free Procs, Running LPARs |
| `systems health` | Category, Name, UUID, State, RMC, System, System UUID |
| `lpars list` | System, System UUID, Name, ID, UUID, State, Type, OS, RMC |
| `vios list` | System, System UUID, Name, ID, UUID, State, Version |
| `lpars refcodes` | Partition, Timestamp, Code, Definition |
| `storage list-vgs` | Name, UUID, Free (GiB), Capacity (GiB), Diagnostic |
| `storage list-optical-media` | Media Name, Size (MiB) |
| `storage list-mappings` | Mapping ID, Client LPAR, Backing Storage, Type |
| `systems show`, `systems summary`, `lpars show`, `lpars summary`, `storage get-media-repo` | Field, Value |

Single-object commands emit one row for each top-level field, in document order.
Nested values use compact JSON within the cell; decode TSV escaping before parsing
that JSON. `Resource.RepositorySize` in `get-media-repo` is measured in GiB;
summary field names identify memory units as MiB. Tables show the same projection.

TSV always emits its header, including for zero results, and terminates each record
with LF. It emits no titles or presentation styling, and does not wrap with terminal
width. Empty strings are empty cells; absent or null values are the exact token
`\N`. Literal backslashes are doubled, then tab, LF and CR become `\t`, `\n`
and `\r`. Thus a literal `\N` value is encoded as `\\N`, distinct from null.
Decode `\N` as null first, then scan other cells once left-to-right, replacing
`\\`, `\t`, `\n`, `\r` with their single-character values. Do not repeatedly
apply replacements: a literal backslash followed by `n` is not a newline.
Zero and false are values, not missing; Unicode and brackets are retained.

Empty collections exit 0, with an informative notice on stderr for table/TSV;
JSON retains its original empty collection or envelope. Missing named systems or
LPARs still exit 1. Partial fleet reads also exit 0 and warn on stderr. Retain
stderr with TSV to distinguish empty successful inventory from skipped systems;
JSON additionally retains `unreadable_systems`. Health and summary warnings go to
stderr even when selecting JSON.

Unscoped LPAR/VIOS results add `ManagedSystem: {"UUID": ..., "SystemName": ...}`
to each entry. This metadata comes from the parent inventory already fetched,
without an extra request per row, and does not alter `Resource`. State filtering
retains it. Scoped listings keep their prior JSON entries; their parent columns
are missing when the entry lacks this metadata. `systems health` uses categories
`systems`, `vios`, `lpars`; system rows have missing parent/RMC cells.

`lpars state` remains one scalar state line. Refcode JSON retains raw code and
definition data; its table path keeps terminal sanitation. Console capture and
raw REST streams keep their existing byte-stream contracts. These inventory
format options do not change storage mutation or decommission output.

## Storage selector migration

VIOS-centric commands take one positional VIOS name or UUID. Use `--system` to
constrain that VIOS to a managed system, `--vg NAME_OR_UUID` for its volume group,
and `--name` for the disk or optical image. VG names match exactly within that
VIOS; missing, ambiguous or incomplete identities fail with candidate details.
UUIDs also require membership in the selected VIOS before a write.

These pre-release forms replace the earlier syntax (prefix each with `hmcpctl storage`):

| Earlier | Current |
| --- | --- |
| `get-media-repo VIOS VG` | `get-media-repo VIOS --vg VG` |
| `list-optical-media VIOS VG` | `list-optical-media VIOS --vg VG` |
| `create-media-repo VIOS VG --size-mib 20480` | `create-media-repo VIOS --vg VG --size-mib 20480` |
| `delete-media-repo VIOS VG` | `delete-media-repo VIOS --vg VG` |
| `create-media VIOS VG --name install.iso --size-mib 1024` | `create-media VIOS --vg VG --name install.iso --size-mib 1024` |
| `delete-media VIOS VG install.iso` | `delete-media VIOS --vg VG --name install.iso` |
| `upload-iso VIOS VG install.iso URL` | `upload-iso VIOS --vg VG --name install.iso URL` |
| `mount-optical-media VIOS LPAR install.iso` | `mount-optical-media VIOS --lpar LPAR --name install.iso` |
| `unmount-optical-media VIOS LPAR install.iso` | `unmount-optical-media VIOS --lpar LPAR --name install.iso` |
| `map VIOS --lpar LPAR --disk disk01` | `map VIOS --lpar LPAR --name disk01` |

`create-disk` and `delete-disk` already use `--vg` and `--name`; their `--vg`
now accepts an exact VG name as well as a UUID. Confirmation options are unchanged.
For example, `hmcpctl storage delete-disk vios-a --vg datavg --name disk01 --yes`
still refuses to delete a mapped disk. Mounted-media and If-Match guards also remain.

`attach-disk` is the target-oriented exception: its positional parent is the LPAR,
with both `--vios NAME_OR_UUID` and `--vg NAME_OR_UUID`, for example:

```bash
hmcpctl storage attach-disk web01 --vios vios-a --vg datavg --name disk01 --capacity-mib 1024 --dry-run
```

The media repository belongs to its VIOS/VG, so it has no LPAR filter. Inspect LPAR
relationships with `hmcpctl storage list-mappings vios-a --lpar web01`.

## Fleet utilization report

`hmcpctl report utilization --csv PATH` reads every profile in `config.toml`, or only those
named with repeated `--profile NAME`, and writes one CSV of CPU, memory, VIOS disk and adapter
allocation. It only reads; it changes nothing on any HMC. The accounting follows
[ADR 0184](adr/0184-fleet-utilization-accounting-model.md) and, for disk and adapters,
[ADR 0185](adr/0185-fleet-utilization-disk-and-adapter-accounting.md).

> **Both reports hold internal hostnames, system names and serial numbers. Never commit them or
> post them in a public place.** They are written with owner-only permissions.

`--html PATH` writes the same survey as one self-contained HTML page, instead of or beside the
CSV: give `--csv PATH`, `--html PATH`, or both, and both are written from one survey. The page
is stamped with the UTC time the survey finished and names every profile surveyed. It shows
fleet tiles for CPU, memory, disk, I/O slots, SR-IOV logical ports and idle (`not activated`)
partitions, the fleet notes, a per-HMC table, a per-system table sorted by clicking a column
heading, and every failed profile with its reason. Memory and disk show in GiB; the CSV keeps
exact MiB. A figure the survey could not read shows `unknown`, never 0. The page loads nothing
from the network: its style and sort script are inline and its content security policy blocks
any other script and every fetch, so it reads offline and prints.

The first column, `row_type`, says what each row is:

- `system`: one managed system. A system two HMCs manage appears once, with both profiles in
  `profiles`, and its most complete reading.
- `hmc`: the totals for one profile's HMC.
- `fleet`: the totals over the `system` rows.
- `failure`: a profile that could not be surveyed, with the reason in `notes`.

Each CPU (processor units) and memory (MiB) figure means:

- `installed`, `configurable`: the system's own totals.
- `vios`: the current configuration of the system's VIOS partitions.
- `client_active`: the current configuration of client partitions in any state except
  `not activated`.
- `idle_reserved`: the current configuration of `not activated` partitions. The hypervisor
  keeps it reserved, so it is allocated capacity that nothing is running on.
- `hypervisor` (memory only): memory the hypervisor itself uses.
- `other_reserved`: what remains of configurable capacity after free, the hypervisor and the
  partitions.
- `free`: what the system reports available.
- `allocated` and `util_pct`: configurable minus free, and that as a share of configurable.
- `dedicated`, `shared`, `shared_pools`: units held by dedicated and shared-processor
  partitions, and the shared pools in use.
- `profile_claims`, `profile_claim_mem_mib`, `profile_claim_cpu`: partitions that have never had
  a profile applied, and what their profiles would claim. The hypervisor reserves nothing for
  them, so these are not counted as allocated.

Disk and adapter columns follow `notes`:

- `disk_internal_*_mib` and `disk_san_*_mib`: the capacity of the physical volumes the system's
  VIOS report, as `total`, `assigned` and `free`. SAN means Fibre Channel- or iSCSI-backed. A
  volume two VIOS of one system both see counts once; it is assigned when either VIOS reports it
  unavailable for use (mapped, in a volume group, or otherwise in use), and free otherwise. Only
  VIOS-reported volumes count: NPIV-mapped LUNs and disks behind adapters a client partition
  owns are absent, and a system without VIOS reads 0. A LUN that VIOS of two systems see counts
  in both systems. `disk_util_pct` is assigned over total.
- `slots_assigned`, `slots_unassigned`, `slots_sriov`, `slots_empty`: physical I/O slots owned
  by a partition, occupied but unowned, holding an adapter in SR-IOV shared mode, and empty. An
  empty slot counts as empty even when a partition owns it. `slots_util_pct` is assigned plus
  SR-IOV over occupied slots.
- `sriov_adapters`, `sriov_logical_ports`, `sriov_logical_ports_free`: adapters in SR-IOV shared
  mode, the logical ports they support, and those not yet configured. `sriov_util_pct` is
  configured over supported ports.

An absent `IsISCSIBacked` (schema versions before V1_8_0) reads as not iSCSI-backed. A
`*_util_pct` is also `unknown` when its capacity is 0.

A VIOS that is not running reports no storage, so its system's disk columns are `unknown`, and
so are every system's when the HMC cannot read VIOS storage at all.

`unknown` means the HMC did not report a figure or a read failed; it is never written as 0. A
system row's `notes` names the failed read. A roll-up figure sums the systems that reported it,
and the roll-up row's `notes` names every column some of its systems lack, for example
`mem_vios_mib: 1 of 4 systems unknown`. A roll-up's percentage uses only the systems that
reported both configurable and free capacity.

The command surveys 4 profiles at a time (`--concurrency`) and gives each one 300 seconds
(`--hmc-timeout`) for logon and every read. A profile that runs out of time becomes a `failure`
row and keeps none of its readings. Ending its HMC session afterwards is bounded by the client's
own per-request `HMC_TIMEOUT` settings, not by `--hmc-timeout`. A profile whose reads all
finished keeps them even if ending its session fails; stderr carries a warning instead. A
system the HMC cannot list is absent from the report, with only a warning on stderr. The CSV
replaces `PATH` only once it is complete. A run killed by a signal other than Ctrl-C can leave
an empty, owner-only `.<name>.*.tmp` file beside `PATH`; delete it.

The command refuses to run when `HMC_HOST`, `HMC_USER`, `HMC_PASSWORD`, `HMC_PORT` or
`HMC_VERIFY_SSL` is exported, even as an empty value, or when a global connection option
(`--host`, `--user`, `--password`, `--verify-ssl`, `--profile`) is given. An exported value
overrides every profile's own, so one HMC's host, credentials or TLS setting would reach all of
them; the global options would be ignored. `HMC_TIMEOUT` and `HMC_SCHEMA_VERSION` still apply
to every profile.

When profiles fail, the `fleet` row's `notes` says how many and which: a system that only a
failed profile manages is missing from the fleet totals. It also says how many systems
reported no machine type-model-serial: those cannot be deduplicated, so one managed by two
HMCs counts twice. A roll-up over no systems reads 0. A text cell that would start with
`=`, `+`, `-` or `@` is written with a leading `'` so a spreadsheet shows it rather than
evaluating it.


### Managed-system scope for inspection

`lpars show` and `lpars state` now accept `--system NAME_OR_UUID` (or `-s`),
so inspection can carry the same scope as deletion. Without scope, ambiguous
names still fail. With scope, both names and UUIDs must belong to that system;
a UUID previously accepted with an unrelated system is now rejected. Correct
the system selector instead of retrying the mismatched target. This also applies
to shared LPAR/VIOS selectors used by lifecycle and storage commands.

`lpars delete` and `lpars decommission` require `--system` for scoped ownership
and cleanup checks. `lpars capture-console` requires it to select the console
host. A UUID or `--ownership-override` does not remove those requirements.
An unreadable system is reported as unavailable, rather than as an empty system.
