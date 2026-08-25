# ADR 0094: Deriving the Managed System an Ownership Guard Needs

## Status

Accepted (2026-08-25)

## Context

Issue #365 extracted the DLPAR processor and memory workflows out of the
`hmc_dlpar_proc` / `hmc_dlpar_mem` tool bodies into
`operations_lpar.set_lpar_processors` and `operations_lpar.set_lpar_memory`, so
a consumer already on an event loop can call them at all (the tool bodies ran
`asyncio.run`, which raises inside a running loop). ADR 0092 §3.2 classifies both
as **Reconfiguring** and requires them to be guarded unconditionally, so each new
operation must call `authorize_lpar_mutation` before it writes.

That guard reads the ADR 0011 ownership token through
`ssh_commands.get_lpar_description`, which runs `lssyscfg -m <system> -r lpar
--filter lpar_names=<lpar>`. It is keyed by **CLI managed-system name plus
partition name**, not by partition UUID. Every operation that guards today —
`delete_lpar`, `rename_lpar`, `set_lpar_boot_order`, the PCIe and SR-IOV
operations — takes a **required** managed-system selector and feeds it through
`resolve_system_uuid` → `resolve_lpar_ownership_names` to produce that pair.

The two DLPAR entry points do not. ADR 0063 gave them
`system_name_or_uuid: str | None = None` and recorded "optional, not required" as
a deliberate decision: required parameters would break positional ADR 0029
callers, and ADR 0039 already denies an omitted optional selector under any
`targets` table, so an operator who wants the selector enforced gets it from the
policy rather than the signature. Issue #365 additionally requires that the tool
contract not break for MCP clients.

So the guard needs a system, the caller may legitimately not supply one, and
neither constraint can be dropped. The operation has to *derive* the owning
managed system in that case.

## Decision

**An LPAR-mutating operation whose managed-system selector is optional derives
the owning system by bounded parent discovery when the caller omits it.**

`operations_lpar._resolve_and_authorize_lpar` is the guarded resolve chain for
those operations. With a selector it is the chain `rename_lpar` already uses.
Without one it resolves the partition UUID fleet-wide, then calls
`_discover_owning_system_uuid`, which walks `list_managed_systems()` and each
system's `list_logical_partitions(<system_uuid>)` feed until it finds the system
whose partition list contains that UUID. From there the existing
`resolve_lpar_ownership_names` → `authorize_lpar_mutation` chain is unchanged.

The walk reuses the bounds `HMCClient.find_partition_by_name` already applies to
a fleet-ambiguous partition name — `bounded_parent_systems`' 100-system fan-out
cap and `PARENT_DISCOVERY_TIMEOUT_SECONDS` — and fails with the same operator
remedy that path names, *supply managed-system scope*. A caller who does not want
to pay for discovery, or whose fleet is too large for it, passes the selector.

Containment is established by UUID equality against the system's own partition
feed, so a partition name that collides across systems cannot cause the guard to
read the token off the wrong partition.

The cost lands only on the omitted-selector path: 1 + N REST reads, N bounded at
100. A caller that supplies the selector pays nothing new, and any caller under a
`targets` table is already forced to supply it by ADR 0039.

## Consequences

`set_lpar_processors` and `set_lpar_memory` are guarded on every entry path — MCP
tool, `hmc_mcp.api`, and any future CLI command — while keeping the ADR 0063
signature. ADR 0092's Domain B loses its two DLPAR rows.

Both MCP tools gain `ownership_override: bool = False`, appended after the
existing parameters. ADR 0092 §5 requires every guarded operation to accept the
per-call operator override, and a tool that guards without exposing it would
leave an MCP client with a rejection and no approved way through — the position
every other guarded LPAR tool already avoids. Appending keeps every existing call
valid, positional callers included, so the tool contract is extended rather than
changed. Issue #365's "parameter lists are unchanged" is read as its stated
intent, *this must not be a breaking change for MCP clients*, which appending
satisfies.

A DLPAR call that omits the selector now makes REST reads proportional to fleet
size before it writes. On a large fleet that is visible latency, and the remedy
is the selector. A partition whose owning system is not in
`list_managed_systems()` — an unreachable or unmanaged frame — fails with a named
error rather than mutating unguarded.

This rule is stated generally because ADR 0092 tracks three more entry points
with the same shape (#441's `hmc_set_lpar_msp` and `hmc_set_lpar_proc_compat`,
#442's `hmc_modify_lpar`). Each will need a managed-system name for its guard and
each carries an optional selector; they should reuse
`_resolve_and_authorize_lpar` rather than invent a second derivation.

## Considered & rejected

**Make the selector required on the operation, and on the tool.** The simplest
code, and it is what every other guarded operation does. Rejected: ADR 0063
decided the opposite for these exact tools with reasons that still hold, and
issue #365 forbids breaking the tool contract.

**Make the selector required on the operation but keep the tool's optional and
raise when it is omitted.** The parameter list would be unchanged while the
behaviour was not: a fleet-wide call that works today would start failing. That
is a breaking change wearing a compatible signature, which is worse than an
honest one.

**Read the owning system from an `AssociatedManagedSystem` link on the
LogicalPartition resource.** One GET, and the operation already fetches the
partition inside `resolve_lpar_ownership_names`, so it would cost nothing extra.
Rejected on evidence: this repo has only ever observed that link on a
`VirtualIOServer` (`client_storage.py:70`), never on a `LogicalPartition`, and
there is no live-HMC survey for the latter the way #374 surveyed `Description`.
Betting the operation's correctness on an unobserved schema element would make it
fail on firmware where it works today, with no way to test the claim from this
repository. Bounded discovery uses only endpoints already exercised end to end.
If a survey later confirms the link across the supported firmware generations,
replacing the walk with it is a contained change behind
`_discover_owning_system_uuid` and should be recorded as its own decision.

**Move the guard's read from SSH to the REST `Description` element.** ADR 0071
established that the description is readable over REST and fully inlined in the
per-system list feed, which would make discovery and authorization one call.
Rejected as out of scope: ADR 0071 deliberately left the single-LPAR
authorization path on SSH and recorded that move as a separate decision, and ADR
0092 §4 costs the guard on that basis. Changing the guard's transport affects
every guarded operation, not these two.
