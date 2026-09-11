# LPAR ISO Installation CLI Recipe — Design Specification

**Issue:** #776  
**ADR:** [ADR 0135](../../adr/0135-cli-lpar-install-lifecycle-surfaces.md)  
**Branch:** `feat/lpar-iso-boot-recipe-776`  
**Status:** Accepted

## Scope and outcome

Publish a copy-and-adapt operator recipe that starts with HMC connection configuration and ends with
a booted, inspected LPAR whose installation media is detached and boot order is restored. Add only
the missing optical mapping and bounded console-capture CLI adapters needed to make every step
executable through the installed CLI.

The recipe must satisfy issue #776's eleven lifecycle steps, use stable placeholders for related
resources, identify names versus UUIDs, and explain confirmations, ownership, available dry runs,
partial failure, verification, and optional destructive cleanup. It must be linked from
`docs/cli.md` and `docs/index.md`.

Out of scope are new optical-media or capture semantics, an interactive terminal, MCP contract
changes, dependencies, automatic rollback, and claims of live hardware verification when no suitable
HMC and VIOS are available.

## Public CLI contracts

### Optical media

`hmc-mcp storage mount-optical-media VIOS LPAR MEDIA` accepts:

- `--system/-s SYSTEM`, an optional managed-system name or UUID used for selector disambiguation;
- `--target-device DEVICE`, an optional vtscsi device name;
- `--ownership-override`, passed to the existing LPAR mutation authorization;
- `--yes/-y`, which skips a confirmation that identifies the ISO, LPAR, and VIOS.

It calls `mount_optical_media` once and prints an identified success plus the returned mapping as
JSON. Declining confirmation exits without calling the operation.

`hmc-mcp storage unmount-optical-media VIOS LPAR MEDIA` accepts:

- `--system/-s SYSTEM` and `--ownership-override` with the same meaning;
- `--confirm/-y`, which skips a prompt that identifies the mapping and states that the backing ISO is
  preserved.

It calls `unmount_optical_media` once and prints an identified success. Missing or ambiguous mappings,
ownership rejection, and HMC failures remain existing operation errors. Declining confirmation exits
without mutation.

### Bounded console capture

`hmc-mcp lpars capture-console LPAR SYSTEM` accepts `--duration`, `--max-bytes`,
`--idle-timeout`, and `--json`. Defaults and bounds remain the existing capture contract: 30 seconds,
65,536 bytes, 10 seconds idle, with maximums enforced below the adapter.

A shared async operation resolves system and LPAR selectors to CLI names, then calls the existing
sealed-stdin capture. The MCP wrapper and CLI both use it, preserving UUID resolution, contention,
duration/size/idle stops, mandatory release, and honest `released` reporting.

JSON output is an object with `system`, `partition`, `stop_reason`, `released`, `error`,
`bytes_captured`, and `data_base64`. Human output prints the metadata and captured data as escaped
UTF-8-with-backslash-replacement text. It never writes captured bytes or interpreted ANSI sequences
directly to the terminal. A false `released` value is visibly warned because the vterm may still be
held.

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
adapters, mappings, optical media, and boot order, then continue at the first incomplete step. Cleanup
is opt-in and resource ownership must be checked before deletion.

## Command verification

A focused test reads shell blocks from the recipe. For every line whose executable is `hmc-mcp`, it
resolves the group and command from the Typer/Click tree, validates option names and positional arity
against the leaf command without invoking HMC I/O, and substitutes documented numeric placeholder
values where Click type parsing requires them. This is structural command-contract coverage, not a
snapshot of prose.

Separate focused CLI tests patch the operation boundary and prove both optical commands' argument
forwarding and decline paths, console selector/bound forwarding, base64 JSON shape, safe terminal
escaping, false-release warning, and the new help surfaces.

## Errors and recovery

- Existing CLI runtime handling converts operation exceptions to an actionable stderr error and exit
  status 1.
- An optical unmount that cannot identify exactly one matching mapping performs no POST.
- A held vterm fails distinctly and never releases another session's console.
- Capture cleanup remains mandatory; `released=false` tells the operator to recover deliberately via
  the HMC UI or the documented underlying command reference.
- The recipe never promises rollback. Each completed command is durable and must be inspected before
  retrying or cleaning up.

## Threat model

### Boundary inventory

- **Added:** local CLI selectors and options cross into existing REST and SSH operations.
- **Added:** LPAR-controlled console bytes cross into a local terminal or JSON consumer.
- **Widened:** existing optical mount/unmount and bounded capture become reachable from the installed
  CLI in addition to MCP.

### Actors and trust

The local operator and their selected HMC credentials are trusted to request mutations. HMC and VIOS
responses, LPAR console bytes, profile contents, and copied recipe values are untrusted data. The CLI
does not add a remote listener or multi-tenant boundary.

### Controls

- Existing resolvers constrain system, VIOS, LPAR, and mapping identity; storage operations enforce
  ownership and fail closed on ambiguity.
- Existing document builders and HTTP client paths encode REST values; console SSH commands retain
  existing `shlex.quote` construction and bounded arguments.
- Confirmation gates precede optical mutation; overrides are explicit and named.
- Console stdin remains sealed, all capture dimensions stay bounded, another holder is never evicted,
  and release is independently proven.
- JSON base64-encodes arbitrary bytes. Human output escapes rather than interprets all captured
  control bytes. Errors use the existing escaped CLI error path.

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

