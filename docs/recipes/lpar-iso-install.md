# LPAR ISO installation recipe

> **Unverified.** No live run has executed this recipe on current code yet. The v0.1.0 live
> window (#879) will run it and correct each expected result. Until then, treat the expected
> results as intended behaviour, not recorded hardware behaviour.

This recipe creates one powered-off LPAR, gives it a virtual network adapter and a VIOS-backed
virtual disk, uploads an installation ISO, boots the partition from that ISO, observes it, then
unmounts the ISO and restores a disk-first boot order. The last section tears everything down.
Run it only against an HMC, managed system, and VIOS you own.

> **Sensitive data:** command output contains system, VIOS, and partition names, UUIDs, VLANs,
> and console text. Keep transcripts in a protected location. Do not paste real values into
> issues, pull requests, or this file. The values below are placeholders.

## Safety model

- **Nothing here is atomic.** Each command commits its own change. When a later step fails,
  earlier resources stay in place. Inspect state and resume at the first incomplete step. Do not
  start again from the top.
- **VolumeGroup and VIOS writes can fail after they take effect.** HMC V10R3 has returned
  HTTP 500 after a VolumeGroup write had already changed storage (#779). On a 5xx from a
  volume-group, virtual-disk, media-repository, ISO-import, or mapping write, the CLI reads
  the state back and reports a possible side effect (ADR 0136). **Do not retry.** Read the
  reported state, compare it with `storage list-vgs` and `storage list-mappings`, and reconcile
  by hand first.
- **VIOS mapping writes rewrite the whole VIOS document.** `storage map`,
  `mount-optical-media`, `unmount-optical-media`, and `detach-mapping` read the VIOS, change
  it, and write it back. A concurrent writer's change in that window is lost. Serialize every
  mapping writer on the chosen VIOS for the length of this run, and take a fresh
  `storage list-mappings` before and after each mapping write.
- **Confirmations.** Every mutating command prompts unless you pass `--yes` (or `--confirm` for
  `unmount-optical-media` and `detach-mapping`). The examples pass it so they copy cleanly. Remove
  it when you run the recipe by hand. `--yes` is not a dry run.
- **Dry runs.** No global dry-run mode exists. Only commands that advertise `--dry-run` support
  one, such as the composite `storage attach-disk`. The commands below do not. Run the listing
  commands shown before each mutation instead.
- **Ownership.** `lpars create` stamps the new partition with this connection's ownership token.
  Adapter, mapping, optical, boot-order, power, and delete commands then refuse a partition that
  another owner stamped. Never add `--ownership-override` in this recipe.

## Names and UUIDs

Some commands take names, some take UUIDs, and some take either. Use the variable each example
shows.

| Variable | Value | Where it comes from |
|---|---|---|
| `SYSTEM_NAME` | managed-system name | you choose it |
| `SYSTEM` | managed-system UUID | `systems show` step 1 |
| `VIOS` | VIOS name or UUID | you choose it; `vios list` shows both |
| `VIOS_ID` | VIOS partition ID (integer) | `PartitionID` in `vios list --json` |
| `VIOS_SLOT` | unused VIOS virtual slot (integer) | the HMC, see step 1 |
| `VG` | volume-group UUID | `storage list-vgs` |
| `LPAR_NAME` | new partition name | you choose it |
| `LPAR` | new partition UUID | `lpars create` output |
| `DISK_NAME` | virtual-disk (logical volume) name | you choose it |
| `MEDIA_NAME` | ISO name in the media repository | you choose it |

- `lpars create --system` and `network list-networks` take the managed-system **UUID**.
- `lpars get-description` takes the partition **name** and the managed-system **name**. It runs
  over SSH and does not resolve UUIDs.
- `lpars read-boot-order`, `set-boot-order`, and `clear-boot-order` take the managed-system
  **name**, then the partition name or UUID.
- Every `--vg` and `VG` argument is a volume-group **UUID**.
- Every other `--system`, VIOS, and LPAR selector accepts a name or a UUID.

## Prerequisites

- A connection profile for the HMC, with SSH access for the SSH-backed commands
  (`get-description`, boot order, `capture-console`). SSH access needs a trusted host key, see
  [SSH trust setup](../HMC_HINTS.md#ssh-host-key-trust).
- `HMC_AUTHORIZE_POWER_OPERATIONS=true`. It turns on the ownership guard for `power-on` and
  `power-off`, so they refuse a partition another owner stamped. The guard needs a
  managed-system selector: every power command below passes `--system`.
- A running VIOS with a volume group that has room for the virtual disk and, if one does not
  exist yet, the virtual media repository.
- An ISO served over HTTP or HTTPS. **The machine running `hmcpctl` downloads the ISO, not the
  HMC.** The URL must be reachable from that machine, its host must be on
  `HMC_ISO_URL_ALLOWLIST` (`host` or `host:port`, no scheme or path), and it must not redirect.
  An empty allowlist refuses every URL. A local file path is not accepted. To stage a local
  file, serve its directory on a loopback port and allowlist `localhost:<port>`.

```bash
export HMC_PROFILE=<profile-name>
export HMC_AUTHORIZE_POWER_OPERATIONS=true
export HMC_ISO_URL_ALLOWLIST=<iso-host-or-host:port>
SYSTEM_NAME=<managed-system-name>
VIOS=<vios-name-or-uuid>
LPAR_NAME=<new-lpar-name>
RUN_TOKEN=<run-unique-token>
DISK_NAME=<virtual-disk-name>
MEDIA_NAME=install.iso
ISO_URL=https://<iso-host>/images/install.iso
VLAN_ID=<port-vlan-id>
hmcpctl config list
hmcpctl config show
hmcpctl console info
```

Expected: `config list` marks the intended profile. `config show` reports it with
`authorize_power_operations` set to `True`. `console info` prints the HMC version.

## 1. Discover the environment

```bash
hmcpctl systems show "$SYSTEM_NAME" --json
SYSTEM=<managed-system-uuid-from-UUID-field>
hmcpctl vios list --system "$SYSTEM" --json
VIOS_ID=<vios-PartitionID>
VIOS_SLOT=<unused-vios-virtual-slot>
hmcpctl network list-networks "$SYSTEM" --json
hmcpctl storage list-vgs "$VIOS" --system "$SYSTEM" --json
VG=<volume-group-uuid>
hmcpctl lpars list --system "$SYSTEM" --json
```

Expected: `systems show` prints the managed system; copy its `UUID` into `SYSTEM`. `vios list`
shows the VIOS with its `PartitionID`. `list-networks` shows the VLAN you will use.
`list-vgs` reports `free_space_gib` and `capacity_gib`. If `free_space_diagnostic` is not
`null`, the HMC reported a free-space value larger than the group, so do not trust the free-space
figure. `lpars list` confirms that `LPAR_NAME` is not already in use.

The recipe resolves the system by name with `systems show`. On some HMC firmware
`systems list` cannot serialize the full inventory; `systems show` does not need it.

No `hmcpctl` command lists free VIOS virtual slots. Pick an unused slot number from the HMC
(the VIOS's virtual adapters view, or `lshwres -r virtualio --rsubtype scsi --level lpar`
over the HMC CLI).

## 2. Create the powered-off partition

A shared-processor partition with more than one virtual processor needs explicit processing
units; `lpars create` refuses otherwise. Pass all three resource axes explicitly.

```bash
hmcpctl lpars create "$LPAR_NAME" --system "$SYSTEM" \
  --min-mem 4096 --mem 8192 --max-mem 16384 \
  --min-procs 0.1 --procs 0.5 --max-procs 1.0 \
  --min-vcpus 1 --vcpus 2 --max-vcpus 2 \
  --caller-token "$RUN_TOKEN" --yes
LPAR=<lpar-uuid-from-create-output>
hmcpctl lpars state "$LPAR"
hmcpctl lpars get-description "$LPAR_NAME" "$SYSTEM_NAME"
```

Expected: `Created LPAR '<new-lpar-name>'` and the partition as JSON; copy its `UUID` into
`LPAR`. `lpars state` prints `not activated`. The description carries the ownership stamp and
`[caller <run-unique-token>]`. If `lpars create` warns that it could not stamp ownership, stop:
the later guarded commands will refuse the partition.

## 3. Network adapter, vSCSI adapter, and virtual disk

```bash
hmcpctl adapters add-network "$LPAR" --vlan "$VLAN_ID" --system "$SYSTEM" --yes
hmcpctl adapters add-vscsi "$LPAR" --vios-id "$VIOS_ID" --vios-slot "$VIOS_SLOT" \
  --system "$SYSTEM" --yes
hmcpctl adapters list "$LPAR" --type VirtualSCSIClientAdapter --system "$SYSTEM" --json
hmcpctl storage create-disk "$VIOS" --vg "$VG" --name "$DISK_NAME" --capacity-mib 51200 \
  --system "$SYSTEM" --yes
hmcpctl storage list-vgs "$VIOS" --system "$SYSTEM" --json
hmcpctl storage list-mappings "$VIOS" --lpar "$LPAR" --system "$SYSTEM" --json
hmcpctl storage map "$VIOS" --lpar "$LPAR" --disk "$DISK_NAME" --system "$SYSTEM" --yes
hmcpctl storage list-mappings "$VIOS" --lpar "$LPAR" --system "$SYSTEM" --json
```

Expected: `adapters list` shows the new vSCSI client adapter paired with `VIOS_ID` and
`VIOS_SLOT`. `--capacity-mib` must be a positive multiple of 1024; the CLI converts it to the
whole GiB the HMC takes. The second `list-vgs` shows the new disk in `VG`. The last
`list-mappings` shows one new mapping for `DISK_NAME` and nothing else changed.

## 4. Media repository, ISO upload, and mount

Check for an existing repository first. Create one only if `get-media-repo` reports none. It
must be large enough for the ISO.

```bash
hmcpctl storage get-media-repo "$VIOS" "$VG" --system "$SYSTEM" --json
hmcpctl storage create-media-repo "$VIOS" "$VG" --size-mib 20480 --system "$SYSTEM" --yes
```

Upload the ISO and mount it on the partition:

```bash
hmcpctl storage upload-iso "$VIOS" "$VG" "$MEDIA_NAME" "$ISO_URL" --system "$SYSTEM" --json
hmcpctl storage list-optical-media "$VIOS" "$VG" --system "$SYSTEM" --json
hmcpctl storage list-mappings "$VIOS" --lpar "$LPAR" --system "$SYSTEM" --json
hmcpctl storage mount-optical-media "$VIOS" "$LPAR" "$MEDIA_NAME" --system "$SYSTEM" --yes
hmcpctl storage list-mappings "$VIOS" --lpar "$LPAR" --system "$SYSTEM" --json
```

Expected: `upload-iso` reports `uploaded` with the size and SHA-256 it computed. It refuses a
`MEDIA_NAME` that already exists in the repository. `list-optical-media` lists `MEDIA_NAME`.
`mount-optical-media` prints the new optical mapping. The last `list-mappings` shows the disk
mapping from step 3 and the new optical mapping, and nothing else changed.

## 5. Boot from the ISO and observe

Set an optical-first boot order, then activate the partition against its profile. A PowerOn
names the partition profile by UUID; read it from the partition.

```bash
hmcpctl lpars read-boot-order "$SYSTEM_NAME" "$LPAR"
hmcpctl lpars set-boot-order "$SYSTEM_NAME" "$LPAR" "cd,disk"
hmcpctl lpars read-boot-order "$SYSTEM_NAME" "$LPAR"
hmcpctl lpars show "$LPAR" --json
PROFILE_UUID=<uuid-at-the-end-of-AssociatedPartitionProfile-href>
hmcpctl lpars power-on "$LPAR" --system "$SYSTEM" --partition-profile "$PROFILE_UUID" \
  --wait --timeout 900 --interval 5 --yes
JOB_ID=<job-id-from-power-on-output>
hmcpctl jobs show "$JOB_ID"
hmcpctl lpars state "$LPAR"
hmcpctl lpars capture-console "$LPAR" "$SYSTEM" --duration 30 --max-bytes 65536 \
  --idle-timeout 10 --json
```

Expected: the second `read-boot-order` shows `cd,disk` pending. `power-on` prints
`Job submitted for <lpar-uuid>` and the finished job, whose status is `COMPLETED_OK`.
`jobs show` prints the same job. `lpars state` prints `running` or `open firmware`.

`capture-console` records at most `--duration` seconds and `--max-bytes` bytes, and stops
after `--idle-timeout` seconds without output. It never sends input to the partition. With
`--json` the console bytes are base64 in `data_base64`. Without it, control bytes are printed
escaped. Decode `data_base64` only in a log viewer, never straight into a terminal. The capture
holds the partition's single console session while it runs. If another session already holds
the console, the command fails and leaves that session alone. If `released` is `false`, the
console may still be held: release it deliberately from the HMC before another capture. Repeat
the capture to follow the installer's progress.

## 6. After installation: unmount the ISO and boot from disk

Once the installer has finished writing to disk:

```bash
hmcpctl storage list-mappings "$VIOS" --lpar "$LPAR" --system "$SYSTEM" --json
hmcpctl storage unmount-optical-media "$VIOS" "$LPAR" "$MEDIA_NAME" --system "$SYSTEM" --confirm
hmcpctl storage list-mappings "$VIOS" --lpar "$LPAR" --system "$SYSTEM" --json
hmcpctl lpars set-boot-order "$SYSTEM_NAME" "$LPAR" "disk"
hmcpctl lpars read-boot-order "$SYSTEM_NAME" "$LPAR"
```

Expected: the second `list-mappings` shows the optical mapping gone and the disk mapping
unchanged. The ISO stays in the repository. `read-boot-order` shows `disk` pending for the next
activation.

To restore the HMC default boot order instead of a disk-first order:

```bash
hmcpctl lpars clear-boot-order "$SYSTEM_NAME" "$LPAR"
```

## 7. Optional cleanup (destructive)

Run this section only to tear the partition down. Every command in it destroys something.
Run each one by hand and check the identity it names before you confirm.

Power the partition off. Add `--immediate` only if a graceful shutdown hangs.

```bash
hmcpctl lpars power-off "$LPAR" --system "$SYSTEM" --wait --timeout 900 --interval 5 --yes
hmcpctl lpars state "$LPAR"
```

Do not continue until the state reads `not activated`. Next, detach the disk mapping. The
description must still carry `[caller <run-unique-token>]`.

```bash
hmcpctl storage list-mappings "$VIOS" --lpar "$LPAR" --system "$SYSTEM" --json
MAPPING_ID=<identity-of-the-DISK_NAME-mapping-from-list-mappings>
hmcpctl lpars get-description "$LPAR_NAME" "$SYSTEM_NAME"
hmcpctl storage detach-mapping "$VIOS" "$MAPPING_ID" --system "$SYSTEM" --confirm
hmcpctl storage list-mappings "$VIOS" --lpar "$LPAR" --system "$SYSTEM" --json
```

Delete the virtual disk, then the ISO. `delete-disk` refuses a disk that is still mapped.

```bash
hmcpctl storage delete-disk "$VIOS" --vg "$VG" --name "$DISK_NAME" --system "$SYSTEM" --yes
hmcpctl storage delete-media "$VIOS" "$VG" "$MEDIA_NAME" --system "$SYSTEM" --yes
```

Delete the media repository only if this run created it and it holds nothing else:

```bash
hmcpctl storage list-optical-media "$VIOS" "$VG" --system "$SYSTEM" --json
hmcpctl storage delete-media-repo "$VIOS" "$VG" --system "$SYSTEM" --yes
```

Delete the partition last:

```bash
hmcpctl lpars get-description "$LPAR_NAME" "$SYSTEM_NAME"
hmcpctl lpars delete "$LPAR" --system "$SYSTEM" --yes
hmcpctl lpars list --system "$SYSTEM" --json
```

Expected: the description again carries your caller token; stop if it does not. `lpars delete`
prints `Deleted LPAR <lpar-uuid>`, and `lpars list` no longer shows the partition.

## Recovery

If a step fails, stop and keep the transcript and any job ID. Then:

1. Read the current state: `lpars state`, `jobs show`, `adapters list`, `storage list-vgs`,
   `storage list-mappings`, `storage list-optical-media`, and `lpars read-boot-order`.
2. On a storage or VIOS error that reports a possible side effect, do not retry. Compare the
   reported readback with the listings above and reconcile by hand.
3. Resume at the first step whose expected result you cannot see. Do not repeat a step whose
   resource already exists. Its create command either refuses the duplicate or makes a second
   one.
