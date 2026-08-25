# ADR 0092: Uniform ownership authorization rule for LPAR-mutating operations

## Status

Accepted (2026-08-25)

## Context

ADR 0011 established the advisory ownership protocol: a token
(`[hmc-mcp owner:<agent_id> created:<date>]`) stamped into the partition
description, and `authorize_lpar_mutation` (`src/hmc_mcp/operations_lpar.py:495`)
to reject a mutation of a partition another agent owns. ADR 0011 named the tools
that *stamp* and *read* the token. It never said which mutations must *check* it.

The result is coverage set by whoever wrote the operation. Inside one module,
`delete_lpar` (`operations_lpar.py:728`, guard at `:743`), `rename_lpar` (`:808`,
guard at `:824`) and the boot-order operations (guards at `:928`, `:982`) call the
guard; `power_lpar` (`:763`) — exported from the same facade — does not. Across
modules the split is wider still: the PCIe, SR-IOV, vNIC and minimum-affinity
operations guard; the adapter, storage, provisioning, DLPAR and LPM operations do
not. Nothing records why, so a maintainer adding a new mutating operation has no
rule to consult.

This matters most for a library consumer. #218's server access policy — now
implemented — governs MCP tool dispatch, and its non-goals exclude the CLI and the
supported Python API by design. For a consumer of `hmc_mcp.api`, ADR 0011
ownership is therefore the *only* authorization boundary that applies, and it
currently covers roughly half the mutating surface.

The guard is not free. `authorize_lpar_mutation` reads the token through
`ssh_commands.get_lpar_description` (`src/hmc_mcp/ssh_commands.py:1577`), which
calls `ssh.run_hmc_command` (`src/hmc_mcp/ssh.py:34`), which opens a **fresh
`asyncssh.connect`** inside the call (`src/hmc_mcp/ssh.py:38`). There is no
connection pool and no reuse: every guarded call pays one full SSH login and
teardown. ADR 0071 added a bulk ownership read over REST but deliberately left the
single-LPAR authorization path on SSH, recording that move as a separate decision.

A uniform "guard everything" rule would therefore charge an SSH login to the
highest-frequency call in the package. A uniform "guard nothing new" rule leaves
the facade consumer unprotected. The rule has to distinguish classes of mutation.

## Decision

### 1. What this ADR governs

An operation is **LPAR-mutating** when both hold:

1. it takes a selector naming an **existing** logical partition, and
2. it changes that partition's existence, identity, configuration, resource
   shape, virtual-device attachments, placement, or run state.

The guard belongs in the **operations layer**, not in a tool body or CLI command
(ADR 0013), so that every entry path — MCP tool, `hmc` CLI, and `hmc_mcp.api` —
crosses the same check.

Out of scope: read operations; VIOS-, managed-system-, user- and cluster-scoped
mutations that take no partition selector (`create_volume_group`, `upload_iso`,
`create_media_repository`, `power_system`, `power_vios`, `set_sriov_adapter_mode`,
the user and cluster tools); and operations that *create* a partition, which have
no prior owner to check and stamp instead (ADR 0011, ADR 0014).

### 2. The three classes and their guard rules

**Destructive** — irreversible, or destroys state a consumer cannot reconstruct
from what remains.
→ **Guard unconditionally.** No configuration switch, no opt-out. The only bypass
is the per-call operator override in §5.

**Reconfiguring** — changes the partition's resource shape, device attachments or
placement. Reversible in principle, but reverting requires knowing the prior state,
which the caller may not have recorded.
→ **Guard unconditionally.** These are low-frequency operations; one SSH login is
noise against the cost of the mutation itself.

**Operational** — changes run state only. Reversible by the inverse call with no
knowledge of prior configuration, and the highest-frequency calls a non-interactive
orchestrator makes.
→ **Decide explicitly per operation, with the cost stated.** §4 records the one
decision in this class.

### 3. Classification

Exhaustive as of `b41e658`. Guard-call sites are `authorize_lpar_mutation` unless
noted. "Unguarded" is a defect against this ADR, closed by the remaining sub-issues
of #369 — not a standing exemption; standing exemptions are §3.4 only.

#### 3.1 Destructive — guard unconditionally

| Operation | Location | Status |
|---|---|---|
| `delete_lpar` | `operations_lpar.py:728` | guarded (`:743`) |
| `decommission_lpar` | `operations_decommission.py:610` | guarded (`:287`, `:641`, `:660`, via `authorize_decommission_lpar_ownership_snapshot`) |
| `rename_lpar` | `operations_lpar.py:808` | guarded (`:824`) |
| `set_lpar_ownership_description` | `operations_lpar.py:693` | guarded (`:719`) |
| `hmc_install_lpar_os` | `server_vios.py:247` | **unguarded** |
| `hmc_sync_lpar_profile` | `server_profiles.py:121` | **unguarded** |

`rename_lpar` is Destructive rather than Reconfiguring because the partition name
is the identity every consumer addresses, and the ownership token itself is keyed
by name — a rename silently detaches both. `set_lpar_ownership_description`
overwrites the token, so it is destructive of the protocol's one artifact.
`hmc_sync_lpar_profile` overwrites a named profile with the running configuration;
the previous profile definition is gone.

#### 3.2 Reconfiguring — guard unconditionally

| Operation | Location | Status |
|---|---|---|
| `set_lpar_boot_order` | `operations_lpar.py:883` | guarded (`:928`) |
| `clear_lpar_boot_order` | `operations_lpar.py:950` | guarded (`:982`) |
| `assign_dedicated_pcie_slot` | `operations_pcie.py:160` | guarded (`:220`, via `_authorize_pcie_profile_request`) |
| `unassign_dedicated_pcie_slot` | `operations_pcie.py:180` | guarded (`:220`) |
| `assign_sriov_logical_port` | `operations_pcie.py:315` | guarded (`:311`, via `_resolve_lpar`) |
| `unassign_sriov_logical_port` | `operations_pcie.py:472` | guarded (`:311`) |
| `add_vnic` | `operations_ssh_network.py:614` | guarded (`:409`, via `_resolve`) |
| `remove_vnic` | `operations_ssh_network.py:737` | guarded (`:409`) |
| `set_minimum_affinity_policy` | `operations_ssh_network.py:280` | guarded (`:293`) |
| `apply_lpar_pcie_assignments` | `operations_assignments.py:272` | guarded by delegation to the PCIe/SR-IOV/vNIC operations above |
| `add_network_adapter` | `operations_adapters.py:32` | **unguarded** |
| `add_vios_adapter` | `operations_adapters.py:51` | **unguarded** |
| `delete_adapter` | `operations_adapters.py:69` | **unguarded** |
| `map_storage` | `operations_storage.py:105` | **unguarded** |
| `mount_optical_media` | `operations_storage.py:641` | **unguarded** |
| `unmount_optical_media` | `operations_storage.py:661` | **unguarded** |
| `detach_optical_mapping` | `operations_storage.py:678` | **unguarded** |
| `attach_disk_to_lpar` | `operations_provision.py:323` | **unguarded** |
| `migrate_lpar` (migrating form) | `operations_lpm.py:268` | **unguarded** |
| `migrate_lpar_with_affinity_preflight` | `operations_lpm.py:219` | **unguarded** |
| `abort_lpar_migration` | `operations_lpm.py:320` | **unguarded** |
| `recover_lpar_migration` | `operations_lpm.py:341` | **unguarded** |
| `remote_restart_lpar` | `operations_lpm.py:362` | **unguarded** |

These MCP tools mutate a partition directly in the tool body with no operations-layer
function, so they are classified here and must gain both an operation and its guard:

| Tool | Location | Status |
|---|---|---|
| `hmc_dlpar_proc` | `server_lpars.py:307` | **unguarded** (operation and guard added by #365) |
| `hmc_dlpar_mem` | `server_lpars.py:349` | **unguarded** (operation and guard added by #365) |
| `hmc_set_lpar_msp` | `server_lpar_config.py:368` | **unguarded** |
| `hmc_set_lpar_proc_compat` | `server_lpar_config.py:417` | **unguarded** |
| `hmc_modify_lpar` | `server_lpars.py:193` | **partially guarded** — the `assignments` leg delegates to guarded operations, the `resources` leg calls `modify_logical_partition` with no ownership check |

`hmc_modify_lpar` is the sharpest illustration of the gap this ADR closes: one tool,
one `ownership_override` argument, and two legs on opposite sides of the line.

#### 3.3 Operational — decide explicitly

| Operation | Location | Status |
|---|---|---|
| `power_lpar` | `operations_lpar.py:763` | **unguarded**; decision in §4 |

`power_lpar` is the whole class. Both `hmc_power_on_lpar` (`server_lpars.py:500`)
and `hmc_power_off_lpar` (`server_lpars.py:611`) delegate to it, and so does the
CLI, so one decision covers every entry path.

#### 3.4 Standing exemptions

These operations take a partition selector and are registered `mutate` or
`destructive`, but are exempt from the guard rule. This table is the exemption list
the #369 enforcement test reads; each row carries its reason.

| Operation | Reason |
|---|---|
| `create_and_stamp_lpar` (`operations_lpar.py:584`) | Creates the partition. No prior owner exists to authorize against; it stamps the token instead (ADR 0011). |
| `provision_lpar` (`operations_provision.py:416`) | Composite create-and-stamp. Its post-create legs act on the partition it just created and owns, inside one workflow. |
| `deploy_partition_template` (`operations_templates.py:88`) | Creates the partition and stamps it per ADR 0014. |
| `capture_lpar_console` (`server_console.py:25`) | Holds a console session and releases it. Changes no partition existence, configuration or run state, so it is not LPAR-mutating under §1. |
| `migrate_lpar(validate=True)` / `hmc_migrate_validate_lpar` (`operations_lpm.py:268`, `server_lpm.py:140`) | Submits an LPM validation job only; the partition is unchanged. The migrating form (`validate=False`) is Reconfiguring and guarded. |
| `detach_storage_mapping` (`operations_storage.py:158`) | Identified by VIOS plus mapping UUID; the owning partition is not a parameter. A guard would require an extra read to resolve the client partition. **Recorded gap, not a safe exemption** — the mapping belongs to some partition. |
| `hmc_backup_lpar_profiles` / `hmc_restore_lpar_profiles` (`server_profiles.py:35`, `:86`) | Managed-system-scoped; no partition selector, so a per-partition decision is not expressible. Restore rewrites every profile on the system. **Recorded gap, not a safe exemption.** |

The last two rows are exempt because the current selector cannot express the check,
not because the check is unnecessary. Closing them means changing the operation's
signature, which is a separate decision and a separate PR.

### 4. The `power_lpar` decision

**`power_lpar` is guarded only when the operator opts in. The default is off.**

The setting is a new `HMCConfig` field, `authorize_power_operations` — environment
variable `HMC_AUTHORIZE_POWER_OPERATIONS`, TOML profile key
`authorize_power_operations` — defaulting to `false`. When true, `power_lpar` calls
`authorize_lpar_mutation` with the same `ownership_override` semantics as its
siblings in §3.1 and §3.2. When false it does not, and its docstring carries the
ADR 0011 advisory language telling the caller to read the description first.

**The cost, stated.** Each guarded call adds one full SSH login and teardown:
`authorize_lpar_mutation` (`operations_lpar.py:495`) →
`ssh_commands.get_lpar_description` (`ssh_commands.py:1577`) →
`ssh.run_hmc_command` (`ssh.py:34`) → a fresh `asyncssh.connect` (`ssh.py:38`) per
invocation. `run_hmc_command` opens and closes its connection inside the call; the
only long-lived SSH connection in the package is the console path (`ssh.py:80`),
which commands do not share. There is no pool and no reuse. `power_lpar`'s system
selector is also optional (`system_name_or_uuid: str | None`), so when the caller
omits it the guard additionally needs the parent system name via
`resolve_lpar_ownership_names` (`operations_lpar.py:510`), which costs a REST
managed-system read on top of the SSH login.

**Why off.** A power-cycling orchestrator is the highest-frequency caller of
`power_lpar` in the package, and it is the caller that would pay this cost on every
call. Defaulting on would make the package materially slower for its main
non-interactive consumer in exchange for protecting the one class that is trivially
reversible by the inverse call, with no prior state to reconstruct. Defaulting off
with an explicit opt-in lets an operator on a shared HMC turn it on and accept the
cost knowingly.

**Reconsider when the per-call SSH cost is gone.** Two concrete triggers, either of
which is sufficient:

1. `authorize_lpar_mutation` moves its description read onto the ADR 0071 REST
   path. ADR 0071 established that the description is fully inlined in the bulk
   `LogicalPartition` list feed and explicitly deferred moving the authorization
   read as "a separate decision".
2. The SSH transport gains connection reuse, so `run_hmc_command` no longer opens a
   connection per call.

When either lands, the cost argument above no longer holds and the default should
flip to on. That revisit needs no new investigation — this paragraph is the
finding. Anyone landing either change should amend this ADR in the same PR.

### 5. The exemption mechanism

Two distinct mechanisms, deliberately not interchangeable:

- **Per-call operator override.** Every guarded operation takes
  `ownership_override: bool = False`. When true the guard is bypassed for that one
  call and the bypass is audited by `_audit_lpar_ownership_override`
  (`operations_lpar.py:427`). This is an operator-approved exception to a *single*
  mutation. It is not an exemption from this ADR, and an operation that accepts it
  is still classified and still guarded.
- **Standing exemption.** A row in §3.4 with a recorded reason. This is the only
  way an LPAR-mutating operation may ship without a guard call.

An operation therefore satisfies this ADR if and only if it either calls a guard or
appears in §3.4. That is the predicate the #369 enforcement test asserts, and its
failure message should name this section.

### 6. Rule for future mutating operations

A change that adds an operation meeting §1's definition must, **in the same PR**:

1. add a row to the §3.1, §3.2 or §3.3 table naming the operation and its class, or
   to §3.4 with a reason; and
2. satisfy that class's guard rule — for Destructive and Reconfiguring, a guard
   call in the operations layer, not in the tool body or CLI command.

A PR that adds an LPAR-mutating operation without a classification row is
incomplete. "It is new, so nothing owns it yet" is not a reason: the operation acts
on a partition that may already be owned.

### 7. Relationship to #218's server access policy

The two boundaries are complementary and neither substitutes for the other. Both
must pass for an MCP call; only this one applies to a CLI or `hmc_mcp.api` call.

**What #218 gives this ADR.** The server access policy is an operator-configured,
startup-immutable allowlist evaluated at MCP dispatch, before any handler opens a
client: an effect-class ceiling, allowed connection profiles, and exact target
selectors. It decides *whether this caller may dispatch this tool against this
connection and target*. It never inspects an ownership token, never resolves who
created a partition, and is static — an operator writes it ahead of time. A policy
granting `lpar.power_off` over the `all-targets` sentinel says nothing about who
owns the partition being powered off. So #218 does not make this rule redundant for
any operation, on any entry path.

**What this ADR gives #218.** This guard is a resource-level check inside the
operation, evaluated on every entry path. It decides *whether this agent may mutate
this particular partition*, using state written at creation time by whichever agent
created it. It never widens what an MCP caller may dispatch: a denial by either
boundary denies the call, and this guard runs after the policy check, so a
policy-denied call never reaches it. #218's non-goals exclude the CLI and the
supported Python API from the policy boundary; that exclusion is deliberate and
correct, and it is precisely why this rule must live in the operations layer.

**Ordering and cost.** Access policy first: it performs no HMC request, so a denial
is free. Ownership second: it performs one description read by definition, which is
why §4 exists at all.

## Consequences

- Every row marked **unguarded** in §3.1 and §3.2 is now a recorded defect against
  an accepted ADR rather than an undocumented inconsistency. The remaining
  sub-issues of #369 close them; #371 implements §4.
- The #369 enforcement test has a concrete predicate (§5) and a concrete list
  (§3.4) to check against, so coverage cannot silently regress.
- `authorize_power_operations` adds a configuration field to the supported surface
  when #371 lands. That PR owns the CHANGELOG entry and the release-classification
  call under ADR 0029; this ADR changes no code and no signature.
- The setting's name and key are a reversible naming choice. If #371 renames them
  it must amend §4 in the same PR.
- Guarding the §3.2 operations adds one SSH login per call to each. They are
  low-frequency by construction, and the alternative is a facade consumer with no
  authorization boundary at all on adapter, storage and LPM mutations.
- `hmc_modify_lpar`'s two legs currently disagree (§3.2). Closing that means the
  `resources` leg gains a guard, which changes its cost profile — it is the one
  Reconfiguring operation a caller might invoke in a loop.
- `provision_lpar` calls `power_lpar` for its activation leg
  (`operations_provision.py:273`). With the setting on, that leg would authorize a
  partition the same workflow just created and stamped — the check passes but costs
  an SSH login for nothing. #371 owns that case; §3.4's exemption for
  `provision_lpar` covers the workflow, so the sensible resolution is to skip the
  guard on the internal call rather than to pay it.
- The two "recorded gap" rows in §3.4 stay unguarded until someone changes the
  operation signatures. They are visible in the exemption list with their reasons
  rather than absent from the inventory.

## Considered & rejected

**Guard every LPAR-mutating operation unconditionally, including `power_lpar`.**
The simplest rule, and it needs no configuration field. Rejected on the cost in §4:
one SSH login on every power call, paid by the package's highest-frequency
non-interactive consumer, to protect the one operation class whose inverse is a
single call with no prior state to recover.

**Leave ADR 0011 purely advisory and guard nothing further.** Rejected: #218's
policy boundary does not reach the CLI or `hmc_mcp.api` by design, so a facade
consumer would have no enforced authorization on any mutation. That is the gap this
epic exists to close.

**Cache the ownership token per `(system, lpar)` for a TTL, then guard everything.**
Removes the cost objection without a configuration field. Rejected: a token can
change out of band — another agent restamps it, an operator edits the description
in the HMC GUI — so a cache turns a stale read into an authorization decision, and
the failure mode is *allowing* a foreign mutation. Reconsider only with an
invalidation story, and prefer the ADR 0071 REST read (§4) which is cheap without
being stale.

**Rely on #218's target allowlist instead of ownership.** Rejected twice over: it
does not apply to the CLI or the supported Python API, and it is operator-static —
it cannot express "the partition another agent created five minutes ago", which is
the entire case ADR 0011 addresses.

**Default the power guard on with an opt-out.** Same cost, and it makes the slow
path the default for the consumer that motivates this ADR. An operator who wants it
sets one flag; the alternative is every other operator paying for a check they did
not ask for.

**Classify by the existing MCP `effect` metadata (`read`/`mutate`/`destructive`)
rather than a new taxonomy.** Attractive because the metadata is already exhaustive
and test-enforced. Rejected: `effect` is a client-facing blast-radius hint, not a
reversibility judgement, and on the cases that matter it points the wrong way —
`hmc_power_off_lpar` is `destructive` while `hmc_dlpar_mem` is `mutate`, the exact
inverse of the guard priority this rule needs. `effect` also stops at the MCP
boundary, and this rule has to hold for the CLI and the facade.
