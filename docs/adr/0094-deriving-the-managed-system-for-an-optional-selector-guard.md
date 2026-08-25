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
`_discover_owning_system`, which walks `list_managed_systems()` and each system's
`list_logical_partitions(<system_uuid>)` feed until it finds the system whose
partition list contains that UUID, returning that system's UUID **and its
`SystemName` from the inventory entry that matched**. From there the existing
`resolve_lpar_ownership_names` → `authorize_lpar_mutation` chain is unchanged.

Returning the name matters: `_system_name`'s degraded path falls back to the
selector string it was handed, so passing a UUID there would end in
`lssyscfg -m <uuid>`, which the HMC CLI cannot satisfy, and the operator would
see an opaque SSH failure instead of a diagnosis. The walk already read the name
from the same feed, so it carries it forward rather than re-deriving it.

The walk applies the bounds `HMCClient.find_partition_by_name` applies to a
fleet-ambiguous partition name — `MAX_PARENT_DISCOVERY_SYSTEMS` and
`PARENT_DISCOVERY_TIMEOUT_SECONDS` — and names the same operator remedy, *supply
managed-system scope*. It does not reuse `bounded_parent_systems` itself, whose
message asserts an ambiguous partition name; nothing is ambiguous on this path,
because the UUID resolved uniquely before the walk began. It also diverges from
the sibling on unusable fleet entries — see the Consequences below, where the
reason for that divergence belongs.

**Both branches establish containment, and neither may skip it.** The guard reads
`lssyscfg -m <system> --filter lpar_names=<partition>`, so if the system it is
told about is not the system the partition lives on, it reads *some other*
partition's token — and on a cross-system name collision that token may approve
the mutation. The omitted-selector branch gets containment from discovery's UUID
match. The selector-supplied branch has none for free: `resolve_lpar_uuid` passes
a canonical UUID straight through without checking it against the selector, and
ADR 0039 actively recommends UUIDs in policy `lpar` allowlists, so "a partition
UUID paired with a managed-system selector" is the *recommended* input shape.
`_verify_partition_on_system` therefore reads the selected system's partition
feed and rejects a UUID that is not in it, before the guard runs. One REST read,
against an operation that already pays an SSH login.

`rename_lpar`, `delete_lpar` and the PCIe/SR-IOV resolve chain share the
unchecked shape. They are not changed here — this ADR governs the two operations
#365 introduces — and the sweep is tracked in #465.

**An approved override skips discovery entirely.** `ownership_override=True`
reads no token (ADR 0092 §5), so it needs none of the resolution the guard alone
requires: the chain resolves the partition UUID exactly as the pre-extraction tool
body did, audits the override, and writes. Without this short-circuit the
operator's exception would be *blocked* by discovery's failure modes — an
oversized fleet, a slow one, an unreachable owning frame — which are precisely
the degraded conditions that provoke an operator into using it, and ADR 0092 §4's
"that path pays nothing" would stop being true.

The audit record for that bypass carries the **resolved partition UUID**, which
is already in hand and is fleet-unique, so the record identifies exactly one
partition however the caller named it — a record for a deliberate bypass of the
ownership control has to be attributable to a specific partition, and the raw
selector string is not. The `system` field carries what the caller supplied, and
is empty when the caller named no system: that is what the operator actually
asserted, and resolving a system name the caller never chose — at the cost of the
discovery this branch exists to skip — would make the record less faithful, not
more.

The cost lands only on the guarded, omitted-selector path: 1 + N REST reads, N
bounded by the cap. A caller that supplies the selector pays nothing new, any
caller under a `targets` table is already forced to supply it by ADR 0039, and an
override pays nothing at all.

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

**The walk crosses frames the caller never named, so it tolerates their
failures.** A frame whose partition feed errors, or whose inventory entry carries
no usable UUID or `SystemName`, is skipped and recorded rather than made fatal:
otherwise one unhealthy frame that happens to sort early in
`list_managed_systems()` takes DLPAR down for every partition in the fleet, and
whether a call works depends on inventory order. Skipping cannot widen what may
be mutated — the walk returns only on a positive UUID match, so a skipped owner
ends in the same raise as an absent one — and the raise names how many frames
went unread, so a degraded fleet reads as degraded instead of as a missing
partition.

**The two DLPAR entry points now require SSH reachability to the HMC.** They were
REST-only; the guard reads the token over SSH (ADR 0092 §4), so an environment
where the HMC user has REST access but not remote command execution — port 22
filtered, or a task role that denies it — loses both tools unless the caller
passes `ownership_override=True`, which reads no token. This is disclosed rather
than mitigated: the guard's transport is ADR 0071's decision, not this one's. The
resulting failure is an unwrapped `HMCCLIError` that names neither the ownership
precheck nor the override, which is the same shape `delete_lpar` and `rename_lpar`
already produce; wrapping it belongs to the shared guard and to every operation
that calls it, so it is tracked in #459 rather than special-cased here.

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
