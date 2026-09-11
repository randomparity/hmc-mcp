# LPAR ISO installation recipe

This is an explicit, resumable operator sequence for installing an LPAR from an ISO.
Run it only against a connection and managed system you own. Keep the transcript and job IDs.

## Preconditions and placeholders

Configure a profile with the HMC credentials and an `HMC_ISO_URL_ALLOWLIST` entry for the ISO host.
Use names or UUIDs consistently within one run; UUIDs are preferred for destructive steps.

| Placeholder | Meaning |
|---|---|
| `SYSTEM` / `VIOS` / `LPAR` | managed-system, VIOS, and partition selectors |
| `VLAN` / `VG` / `ISO_URL` | network VLAN, volume group, and allowlisted HTTPS URL |

```bash
SYSTEM=<system-uuid>
VIOS=<vios-uuid>
LPAR_NAME=<new-lpar-name>
LPAR=<lpar-uuid-after-create>
VLAN=100
VIOS_ID=2
VIOS_SLOT=20
VG=<volume-group-uuid>
ISO_URL=https://<allowlisted-host>/images/install.iso
```

## Discover the environment

```bash
hmc-mcp config show
hmc-mcp systems list --json
hmc-mcp vios list --json
hmc-mcp lpars list --system "$SYSTEM" --json
hmc-mcp network list-networks "$SYSTEM" --json
hmc-mcp storage list-vgs "$VIOS" --system "$SYSTEM" --json
hmc-mcp adapters list "$LPAR" --type VirtualSCSIClientAdapter --json
```

Record the VIOS partition ID, available server slot, VLAN/network identity, and VG free space.

## Create the powered-off partition

```bash
hmc-mcp lpars create "$LPAR_NAME" --system "$SYSTEM" --mem 8192 --vcpus 2 --yes
hmc-mcp lpars state "$LPAR"
hmc-mcp lpars get-description "$LPAR" "$SYSTEM"
```

Confirm the new partition is powered off before adding devices.

## Network, vSCSI, and disk

```bash
hmc-mcp adapters add-network "$LPAR" --vlan 100 --yes
hmc-mcp adapters add-vscsi "$LPAR" --vios-id "$VIOS_ID" --vios-slot "$VIOS_SLOT" --yes
hmc-mcp storage create-disk "$VIOS" --vg "$VG" --name install-root --capacity-mib 51200 --system "$SYSTEM" --yes
hmc-mcp storage map "$VIOS" --lpar "$LPAR" --disk install-root --system "$SYSTEM" --yes
hmc-mcp storage list-mappings "$VIOS" --lpar "$LPAR" --system "$SYSTEM" --json
```

Save the mapping identity and verify it is visible before boot configuration.

## Media repository, ISO upload, and mount

```bash
hmc-mcp storage create-media-repo "$VIOS" "$VG" --size-mib 20480 --system "$SYSTEM" --yes
hmc-mcp storage get-media-repo "$VIOS" "$VG" --system "$SYSTEM" --json
hmc-mcp storage upload-iso "$VIOS" "$VG" install.iso "$ISO_URL" --system "$SYSTEM" --json
hmc-mcp storage list-optical-media "$VIOS" "$VG" --system "$SYSTEM" --json
hmc-mcp storage mount-optical-media "$VIOS" "$LPAR" install.iso --system "$SYSTEM" --yes
```

The upload URL must match the configured allowlist. If the media name differs, use the exact name
returned by `list-optical-media`.

## Optical-first boot, power-on, and console evidence

```bash
hmc-mcp lpars read-boot-order "$SYSTEM" "$LPAR"
hmc-mcp lpars set-boot-order "$SYSTEM" "$LPAR" "cd,disk"
hmc-mcp lpars power-on "$LPAR" --system "$SYSTEM" --wait --timeout 900 --interval 5 --yes
hmc-mcp jobs show <power-on-job-uuid>
hmc-mcp lpars state "$LPAR"
hmc-mcp lpars capture-console "$LPAR" "$SYSTEM" --duration 30 --max-bytes 65536 --idle-timeout 10 --json
hmc-mcp console info
```

Capture output is bounded and base64 encoded in JSON. Decode it only in a controlled log viewer;
do not paste raw console bytes into a terminal.

## Restore the disk-first configuration

Serialize all VIOS mapping writers. Immediately before unmounting, take a fresh mapping inventory;
stop and reconcile if it differs from the expected mapping. Unmount, then take another fresh
inventory and stop if any unexpected mapping changed.

```bash
hmc-mcp storage list-mappings "$VIOS" --lpar "$LPAR" --system "$SYSTEM" --json
hmc-mcp storage unmount-optical-media "$VIOS" "$LPAR" install.iso --system "$SYSTEM" --confirm
hmc-mcp storage list-mappings "$VIOS" --lpar "$LPAR" --system "$SYSTEM" --json
hmc-mcp lpars set-boot-order "$SYSTEM" "$LPAR" "disk,cd"
hmc-mcp lpars read-boot-order "$SYSTEM" "$LPAR"
hmc-mcp lpars clear-boot-order "$SYSTEM" "$LPAR"
```

## Recovery and optional cleanup

On a failed job, preserve the job payload and current state, then stop rather than retrying a
destructive step. A not-proven console release requires operator recovery before another capture.
Cleanup is optional and destructive; use explicit confirmation and ownership approval.

```bash
hmc-mcp lpars power-off "$LPAR" --system "$SYSTEM" --wait --timeout 900 --yes
hmc-mcp storage detach-mapping "$VIOS" <mapping-uuid>
hmc-mcp storage delete-disk "$VIOS" --vg "$VG" --name install-root --yes
hmc-mcp storage delete-media "$VIOS" "$VG" install.iso --yes
hmc-mcp storage delete-media-repo "$VIOS" "$VG" --yes
hmc-mcp lpars delete "$LPAR" --system "$SYSTEM" --yes
```

Never run the cleanup block automatically. Confirm the partition, disk, media, repository, and
mapping identities one more time immediately before each deletion.
