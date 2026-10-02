# 0191 — Provisioning binds producer media and ends when the installer boot has started

## Status

Accepted (2026-10-01), issue #1216.

## Context

Epic #1215 asked `hmc_provision_lpar` to install Ubuntu or Rocky unattended and prove a ready
guest over authenticated SSH. On 2026-10-01 the operator narrowed this: hmcpctl runs no SSH
readiness check and holds no guest credential, its responsibility ends at the progress code
showing the boot has started, and install success is out of scope. The operator also kept one
call as the default, and chose that a caller who wants the console from its first byte defers
the boot, attaches their own console, and then proceeds. hmcpctl captures nothing itself.

The media producer is iso-chain-loader. Its v3 manifest binds a lower-case unicast MAC, a static
network and a credential-free HTTP source (`scripts/iso_chain.py`). Its build emits only the
ISO, with no digest or machine-readable result, and hard-codes the install disk as `vda`.
`upload_iso` hashes its own download but cannot be given an expected digest, and never reads
the VIOS copy back (`operations/storage/resources.py`, ADR 0177). `hmc_read_lpar_refcodes`
reads reference codes without a console. Its `time_stamp` field carries no zone
(`tests/unit/test_ssh_refcodes.py`), and whether codes are still reported once the partition is
Running is unconfirmed (#879).

POWER offers no one-time boot device. `chsysstate -b` selects a boot *mode* (`norm`, `dd`, `ds`,
`of`, `sms`) for one activation (`docs/refs/hmc-commands-p10/commands/chsysstate.md`). The device
order hmcpctl writes is `PendingBootString` (`hmc_set_lpar_boot_order`), and whether it persists
across activations is unverified.

## Decision

1. **Boot timing.** `boot: immediate` is the default, and provision runs through to the boot.
   With `boot: deferred`, the operation stops at `ready_to_boot`:
   - the partition is powered off;
   - the media is uploaded, verified and mounted;
   - the boot order is set.

   `continuation: boot` on the same `request_id` then powers it on. Before power-on it
   revalidates the mount, the boot order, the media binding and the hold. hmcpctl never
   acquires the console during boot, so a console the caller holds is undisturbed.
2. **Boot started.** Immediately before power-on, hmcpctl reads the partition's newest
   reference code as a baseline. `boot_started` requires both:
   - a later row, by HMC timestamp and HMC row order, so hmcpctl's own clock is never compared;
   - a code in the boot-started set.

   Native proof (#1230) fills the boot-started set, and also settles the timestamp's zone
   handling. While the set is empty, no operation reports `boot_started`. A wait that ends with
   no qualifying code reports `needs_attention` with the codes it observed.
3. **Media binding.** Media enters either *prepared* (an allowlisted URL plus the expected
   SHA-256 and size) or *built* (one operator-configured build entry). Before upload, hmcpctl
   requires a versioned producer result, iso-chain-loader#23's contract. Its ISO digest and
   size, distribution, release, architecture, MAC, network and operation binding must equal the
   request and the recorded effects. The MAC is the client adapter's actual MAC, read after
   creation. A mismatch is `failed`; hmcpctl never rebuilds or overwrites media.
4. **Builder invocation.** The build entry is an executable path plus a fixed argument
   template, both from deployment configuration. Only the manifest file varies per call, and no
   shell runs. The entry publishes the ISO to an origin in `HMC_ISO_URL_ALLOWLIST` and returns
   its URL.
5. **Upload integrity.** `upload_iso` compares the download digest with the bound digest
   before committing the upload. Results state that the VIOS copy was not read back.
6. **Disk and reinstall safety.** Provision attaches the new owned disk as the partition's only
   disk and sets the boot order to installer media first, once. The producer contract
   (iso-chain-loader#23–#25) must keep a reboot from reinstalling:
   - the launcher's default entry boots the installed disk when it carries a boot record, after
     a bounded menu timeout, and boots the installer otherwise;
   - the installer refuses unless exactly one non-optical disk is present and that disk is blank.
7. **After `boot_started`.** The media stays mounted. Detaching it and reordering the boot are
   explicit reconfigure actions (#1228). Caller SSH public keys and the login user are opaque
   producer inputs that hmcpctl never uses.

## Consequences

- `boot_started` does not mean the install ran or succeeded. Every guest fact is `declared` or
  `unverified`.
- No `boot_started` claim is possible until #1230 records native codes.
- First-byte console observation needs two calls (deferred, then `continuation: boot`) and a
  console the caller holds.
- Reinstall safety rests on producer behavior hmcpctl cannot observe. A launcher that defaults
  to the installer would loop if the installer did not refuse non-blank disks.
- #1222 is obsolete as filed, and #1230 shrinks to reaching `boot_started` per distro. Epic
  #1215 requirement 7 and iso-chain-loader#24/#25's host-identity wording conflict; #1216's PR
  lists them.

## Considered & rejected

- **Verify the guest over SSH with a pinned host key.** judgment: fit. The operator ruled guest
  readiness a user concern.
- **Treat partition state `Running` as boot started.** judgment: fit. The state reports
  activation, not which image firmware loaded. Whether codes progress after Running is #879's
  observation.
- **Capture the console from first byte inside hmcpctl.** judgment: fit. The operator chose
  deferral with a caller-held console. A built-in capture would take the single vterm hold
  (ADR 0172) the caller wants for themselves.
- **One-time boot from media.** verified: `chsysstate -b` accepts boot modes only, not devices
  (`docs/refs/hmc-commands-p10/commands/chsysstate.md`, `-b`). judgment: `PendingBootString`'s
  persistence is unverified.
- **Network or PXE boot.** judgment: fit. Epic #1215 excludes DHCP, PXE and NIM.
- **Let hmcpctl build media, or accept a caller-supplied builder command.** judgment: fit. The
  epic forbids a second builder, and a caller command is arbitrary execution on the server host.
