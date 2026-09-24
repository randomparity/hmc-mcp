# LPAR ISO Installation CLI Recipe — Design Specification

**Issue:** #776
**ADR:** [ADR 0135](../../adr/0135-cli-lpar-install-lifecycle-surfaces.md)
**Branch:** `feat/lpar-iso-boot-recipe-776`
**Status:** Accepted

## Scope and outcome

Publish a copy-and-adapt operator recipe that starts with HMC connection configuration and ends with
a booted, inspected LPAR whose installation media is detached and boot order is restored. Add only
the missing optical mapping CLI adapters needed to make every step executable through the
installed CLI; console capture uses `hmcpctl lpars capture-console` from #959.

The recipe must satisfy issue #776's eleven lifecycle steps, use stable placeholders for related
resources, identify names versus UUIDs, and explain confirmations, ownership, available dry runs,
partial failure, verification, and optional destructive cleanup. It must be linked from
`docs/cli.md` and `docs/index.md`.

Out of scope are new optical-media or capture semantics, an interactive terminal, MCP contract
changes, dependencies, automatic rollback, and claims of live hardware verification when no suitable
HMC and VIOS are available.

The 2026-09-23 revision below records what the first live run changed. Where it differs from the
sections that follow, the revision governs.

## Public CLI contracts

### Optical media

`hmcpctl storage mount-optical-media VIOS LPAR MEDIA` accepts:

- `--system/-s SYSTEM`, an optional managed-system name or UUID used for selector disambiguation;
- `--target-device DEVICE`, an optional vtscsi device name;
- `--ownership-override`, passed to the existing LPAR mutation authorization;
- `--yes/-y`, which skips a confirmation that identifies the ISO, LPAR, and VIOS.

It calls `mount_optical_media` once and prints an identified success plus the returned mapping as
JSON. Declining confirmation exits without calling the operation.

`hmcpctl storage unmount-optical-media VIOS LPAR MEDIA` accepts:

- `--system/-s SYSTEM` and `--ownership-override` with the same meaning;
- `--confirm/-y`, which skips a prompt that identifies the mapping and states that the backing ISO is
  preserved.

It calls `unmount_optical_media` once and prints an identified success. Missing or ambiguous mappings,
ownership rejection, and HMC failures remain existing operation errors. Declining confirmation exits
without mutation.

### Bounded console capture

The recipe uses `hmcpctl lpars capture-console LPAR --system SYSTEM` from #959 (ADR 0175) with
`--duration 30 --max-bytes 65536 --idle-timeout 10 --output FILE`. This change adds no capture
code.

## Recipe structure

Create `docs/recipes/lpar-iso-install.md` with these ordered sections:

1. prerequisites and a single placeholder table;
2. connection selection and `HMC_ISO_URL_ALLOWLIST` setup;
3. discovery of system, VIOS, network, volume group, and partition identifiers;
4. powered-off LPAR creation with explicit memory and processors;
5. network adapter, vSCSI pair, virtual disk, and mapping;
6. repository inspection or creation, allowlisted ISO upload, and optical mount;
7. optical-first pending boot order, power-on with job wait, state/job/console inspection;
8. post-install unmount and disk-first or default boot order;
9. optional cleanup, with every destructive command labelled before its example.

Each shell example uses one stable set of uppercase shell variables. Commands that require a UUID say
so beside the variable; flexible selectors say name or UUID. The page states that `--dry-run` exists
for the composite `lpars provision` and `storage attach-disk` paths but not for individual media,
mapping, adapter, boot-order, or power commands. It directs operators to list/read commands before
each mutation instead of implying a dry run exists.

The workflow is resumable rather than transactional: after a failure, inspect LPAR state, jobs,
adapters, mappings, optical media, and boot order, then continue at the first incomplete step. Before
`unmount-optical-media`, the operator must serialize mapping writers for the selected VIOS, take a
fresh `storage list-mappings` inventory, and take a second inventory after the call. The existing
unmount operation rewrites the parent VIOS mapping document from a GET snapshot; without caller-side
serialization, another writer's mapping can be lost. If the post-call inventory is unexpected, stop
and reconcile before any retry. Cleanup is opt-in and resource ownership must be checked before
deletion.

## Command verification

A focused test reads shell blocks from the recipe. For every line whose executable is `hmcpctl`, it
resolves the group and command from the Typer/Click tree, validates option names and positional arity
against the leaf command without invoking HMC I/O, and substitutes documented numeric placeholder
values where Click type parsing requires them. This is structural command-contract coverage, not a
snapshot of prose.

Separate focused CLI tests patch the operation boundary and prove both optical commands' argument
forwarding and decline paths and the new help surfaces. #959 owns the capture command's tests.

## Errors and recovery

- Existing CLI runtime handling converts operation exceptions to an actionable stderr error and exit
  status 1.
- An optical unmount that cannot identify exactly one matching mapping performs no POST. Its caller
  must serialize other VIOS mapping writers and verify fresh pre- and post-call inventories because
  the parent-document write has a known lost-update window.
- The recipe never promises rollback. Each completed command is durable and must be inspected before
  retrying or cleaning up.

## Threat model

### Boundary inventory

- **Added:** local CLI selectors and options cross into existing REST and SSH operations.
- **Widened:** existing optical mount/unmount becomes reachable from the installed CLI in addition
  to MCP.

### Actors and trust

The local operator and their selected HMC credentials are trusted to request mutations. HMC and VIOS
responses, LPAR console bytes, profile contents, and copied recipe values are untrusted data. The CLI
does not add a remote listener or multi-tenant boundary.

### Controls

- Existing resolvers constrain system, VIOS, LPAR, and mapping identity; storage operations enforce
  ownership and fail closed on ambiguity.
- Existing document builders and HTTP client paths encode REST values.
- Confirmation gates precede optical mutation; overrides are explicit and named.
- Errors use the existing escaped CLI error path.

### Explicitly out of scope

This design does not reduce the authority of the operator's HMC account, add an access-policy layer to
the local CLI, make multi-command provisioning atomic, or automate recovery of an unproven vterm
release. Those are existing deployment and operation contracts, not widened by the adapters.

## Acceptance evidence

- Focused tests red before each adapter/structural contract exists and green after implementation.
- `just verify` and `uv run --no-sync prek run --all-files` pass on the branch worktree.
- CI validates Python 3.11–3.14 on amd64 and arm64; ppc64le remains a declared but non-PR-gated target.
- No live HMC arm is claimed unless suitable configured hardware is available and the tested build is
  confirmed to match the branch HEAD.

## Revision 2026-09-23: live-run corrections

The recipe ran live on 2026-09-23 (HMC V10R3 M1060) on a build patched for #935, #936, #961, #962
and #979; PR #777 comment 5801497281 records each step. This revision changes the recipe and the
SSH UUID-to-name lookups to match that run. Product fixes stay with their issues.

### Recipe

- The recipe states its live status and the issues that must land before it runs on `main`:
  #935, #936, #961, #962, #963, #978, #979, #980 and #981. Each affected step names its blocker.
- Three things have no working `hmcpctl` command yet and run as HMC CLI commands over SSH:
  applying the new partition's profile after `lpars create` (until #939), writing the REST-added
  adapters into that profile before a profile power-on (until #981), and reading the partition
  description for ownership checks (until #965). The recipe gives only these commands and says
  which issue removes each.
- `adapters add-vscsi` is dropped: `storage map` and `mount-optical-media` create their own
  adapter pairs, and the separately added adapter was left orphaned. No VIOS slot is chosen.
- Boot-order commands are dropped from the path. They are blocked by #980, and the observed
  firmware booted the virtual CD without a boot order because the new disk was blank. Booting
  the installed disk did not run live, and the recipe says so.
- `upload-iso` is marked blocked by #978; the live run imported the ISO through the HMC web
  File API instead.
- The ISO is unmounted after power-off. Unmounting a running partition returned HTTP 500
  HSCL2957 after removing the VIOS side only. `unmount-optical-media` and `detach-mapping` are
  marked blocked by #979.
- Ownership checks read the description with HMC `lssyscfg -F description` while
  `lpars get-description` prints a blank line (#965).
- Constraints seen live are stated where they apply: virtual-disk names of at most 15 characters
  (#964) and media names matching `[A-Za-z0-9_.]`.
- `create-media-repo` is marked blocked by #963. The recipe states what the command does today
  and the size it means (20 GiB), and documents no workaround value as correct: a value that
  is right today would be wrong once #963 converts or renames the option. The command carries no
  `--yes`, so it prompts.
- Cleanup records the observation that `lpars delete` removed the partition and its VIOS server
  adapter.

### Console capture by UUID

`capture_lpar_console_by_selector` (#959) resolves a partition UUID with the system-scoped
`resolve_lpar_uuid` and then the SSH lookup `resolve_lpar_cli_name`.

The SSH UUID-to-name lookups in `ssh/lpar.py` sent `-F UUID,PartitionName` and
`-F UUID,SystemName`. Those are REST element names, and the HMC rejects `UUID` as an invalid
attribute. They now send the HMC CLI attributes `uuid,name`, as `docs/hmc-cli-cheatsheet.md`
records. Both forms ran live on 2026-09-23: `lpars create`'s `mksyscfg` fallback resolved a
system UUID, and `capture-console` resolved a partition UUID.

The fix reaches every caller of the lookups: the SSH selector fallbacks (`ssh/selectors.py`),
VIOS install, the `lpars create` 406 `mksyscfg` fallback and the ownership system-name fallback.
The last two caught the lookup's failure and fell back to the raw selector. Tests pin the exact
lookup commands and assert that no REST element name reaches `-F`.

### Acceptance evidence for the revision

- `tests/app/test_lpar_iso_recipe.py` matches the recipe's `hmcpctl` command set, including the
  removal of `add-vscsi` and the boot-order commands.
- The SSH lookup tests pin `-F uuid,name` and assert that no REST element name reaches `-F`.
- Live confirmation of the revised recipe belongs to the operator's re-run; this revision claims
  no live arm of its own.
