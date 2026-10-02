# 0191 — Provisioning binds producer media and ends when the installer boot has started

## Status

Accepted (2026-10-01), issue #1216.

## Context

Epic #1215 asked `hmc_provision_lpar` to install Ubuntu or Rocky unattended and prove a ready
guest over authenticated SSH. On 2026-10-01 the operator narrowed this. hmcpctl runs no SSH
readiness check and holds no guest credential. Its responsibility ends at the progress code
showing the boot has started, and install success is out of scope.

The media producer is iso-chain-loader. Its v3 manifest already binds a lower-case unicast MAC,
a static network and a credential-free HTTP source (`scripts/iso_chain.py`). Its build emits
only the ISO: no digest and no machine-readable result, and the install disk is hard-coded
`vda`. hmcpctl's `upload_iso` hashes its own download but cannot be given an expected digest,
and never reads the image back from the VIOS (`operations/storage/resources.py`, ADR 0177).
`hmc_read_lpar_refcodes` reads partition reference codes without a console; whether codes are
still reported once the partition is Running is unconfirmed (#879).

## Decision

1. **Terminal outcomes.** A provision with `install` ends in exactly one of:
   - `boot_started`: a reference code whose HMC timestamp is later than this operation's
     power-on submission, and whose value is in the boot-started set;
   - `needs_attention`: the wait ended without such a code, with the codes observed;
   - `failed`: an HMC write or a binding check failed.

   The boot-started set is data that native proof (#1230) fills. While it is empty, no
   operation reports `boot_started`. Without `install`, provision ends `configured`.
2. **Media binding.** Media enters either as *prepared* (an allowlisted URL plus expected
   SHA-256 and size) or as *built*, by one operator-configured build entry. Before upload,
   hmcpctl requires a versioned producer result, iso-chain-loader#23's contract, whose ISO digest
   and size, distribution, release, architecture, MAC, static network and operation binding equal
   the request and the recorded effects. The MAC is the client adapter's actual MAC, read after
   the adapter is created. Any mismatch is `failed`; hmcpctl never rebuilds or overwrites media
   on its own.
3. **Builder invocation.** The build entry is an executable path plus a fixed argument
   template from deployment configuration. Only the manifest file is supplied per call. The
   caller cannot name a program, a URL to executable code, or extra arguments. The entry
   publishes the ISO to an origin in `HMC_ISO_URL_ALLOWLIST` and returns its URL in the result.
4. **Upload integrity.** `upload_iso` compares its download digest with the bound digest
   before committing the upload. The result says the digest was verified on download and the
   VIOS copy was not read back.
5. **Root disk.** Create attaches the new owned virtual disk as the partition's only disk, so
   "the one disk" is the install target. The producer must refuse to install unless exactly one
   non-optical disk is present. Create never installs over a pre-existing disk.
6. **After `boot_started`.** The installer media stays mounted and the boot order stays as
   provision set it. Detaching the media and booting the installed disk are later explicit
   reconfigure actions. Caller SSH public keys and the login user are opaque inputs passed to
   the producer and never used by hmcpctl.

## Consequences

- `boot_started` does not mean the install succeeded, or even that it ran. The handoff reports
  every guest fact as `declared` or `unverified`.
- #1222 (guest SSH readiness) is obsolete as filed, and #1230's acceptance shrinks to
  reaching `boot_started` per distro. Epic #1215 requirement 7 and its success criteria, and the
  host-identity wording in iso-chain-loader#24 and #25, now conflict with this record. #1216's
  PR lists them as follow-ups.
- No `boot_started` claim is possible until #1230 records native codes.
- A partition left with mounted media boots the installer again if its disk is not bootable.
  That is the caller's to resolve with reconfigure.

## Considered & rejected

- **Verify the guest over SSH with a pinned host key.** judgment: fit. The operator ruled guest
  readiness a user concern, and it would make hmcpctl a holder of guest credentials.
- **Treat partition state `Running` as boot started.** judgment: fit. `Running` follows
  activation, before firmware has loaded any boot image.
- **Detect boot through a console marker.** judgment: fit. Console acquisition takes a shared,
  holder-anonymous session (ADR 0172), and a reference code reaches the same boundary read-only.
- **Let hmcpctl build the media itself.** judgment: fit. The epic forbids a second media
  builder.
- **Accept a caller-supplied builder command.** judgment: fit. That is arbitrary code execution
  on the server host.
