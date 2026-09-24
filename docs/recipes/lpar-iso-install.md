# LPAR ISO installation recipe

> **Status: exercised live, not yet runnable on `main`.** These steps ran in this order, from
> creation through cleanup, on 2026-09-23 against HMC V10R3 M1060, and the ISO booted into its
> installer. The run used a build patched for #935 and #979, and imported the ISO outside
> `hmcpctl` (#978). Unpatched, the recipe stops at `adapters add-network` with HTTP 406 (#935).
> These issues must land before the recipe runs on `main`: #935 and #979. Each step that
> depends on an open issue names it.

This recipe creates one powered-off LPAR, gives it a virtual network adapter and a VIOS-backed
virtual disk, puts an installation ISO in the VIOS media repository, mounts it, and boots the
partition from it. It then powers the partition off and unmounts the ISO. The last section
removes the disk mapping, disk, ISO, repository, and partition.
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
  volume-group, virtual-disk, media-repository, or mapping write, the error says the write may
  have changed state and whether a read-back succeeded (ADR 0136). A 5xx on the `upload-iso`
  transfer says the HMC may already hold the ISO (ADR 0177). Neither prints that state. **Do not retry.** Run `storage list-vgs`, `storage list-mappings`, and
  `storage list-optical-media` to see what changed, and reconcile by hand first.
- **Mapping removals rewrite the whole VIOS document.** `unmount-optical-media` and
  `detach-mapping` read the VIOS, remove one mapping, and write the whole document back. A
  concurrent writer's change in that window is lost. `storage map` and `mount-optical-media`
  add a mapping without that rewrite, but they still change shared VIOS state. Serialize every
  mapping writer on the chosen VIOS for the length of this run, and take a fresh
  `storage list-mappings` before and after each mapping write.
- **Media-repository writes rewrite the whole volume-group document.** `create-media-repo`,
  `delete-media`, and `delete-media-repo` read the volume group, change it, and write it back,
  so a concurrent change to that group is lost. Serialize every writer on `MEDIA_VG` for the
  run, and compare `storage list-vgs` and `storage list-optical-media` before and after each.
- **Confirmations.** Most mutating commands prompt unless you pass `--yes` (or `--confirm` for
  `unmount-optical-media` and `detach-mapping`). The examples pass it so they copy cleanly. Remove
  it when you run the recipe by hand. `--yes` is not a dry run. `storage upload-iso` never
  prompts: it writes as soon as you run it, so check the listing first. The HMC CLI commands
  below never prompt either.
- **Dry runs.** No global dry-run mode exists. Only commands that advertise `--dry-run` support
  one, such as the composite `storage attach-disk`. The commands below do not. Run the listing
  commands shown before each mutation instead.
- **Ownership.** `lpars create` stamps the new partition with this connection's ownership token.
  Adapter, mapping, optical, power, and delete commands then refuse a partition that another
  owner stamped. Never add `--ownership-override` in this recipe.

## Names and UUIDs

Some commands take names, some take UUIDs, and some take either. Use the variable each example
shows.

| Variable | Value | Where it comes from |
|---|---|---|
| `SYSTEM_NAME` | managed-system name | you choose it |
| `SYSTEM` | managed-system UUID | `systems show` step 1 |
| `VIOS` | VIOS name or UUID | you choose it; `vios list` shows both |
| `VG` | volume-group UUID for the virtual disk | `storage list-vgs` |
| `MEDIA_VG` | volume-group UUID holding the media repository | `storage list-vgs`, step 4 |
| `LPAR_NAME` | new partition name | you choose it |
| `LPAR` | new partition UUID | `lpars create` output |
| `DISK_NAME` | virtual-disk (logical volume) name, at most 15 characters | you choose it |
| `MEDIA_NAME` | ISO name in the media repository, only `A-Z a-z 0-9 _ .` | you choose it |

- `lpars create --system` and `network list-networks` take the managed-system **UUID**.
- Every `--vg`, `VG`, and `MEDIA_VG` argument is a volume-group **UUID**.
- The HMC CLI commands take the managed-system **name** and the partition **name**.
- Every other `--system`, VIOS, and LPAR selector accepts a name or a UUID.
- The VIOS rejects a virtual-disk name longer than 15 characters, and `create-disk`
  refuses a longer one. `upload-iso` refuses a `MEDIA_NAME` with any other character, a hyphen
  included.

## HMC CLI steps

Two things in this recipe have no working `hmcpctl` command yet. Run them in an SSH session on
the HMC, as the HMC user in your connection profile:

- writing the adapters that `hmcpctl` adds into the partition's profile before a profile power-on, until
  #981;
- reading the partition description for the ownership checks. `lpars get-description` prints a
  blank line in place of the stamp until #965 is fixed.

The HMC CLI blocks below use `<angle-bracket>` placeholders, not shell variables. Fill them in
from the same values.

## Prerequisites

- A connection profile for the HMC, with SSH access for the whole run. Ownership stamping at
  create and the ownership guard on every adapter, mapping, optical, power, and delete command
  read the partition description over SSH, as does `capture-console`. SSH needs a trusted host
  key, see [SSH trust setup](../HMC_HINTS.md#ssh-host-key-trust). The HMC CLI steps need an
  interactive SSH login to the HMC as the same user.
- `HMC_AUTHORIZE_POWER_OPERATIONS=true`. It turns on the ownership guard for `power-on` and
  `power-off`, so they refuse a partition another owner stamped. With `--system` the guard
  checks that system directly; without it the guard searches every managed system. Every power
  command below passes `--system`.
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
DISK_NAME=<disk-name-up-to-15-chars>
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
hmcpctl network list-networks "$SYSTEM" --json
hmcpctl storage list-vgs "$VIOS" --system "$SYSTEM" --json
VG=<volume-group-uuid>
hmcpctl lpars list --system "$SYSTEM" --json
```

Expected: `systems show` prints the managed system; copy its `UUID` into `SYSTEM`. `vios list`
shows the VIOS. `list-networks` shows the VLAN you will use. `list-vgs` reports
`free_space_gib` and `capacity_gib`. If `free_space_diagnostic` is not `null`, the HMC reported
a free-space value larger than the group, so do not trust the free-space figure. `lpars list`
confirms that `LPAR_NAME` is not already in use.

The recipe resolves the system by name with `systems show`. On some HMC firmware
`systems list` cannot serialize the full inventory; `systems show` does not need it.

## 2. Create the powered-off partition

A shared-processor partition with more than one virtual processor needs explicit processing
units. The HMC rejects the 0.1-unit default spread over several virtual processors (HSCL0622),
and the `mksyscfg` fallback `lpars create` uses on some firmware refuses before it submits.
Pass all three resource axes explicitly.

```bash
hmcpctl lpars create "$LPAR_NAME" --system "$SYSTEM" \
  --min-mem 4096 --mem 8192 --max-mem 16384 \
  --min-procs 0.1 --procs 0.5 --max-procs 1.0 \
  --min-vcpus 1 --vcpus 2 --max-vcpus 2 \
  --caller-token "$RUN_TOKEN" --yes
LPAR=<lpar-uuid-from-create-output>
hmcpctl lpars state "$LPAR"
```

Expected: `Created LPAR '<new-lpar-name>'` and the partition as JSON; copy its `UUID` into
`LPAR`. The output's `steps` include `apply_profile` with status `ok`, and the partition has an
`AssociatedPartitionProfile`: `lpars create` applies the new profile itself (#939), which the
adapter writes in step 3 and the profile power-on in step 5 need. `lpars state` prints
`not activated`. If `lpars create` warns that it could not stamp ownership, or that the profile
was not applied, stop.

Then check the ownership stamp on the HMC CLI:

```text
lssyscfg -r lpar -m <managed-system-name> --filter lpar_names=<lpar-name> -F description
```

The description carries the ownership stamp and `[caller <run-unique-token>]`.

## 3. Network adapter and virtual disk

`add-network`, `create-disk` and `storage map` need #935.

```bash
hmcpctl adapters add-network "$LPAR" --vlan "$VLAN_ID" --system "$SYSTEM" --yes
hmcpctl adapters list "$LPAR" --system "$SYSTEM" --json
hmcpctl storage create-disk "$VIOS" --vg "$VG" --name "$DISK_NAME" --capacity-mib 51200 \
  --system "$SYSTEM" --yes
hmcpctl storage list-vgs "$VIOS" --system "$SYSTEM" --json
hmcpctl storage list-mappings "$VIOS" --lpar "$LPAR" --system "$SYSTEM" --json
hmcpctl storage map "$VIOS" --lpar "$LPAR" --disk "$DISK_NAME" --system "$SYSTEM" --yes
hmcpctl storage list-mappings "$VIOS" --lpar "$LPAR" --system "$SYSTEM" --json
```

Expected: `add-network` and `storage map` each end with a `CurrentProfileSync is ...` line that
says whether the new adapter lives only in the current configuration; step 5 depends on it.
`adapters list` shows the new network adapter on `VLAN_ID`. `--capacity-mib` must be
a positive multiple of 1024; the CLI converts it to the whole GiB the HMC takes. The second
`list-vgs` shows the new disk in `VG`. `storage map` creates its own vSCSI client and server
adapter pair, so do not add a vSCSI adapter first: `adapters add-vscsi` would leave an unused
one behind. The last `list-mappings` shows one new mapping for `DISK_NAME` and nothing else
changed.

## 4. Media repository, ISO, and mount

A VIOS has at most one media repository, and it may sit in a different volume group from the
disk. Run `get-media-repo` against each volume group `list-vgs` returned, starting with the
VIOS's `rootvg`. Set `MEDIA_VG` to the group that holds the repository.

```bash
MEDIA_VG=<volume-group-uuid-holding-the-media-repository>
hmcpctl storage get-media-repo "$VIOS" "$MEDIA_VG" --system "$SYSTEM" --json
```

If `get-media-repo` shows no repository, create one. `--size-mib` must be a multiple of 1024
and reaches the HMC as whole GiB, so the command below creates a 20 GiB repository. It needs
#935.

```bash
hmcpctl storage create-media-repo "$VIOS" "$MEDIA_VG" --size-mib 20480 --system "$SYSTEM" --yes
```

Put the ISO in the repository and mount it on the partition. `upload-iso` sends the ISO
through the HMC web File API and reports `uploaded` once the repository lists it.
`mount-optical-media` needs #935.

```bash
hmcpctl storage upload-iso "$VIOS" "$MEDIA_VG" "$MEDIA_NAME" "$ISO_URL" --system "$SYSTEM" --json
hmcpctl storage list-optical-media "$VIOS" "$MEDIA_VG" --system "$SYSTEM" --json
hmcpctl storage list-mappings "$VIOS" --lpar "$LPAR" --system "$SYSTEM" --json
hmcpctl storage mount-optical-media "$VIOS" "$LPAR" "$MEDIA_NAME" --system "$SYSTEM" --yes
hmcpctl storage list-mappings "$VIOS" --lpar "$LPAR" --system "$SYSTEM" --json
```

Expected: `upload-iso` reports `uploaded` with the size and SHA-256 it computed. It refuses a
`MEDIA_NAME` that already exists in the repository. `list-optical-media` lists `MEDIA_NAME`; its
`size_mib` may be `null`. `mount-optical-media` prints the new optical mapping and creates
another vSCSI adapter pair for it. The last `list-mappings` shows the disk mapping from step 3
and the new optical mapping, and nothing else changed.

## 5. Boot from the ISO and observe

When steps 3 and 4 printed `CurrentProfileSync is Disabled` (or `Suspended`), their adapters
exist only in the partition's current configuration. A power-on with `--partition-profile`
activates the profile and drops them, and the firmware stops at SMS with no boot devices.
`power-on` prints one `Warning:` line for each current adapter the profile lacks, but still
activates. When sync is `On` the HMC already wrote them to the profile; skip to the power-on.
Otherwise write them into the profile on the HMC CLI first. List the partition's virtual SCSI
and Ethernet adapters:

```text
lshwres -r virtualio --rsubtype scsi -m <managed-system-name> --level lpar --filter lpar_names=<lpar-name>
lshwres -r virtualio --rsubtype eth -m <managed-system-name> --level lpar --filter lpar_names=<lpar-name>
```

An earlier 2026-09-23 run first powered on without this step, stopped at SMS, then wrote the
profile and powered on again. Write every adapter those listings show, both vSCSI client adapters and the Ethernet
adapter, into the profile. Take each field from the listing; the `chsyscfg` help on the HMC
gives the field order for both attributes:

```text
chsyscfg -r prof -m <managed-system-name> -i 'name=default_profile,lpar_name=<lpar-name>,"virtual_scsi_adapters=<disk-adapter>,<optical-adapter>","virtual_eth_adapters=<ethernet-adapter>"'
```

Then read the profile UUID from the partition and power on against it:

```bash
hmcpctl lpars show "$LPAR" --json
PROFILE_UUID=<uuid-at-the-end-of-AssociatedPartitionProfile-href>
hmcpctl lpars power-on "$LPAR" --system "$SYSTEM" --partition-profile "$PROFILE_UUID" \
  --wait --timeout 900 --interval 5 --yes
JOB_ID=<job-id-from-power-on-output>
hmcpctl jobs show "$JOB_ID"
hmcpctl lpars state "$LPAR"
CONSOLE_LOG=<new-file-for-the-console-bytes>
hmcpctl lpars capture-console "$LPAR_NAME" --system "$SYSTEM_NAME" --duration 30 \
  --max-bytes 65536 --idle-timeout 10 --output "$CONSOLE_LOG"
```

Expected: `power-on` prints `Job submitted for <lpar-uuid>` and the finished job, whose status
is `COMPLETED_OK`, and no `Warning:` line. A warning names an adapter the profile lacked, which
the activation removed: power the partition off, re-run the step 3 or 4 command that created
it, write it into the profile as above from the new listing, and power on again. `jobs show` prints the same job. `lpars state` prints `running` or
`open firmware`. No boot order is set: the firmware booted the virtual CD because the new disk
is blank. This path does not need the boot-order commands. `lpars set-boot-order` takes Open
Firmware device paths, and a never-booted partition reports none. A V10R3 HMC accepts no value that
clears a pending boot order, so `clear-boot-order` refuses and writes nothing (#1048). A pending
boot order, once set, is replaced with `set-boot-order`. A profile activation (`chsysstate -o on`)
consumed it when observed on V10R3; whether this recipe's `power-on` job does is unverified.

`capture-console` records at most `--duration` seconds and `--max-bytes` bytes, and stops
after `--idle-timeout` seconds without output. It never sends input to the partition. It writes
the raw bytes to `CONSOLE_LOG`, which must not exist yet, and prints one line to stderr, for
example `stop reason: idle; bytes: 2048; released: true`. The bytes carry terminal escape
sequences: read them with `less -R` or a log viewer. The capture holds the partition's single
console session while it runs. Exit status 1 with a message that the console is held means
another session has it open; the command leaves that session alone. Exit status 3 means
`released: false`: the console may still be held, so run the `rmvterm` command the line names
on the HMC before another capture. If the line instead says another client ended the hold
(`ConsoleHoldLostError`), leave that client's session alone. Repeat the capture, each time to a new file, to follow the
installer's progress. UUID selectors resolve the partition name through the HMC CLI
`uuid,name` lookup; the 2026-09-23 run captured the console both by UUID and by name.

## 6. After installation: power off and unmount the ISO

Once the installer has finished writing to disk, power the partition off. Add `--immediate`
only if a graceful shutdown hangs.

```bash
hmcpctl lpars power-off "$LPAR" --system "$SYSTEM" --wait --timeout 900 --interval 5 --yes
hmcpctl lpars state "$LPAR"
```

Do not continue until the state reads `not activated`. Then unmount the ISO:

```bash
hmcpctl storage list-mappings "$VIOS" --lpar "$LPAR" --system "$SYSTEM" --json
hmcpctl storage unmount-optical-media "$VIOS" "$LPAR" "$MEDIA_NAME" --system "$SYSTEM" --confirm
hmcpctl storage list-mappings "$VIOS" --lpar "$LPAR" --system "$SYSTEM" --json
```

Expected: the second `list-mappings` shows the optical mapping gone and the disk mapping
unchanged. The ISO stays in the repository. The 2026-09-23 run powered off gracefully from the
installer and then unmounted, in this order.

`unmount-optical-media` and `detach-mapping` refuse on a live HMC until #979 lands. Unmount
only after power-off. On a running partition an earlier 2026-09-23 run got HTTP 500 HSCL2957 (no RMC
connection to the partition), after the VIOS had already removed the optical device and its
server adapter. The client adapter stayed on the partition. If that happens, treat it as a
partial change: compare `list-mappings` and `adapters list` with the state you expect.

The live run did not boot the installed disk, so this recipe does not document that step.

## 7. Optional cleanup (destructive)

Run this section only to tear the partition down. Every command in it destroys something.
Run each one by hand and check the identity it names before you confirm. The partition must be
powered off, as in step 6.

Detach the disk mapping (#979). First check on the HMC CLI that the description still carries
`[caller <run-unique-token>]`:

```text
lssyscfg -r lpar -m <managed-system-name> --filter lpar_names=<lpar-name> -F description
```

```bash
hmcpctl storage list-mappings "$VIOS" --lpar "$LPAR" --system "$SYSTEM" --json
MAPPING_ID=<identity-of-the-DISK_NAME-mapping-from-list-mappings>
hmcpctl storage detach-mapping "$VIOS" "$MAPPING_ID" --system "$SYSTEM" --confirm
hmcpctl storage list-mappings "$VIOS" --lpar "$LPAR" --system "$SYSTEM" --json
```

Delete the virtual disk, then the ISO. `delete-disk` refuses a disk that is still mapped.

```bash
hmcpctl storage delete-disk "$VIOS" --vg "$VG" --name "$DISK_NAME" --system "$SYSTEM" --yes
hmcpctl storage delete-media "$VIOS" "$MEDIA_VG" "$MEDIA_NAME" --system "$SYSTEM" --yes
```

Delete the media repository only if this run created it and it holds nothing else:

```bash
hmcpctl storage list-optical-media "$VIOS" "$MEDIA_VG" --system "$SYSTEM" --json
hmcpctl storage delete-media-repo "$VIOS" "$MEDIA_VG" --system "$SYSTEM" --yes
```

Delete the partition last, after the same description check on the HMC CLI; stop if it no
longer carries your caller token.

```bash
hmcpctl lpars delete "$LPAR" --system "$SYSTEM" --yes
hmcpctl lpars list --system "$SYSTEM" --json
```

Expected: `lpars delete` prints `Deleted LPAR <lpar-uuid>`, and `lpars list` no longer shows
the partition. On 2026-09-23 (HMC V10R3 M1060), `lpars delete` also removed the partition's
VIOS server adapter. Afterwards the VIOS mappings and the volume group matched their state
before the run.

## Recovery

If a step fails, stop and keep the transcript and any job ID. Then:

1. Read the current state: `lpars state`, `jobs show`, `adapters list`, `storage list-vgs`,
   `storage list-mappings`, and `storage list-optical-media`.
2. On a storage or VIOS error that reports a possible side effect, do not retry. Compare the
   listings above with the state you expected and reconcile by hand.
3. Resume at the first step whose expected result you cannot see. Do not repeat a step whose
   resource already exists. Its create command either refuses the duplicate or makes a second
   one.
