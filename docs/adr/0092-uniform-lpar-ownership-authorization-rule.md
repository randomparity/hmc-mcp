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
`delete_lpar` (`operations_lpar.py:728`, guard at `:743`), `rename_lpar` (`:886`,
guard at `:902`) and the boot-order operations (guards at `:1006`, `:1060`) call the
guard; `power_lpar` (`:790`) — exported from the same facade — does not. Across
modules the split is wider still: the PCIe, SR-IOV, vNIC and minimum-affinity
operations guard; the adapter, storage, provisioning, DLPAR and LPM operations do
not. Nothing records why, so a maintainer adding a new mutating operation has no
rule to consult.

This matters most for a library consumer. #218's server access policy — now
implemented — governs MCP tool dispatch, and its non-goals exclude the CLI and the
supported Python API by design. For a consumer of `hmc_mcp.api`, ADR 0011
ownership is therefore the *only* authorization boundary that applies — and §3
below shows it reaching well under half the LPAR-mutating operations that exist,
with two of the guarded ones currently unable to mutate anything at all.

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

1. it names an **existing** partition whose resource type is `LogicalPartition`,
   and
2. it changes that partition's existence, identity, configuration, resource
   shape, virtual-device attachments, placement, or run state.

The guard belongs in the **operations layer**, not in a tool body or CLI command
(ADR 0013), so that every entry path — MCP tool, `hmc` CLI, and `hmc_mcp.api` —
crosses the same check.

The test in clause 1 is the **resource type**, not the mere presence of a
selector. `VirtualIOServer` partitions are out of scope: ADR 0011 never stamps
them, so there is no token to authorize against. That excludes the VIOS mutations —
`power_vios`, `hmc_delete_vios`, `hmc_install_vios`, `hmc_restore_vios`,
`hmc_backup_vios`, `hmc_vios_update` — and also `hmc_install_lpar_os`, despite its
name, because `installios` requires its `-p` partition to be of type Virtual I/O
Server (`server_vios.py:267`).

Also out of scope: read operations; managed-system-, user- and cluster-scoped
mutations that name no partition (`create_volume_group`, `create_media_repository`,
`power_system`, `set_sriov_adapter_mode`, `set_pcm_preferences` — which rejects
every category but `ManagedSystem` at `operations_pcm.py:52` — and the user and
cluster tools); and operations that *create* a partition, which have no prior
owner to check and stamp instead (ADR 0011, ADR 0014).

**This section supersedes #369 on `upload_iso`.** #369 lists it as unguarded;
`upload_iso` (`operations_storage.py:445`) names a VIOS, a volume group and a
media name, and no partition at all. It is out of scope here, and that is a
decision, not an omission.

### 2. The three classes and their guard rules

**Destructive** — irreversible, or destroys state a consumer cannot reconstruct
from what remains.
→ **Guard unconditionally.** No configuration switch, no opt-out. The only bypass
is the per-call operator override in §5.

**Reconfiguring** — changes the partition's resource shape, device attachments or
placement. Reversible in principle, but reverting requires knowing the prior state,
which the caller may not have recorded.
→ **Guard unconditionally.** These are low-frequency operations; the guard's cost
(§4) is noise against the cost of the mutation itself.

**Operational** — changes run state only. Reversible by the inverse call with no
knowledge of prior configuration, and the highest-frequency calls a non-interactive
orchestrator makes.
→ **Decide explicitly per operation, with the cost stated.** §4 records the one
decision in this class.

### 3. Classification

Exhaustive as of `b41e658`. Guard-call sites are `authorize_lpar_mutation` unless
noted. "Unguarded" is a defect against this ADR, not a standing exemption; standing
exemptions are §3.4 only. The **Tracking** column names the issue that closes each
defect; a row with no issue must read `none yet` rather than be left blank, so the
gap is visible. No row reads `none yet` at this commit, and #369 must not close
while one does.

#### 3.1 Destructive — guard unconditionally

| Operation | Location | Status | Tracking |
|---|---|---|---|
| `delete_lpar` | `operations_lpar.py:728` | guarded (`:743`) | — |
| `decommission_lpar` | `operations_decommission.py:610` | guarded (`:287`, `:641`, `:660`, via `authorize_decommission_lpar_ownership_snapshot`) | — |
| `rename_lpar` | `operations_lpar.py:886` | guarded (`:902`) | — |
| `set_lpar_ownership_description` | `operations_lpar.py:693` | guarded (`:719`) | — |
| `hmc_sync_lpar_profile` | `server_profiles.py:121` | **unguarded** | #441 |

`rename_lpar` is Destructive rather than Reconfiguring because the partition name
is the identity every consumer addresses, and the ownership token itself is keyed
by name — a rename silently detaches both. `set_lpar_ownership_description`
overwrites the token, so it is destructive of the protocol's one artifact.
`hmc_sync_lpar_profile` overwrites a named profile with the running configuration;
the previous profile definition is gone.

`hmc_sync_lpar_profile` is a tool function whose work happens in
`ssh_commands.sync_lpar_profile` (`ssh_commands.py:1868`) — HMC-CLI transport under
ADR 0013, not an operation. Guarding it therefore means introducing an operation
first, exactly as for the tool rows in §3.2.

#### 3.2 Reconfiguring — guard unconditionally

| Operation | Location | Status | Tracking |
|---|---|---|---|
| `set_lpar_boot_order` | `operations_lpar.py:961` | guarded (`:1006`) | — |
| `clear_lpar_boot_order` | `operations_lpar.py:1028` | guarded (`:1060`) | — |
| `assign_dedicated_pcie_slot` | `operations_pcie.py:160` | guarded (`:220`, via `_authorize_pcie_profile_request`) | — |
| `unassign_dedicated_pcie_slot` | `operations_pcie.py:180` | guarded (`:220`) | — |
| `assign_sriov_logical_port` | `operations_pcie.py:315` | guarded (`:311`, via `_resolve_lpar`) | — |
| `unassign_sriov_logical_port` | `operations_pcie.py:472` | guarded (`:311`) | — |
| `add_vnic` | `operations_ssh_network.py:614` | guarded (`:409`, via `_preflight_add:496` → `_resolve:403`) | — |
| `remove_vnic` | `operations_ssh_network.py:737` | guarded (`:409`, via `_resolve`) | — |
| `set_minimum_affinity_policy` | `operations_ssh_network.py:280` | guarded (`:293`) | — |
| `apply_lpar_pcie_assignments` | `operations_assignments.py:272` | guarded by delegation to the PCIe/SR-IOV/vNIC operations above | — |
| `add_network_adapter` | `operations_adapters.py:32` | **unguarded** | #372 |
| `add_vios_adapter` | `operations_adapters.py:51` | **unguarded** | #372 |
| `delete_adapter` | `operations_adapters.py:69` | **unguarded** | #372 |
| `map_storage` | `operations_storage.py:105` | **unguarded** | #372 |
| `attach_disk_to_lpar` | `operations_provision.py:323` | **unguarded** | #372 |
| `mount_optical_media` | `operations_storage.py:641` | **unguarded** | #440 |
| `unmount_optical_media` | `operations_storage.py:661` | **unguarded** | #440 |
| `migrate_lpar` | `operations_lpm.py:268` | **unguarded**; the guard belongs on the `validate=False` branch (see below) | #373 |
| `migrate_lpar_with_affinity_preflight` | `operations_lpm.py:219` | **unguarded**; delegates to `migrate_lpar` unconditionally (`:236`), so #373's guard covers it | #373 |
| `abort_lpar_migration` | `operations_lpm.py:320` | **unguarded** | #373 |
| `recover_lpar_migration` | `operations_lpm.py:341` | **unguarded** | #373 |
| `remote_restart_lpar` | `operations_lpm.py:362` | **unguarded** | #373 |

`mount_optical_media` and `unmount_optical_media` became facade exports in #363,
so they are Domain A callables (§5) as well as MCP tools — the guard is the only
authorization a `hmc_mcp.api` caller of either would cross. `detach_optical_mapping`
is absent from this table because #362 removed it: it was a duplicate alias of
`unmount_optical_media`, and one operation is now one row.

`migrate_lpar` is one function with a `validate: bool = False` keyword. Only the
`validate=False` branch migrates; `validate=True` submits an LPM validation job that
changes nothing. #373 guards the migrating branch. The validating branch is not
separately exempt — the function is classified here, once, as Reconfiguring.

`assign_dedicated_pcie_slot` / `unassign_dedicated_pcie_slot` are guarded but
currently inert: `_authorize_pcie_profile_request` raises
`PcieAssignmentUnavailableError` unconditionally at `operations_pcie.py:226`, right
after the guard, so neither can mutate anything at this commit. They count as
correctly-shaped coverage, not as protection of a live mutation.

These entry points mutate a partition directly with no operations-layer function, so
they are classified here and must gain both an operation and its guard (§6):

| Entry point | Location | Status | Tracking |
|---|---|---|---|
| `hmc_dlpar_proc` | `server_lpars.py:307` | **unguarded** | #365 |
| `hmc_dlpar_mem` | `server_lpars.py:349` | **unguarded** | #365 |
| `hmc_set_lpar_msp` | `server_lpar_config.py:368` | **unguarded** | #441 |
| `hmc_set_lpar_proc_compat` | `server_lpar_config.py:417` | **unguarded** | #441 |
| `hmc_modify_lpar` | `server_lpars.py:193` | **partially guarded** — the `assignments` leg delegates to guarded operations, the `resources` leg calls `modify_logical_partition` at `:241` with no ownership check | #442 |
| `hmc lpar modify` (CLI) | `cli_lpars.py:941` | **partially guarded** — same split, unguarded resource write at `cli_lpars.py:1067` | #442 |

`hmc_modify_lpar` is the sharpest illustration of the gap this ADR closes: one tool,
one `ownership_override` argument, and two legs on opposite sides of the line — and
the CLI repeats it, which is why §6 puts the guard in an operation rather than in
either wrapper.

#### 3.3 Operational — decide explicitly

| Operation | Location | Status | Tracking |
|---|---|---|---|
| `power_lpar` | `operations_lpar.py:790` | guarded when opted in (`:851`, via `_authorize_power_lpar` at `:763`); §4 | #371 |

`power_lpar` is the whole class. Both `hmc_power_on_lpar` (`server_lpars.py:500`)
and `hmc_power_off_lpar` (`server_lpars.py:616`) delegate to it, and so does the
CLI, so one decision covers every entry path.

#### 3.4 Standing exemptions

Operations registered `mutate` or `destructive` that ship without a guard call.
Together with §3.1–§3.3 this is the list the #369 enforcement test reads (§5). Split
by why: rows in 3.4a are outside §1's definition, rows in 3.4b are LPAR-mutating and
exempt anyway.

**3.4a — outside §1's definition** (no ownership decision to make)

| Operation | Reason |
|---|---|
| `create_and_stamp_lpar` (`operations_lpar.py:584`) | Creates the partition. No prior owner exists to authorize against; it stamps the token instead (ADR 0011). |
| `provision_lpar` (`operations_provision.py:432`) | Composite create-and-stamp. Its post-create legs act on the partition it just created and owns, inside one workflow. |
| `deploy_partition_template` (`operations_templates.py:88`) | Creates the partition and stamps it per ADR 0014. |
| `capture_lpar_console` (`server_console.py:25`) | Holds a console session and releases it. Changes no partition existence, configuration or run state. |
| `hmc_migrate_validate_lpar` (`server_lpm.py:140`) | Calls `migrate_lpar(validate=True)`, which submits an LPM validation job and changes nothing. Once #373 guards the migrating branch, this tool reaches a guarded function on a branch that never mutates. |

**3.4b — LPAR-mutating, exempt because the signature cannot express the check**

| Operation | Reason | Tracking |
|---|---|---|
| `detach_storage_mapping` (`operations_storage.py:158`) | Keyed by VIOS plus mapping UUID; the owning partition is not a parameter. A guard would need an extra read to resolve the client partition. | #448 |
| `hmc_backup_lpar_profiles` / `hmc_restore_lpar_profiles` (`server_profiles.py:35`, `:86`) | Managed-system-scoped; no partition named, so a per-partition decision is not expressible. Restore rewrites every profile on the system. | #449 |

**3.4b rows are recorded gaps, not safe exemptions.** The mutation is real and the
partition is owned by someone; only the selector is missing. Closing them means
changing the operation signatures, which is a separate decision and a separate PR —
hence the Tracking column here too. §3's `none yet` gate applies to this table
exactly as it does to §3.1–§3.3: these are the widest-blast-radius unguarded
mutations in the inventory, and exempting them from the gate would hide the two rows
the gate most needs to see.

They sit in the exemption register, rather than in §3.2 as ordinary defects, only
because no amount of guard-call work fixes them — the signature has to change first.

`hmc_install_lpar_os` is absent from every table above because §1 puts it out of
scope: `installios` requires a Virtual I/O Server partition. #366 proposes extracting
a NIM install operation covering LPARs as well as VIOS. If that operation can target a
`LogicalPartition`, it is Destructive under §2 and §6 requires it to be classified and
guarded in the PR that introduces it.

### 4. The `power_lpar` decision

**`power_lpar` is guarded only when the operator opts in. The default is off.**

The setting is a new `HMCConfig` field, `authorize_power_operations` — environment
variable `HMC_AUTHORIZE_POWER_OPERATIONS`, TOML profile key
`authorize_power_operations` — defaulting to `false`. It governs `power_lpar` and
nothing else: it is not a switch for the Operational *class*. A second Operational
operation, if one is ever added, gets its own §3.3 row and its own decision under
§2, and may or may not share this flag.

When true, `power_lpar` calls `authorize_lpar_mutation` with the same
`ownership_override` semantics as its siblings in §3.1 and §3.2. When false it does
not, and its docstring carries the ADR 0011 advisory language telling the caller to
read the description first.

**The selector becomes required when the flag is on.** `power_lpar` is the only
guarded LPAR operation whose `system_name_or_uuid` is optional; every sibling in
§3.1 and §3.2 takes it positionally and required. The token is read per managed
system, and `resolve_lpar_ownership_names` needs a system UUID to reach
`get_managed_system`, so with the flag on and no selector the guard cannot
identify which system's token applies. #371 refuses that call with a `ValueError`,
before any HMC traffic, rather than powering a partition whose ownership was never
read. The refusal is unconditional on `ownership_override`: the override waives the
ownership *decision*, not the guard's need to name the partition it is auditing.
The two entry paths that could omit the selector gained one — `hmc-mcp lpars
power-on` and `power-off` take `--system` — and `hmc_power_on_lpar` /
`hmc_power_off_lpar` already accepted it.

This narrows [ADR 0063](0063-source-system-selectors-for-fleet-ambiguous-lpar-tools.md),
which decided **"Optional, not required"** for this very parameter, named
`hmc_power_off_lpar` as its precedent, and rejected a required selector as
something that "breaks positional Python-API callers for no authorization gain
under a table". It does not reopen that decision. The parameter stays optional in
the signature; the requirement is conditional on a setting that is off by default,
so no existing caller breaks, and it is keyword-only, so no positional call site
shifts. And the gain 0063 could not claim now exists: under this flag the selector
is not a `targets`-table disambiguator but the key the ownership token is read
under, so omitting it is not "today's fleet-wide search" — it is an ownership check
that cannot run. The consequence 0063's reasoning implies is real and worth stating:
an operator holding an `all-targets` grant, for whom 0063 preserved fleet-wide
omission, will see power calls that omit the selector refused once this flag is on.

A caller holding only an LPAR UUID
recovers the selector by listing partitions per managed system
(`hmc_list_lpars(system_name_or_uuid=…)`); the fleet-wide ownership feed does not
help, as `list_lpar_ownership` records that its entries do not name their parent
system.

**Deriving the system instead was not ruled out — it was not evidenced.** The
obvious alternative is to read the parent system off the partition, which would
keep the selector optional and remove the caller's unreconciled assertion
altogether. `client_storage.py:68` already does exactly that for a
`VirtualIOServer`, pulling an exact `ManagedSystem` UUID out of an
`AssociatedManagedSystem` href, and `xmlutil.element_to_dict` preserves `href`, so
the machinery exists. What is missing is evidence that a `LogicalPartition`
document carries the same link: no fixture, no captured payload and no survey in
this repo shows one, and #371 declined to put an unverified payload assumption on
an authorization path. #466 tracks the live-REST check — the kind #374 ran for the
description field — that would settle it. If the link is there, the selector goes
back to optional and this paragraph, the `ValueError` and the two CLI flags are
withdrawn.

**The cost, stated.** Guarding `power_lpar` costs **one SSH login plus two REST
GETs** on every call that does not carry `ownership_override=True`.

The SSH login is the chain `authorize_lpar_mutation` (`operations_lpar.py:495`) →
`ssh_commands.get_lpar_description` (`ssh_commands.py:1577`) →
`ssh.run_hmc_command` (`ssh.py:34`) → a fresh `asyncssh.connect` (`ssh.py:38`) per
invocation. `run_hmc_command` opens and closes its connection inside the call; the
only long-lived SSH connection in the package is the console path (`ssh.py:80`),
which commands do not share. There is no pool and no reuse. (With
`ownership_override=True` the guard returns at `operations_lpar.py:505` after
auditing, before the read — so **`authorize_lpar_mutation` itself** pays nothing.
The *call* still pays the two REST GETs below: every caller resolves the ownership
names before invoking the guard, because the audit record for an approved override
names the system and the partition. #371 corrected an earlier reading of this
parenthetical that took the override path to be free end to end.)

The two REST GETs come from `resolve_lpar_ownership_names`
(`operations_lpar.py:510`), which the guard needs to turn UUIDs into the CLI names
the SSH command takes. It calls `_system_name` (`:517`) → `hmc.get_managed_system`
(`:527`) and `hmc.get_logical_partition` (`:518`) **unconditionally** — supplying
`system_name_or_uuid` does not avoid either, as `rename_lpar` (`:899`) and
`_authorize_pcie_profile_request` (`operations_pcie.py:217`) already demonstrate.

The two REST reads are the same order of work `power_lpar` already does
(`resolve_lpar_uuid` at `:847`, and a `get_quick_property` state check on power-on).
**The SSH login is the outlier**, and it is the part of the cost this decision turns
on.

**Why off.** A power-cycling orchestrator is the highest-frequency caller of
`power_lpar` in the package, and it is the caller that would pay this cost on every
call. Defaulting on would make the package materially slower for its main
non-interactive consumer in exchange for protecting the one class that is trivially
reversible by the inverse call, with no prior state to reconstruct. Defaulting off
with an explicit opt-in lets an operator on a shared HMC turn it on and accept the
cost knowingly.

**Reconsider when the SSH login is gone.** The checkable condition is: *guarding
`power_lpar` no longer opens an SSH connection*. Two concrete triggers, either of
which satisfies it:

1. `authorize_lpar_mutation` moves its description read onto the ADR 0071 REST
   path. ADR 0071 established that the description is fully inlined in the bulk
   `LogicalPartition` list feed and explicitly deferred moving the authorization
   read as "a separate decision".
2. The SSH transport gains connection reuse, so `run_hmc_command` no longer opens a
   connection per call.

Either trigger leaves the two REST reads in place. That is deliberate: they are
comparable to work `power_lpar` already performs, so once the SSH login is gone the
argument for defaulting off no longer holds and the default should flip to on. That
revisit needs no new investigation — this paragraph is the finding. Anyone landing
either change should amend this section in the same PR.

### 5. The exemption mechanism

Two distinct mechanisms, deliberately not interchangeable:

- **Per-call operator override.** Every guarded operation takes
  `ownership_override: bool = False`. When true the guard is bypassed for that one
  call and the bypass is audited by `_audit_lpar_ownership_override`
  (`operations_lpar.py:427`). This is an operator-approved exception to a *single*
  mutation. It is not an exemption from this ADR, and an operation that accepts it
  is still classified and still guarded.
- **Standing exemption.** A row in §3.4b with a recorded reason. This is the only
  way an operation that *is* LPAR-mutating may ship without a guard call. (§3.4a
  rows are not LPAR-mutating at all; they are listed so the inventory stays
  complete, and a test may read the two sub-tables as one allowlist.)

#### The predicate, stated so a test can implement it

An LPAR-mutating operation satisfies this ADR if and only if it **reaches a guard**
or **appears in §3.4**. Every part of that — which operations are in scope, what a
guard is, and what "reaches" means — needs definition, or the test cannot be
written.

**Enumeration domain.** Two domains, checked separately because they are reachable by
different means.

- *Domain A — the facade.* Every **function** exported from `hmc_mcp.api.__all__`
  whose definition lives in `src/hmc_mcp/operations_*.py` — the `inspect.isfunction`
  filter `tests/unit/test_public_api.py:309` already applies, not "every callable",
  which would drag in every exported dataclass and error type. This is the domain
  #369's acceptance criterion names, and the one that matters most, because a
  `hmc_mcp.api` consumer crosses no other authorization boundary (§7).
- *Domain B — entry points with no operation.* Every function registered by `@tool`
  with effect `mutate` or `destructive` that meets §1 and whose mutation does not
  route through a Domain A callable. These are §3.1's and §3.2's tool rows plus the
  CLI row. They cannot be fixed by adding a guard call: §6 requires an operation
  first, at which point they become Domain A and leave Domain B. **Domain B being
  empty is the end state**; until then each row needs a Tracking issue.

**Domain A is enumerated, then partitioned — not filtered by judgement.** "Meets §1"
is a semantic call no test can compute, so the test must not try. Instead every
Domain A callable must fall into exactly one of three sets, and their union must
equal the enumeration:

1. classified in §3.1, §3.2 or §3.3;
2. exempt in §3.4;
3. **not LPAR-mutating** — a set the test maintains explicitly, alongside the frozen
   public-API digest this repo already keeps in `tests/unit/test_public_api.py`.

Set 3 is where `capacity_report`, `list_adapters`, `read_lpar_boot_order` and every
other non-mutating export live. Membership is a deliberate, reviewed act: a new
facade export lands in none of the three sets, so the test fails until someone
classifies it. That is what makes the check non-circular — a filter on "meets §1"
could only ever re-check operations §3 already names, and by construction could never
catch the new unclassified operation §6 exists to prevent.

Domain B needs no such partition: `@tool(effect=…, target_kind="lpar")` is
machine-readable and exhaustively enforced by the existing registry test, so the
domain enumerates itself.

**What counts as a guard.** Exactly two callables:
`authorize_lpar_mutation` (`operations_lpar.py:495`) and
`authorize_decommission_lpar_ownership_snapshot` (`operations_lpar.py:477`). Nothing
else, and no new one without amending this list.

**"Reaches" means call-graph reachability, not a direct call.** Six operations in §3
guard through a private helper — `_authorize_pcie_profile_request`, `_resolve_lpar`,
`_resolve`/`_preflight_add`, and `apply_lpar_pcie_assignments`'s delegation to the
PCIe, SR-IOV and vNIC operations. A test that looks for a direct call in the
function body fails all of them and would drive maintainers to inline the guard for
the test's benefit. The check is reachability from the operation to a guard callable
within `src/hmc_mcp/`.

**Two assertions, not one.** The partition and the predicate answer different
questions, and the test needs both:

- **(a) The partition is total.** Every Domain A function is in exactly one of the
  three sets. This catches a new or reclassified export, and it can be green today.
- **(b) Every row whose Status reads *guarded* still reaches a guard.** This catches
  regression on the coverage that exists. Rows whose Status reads **unguarded** are
  expected-unguarded — they are the defects §3 records — so (b) skips them until
  their Tracking issue closes and the row flips, at which point (b) starts holding
  them.

Asserting the predicate against every classified row instead would fail fourteen
Domain A rows at this commit, because a classified-but-unguarded row satisfies (a)
and not the predicate. That is not a contradiction: §3 already calls those rows
defects and tracks each one. Asserting only (a) would let a guarded row silently
lose its guard, since (a) cannot tell `delete_lpar` from `map_storage` — both are
merely *classified*. Only the pair covers both failure modes, and the Status column
is what makes (b) mechanical.

The failure message should name this section and the row that is missing or that
lost its guard.

### 6. Rule for future mutating operations

A change that adds an operation meeting §1's definition must, **in the same PR**:

1. add a row to the §3.1, §3.2 or §3.3 table naming the operation and its class, or
   to §3.4a / §3.4b with a reason; and
2. satisfy that class's guard rule — for Destructive and Reconfiguring, a guard
   call reachable from an **operations-layer** function, not from a tool body or a
   CLI command. A new mutation that lives only in `server_*.py`, `cli_*.py` or
   `ssh_commands.py` enters §5's Domain B and is a defect on arrival.

A PR that adds an LPAR-mutating operation without a classification row is
incomplete. "It is new, so nothing owns it yet" is not a reason: the operation acts
on a partition that may already be owned.

A change that adds any new **facade export** defined in `operations_*.py` — mutating
or not — must also place it in one of §5's three sets. Doing nothing is not an
option the test permits, which is the point: it forces the §1 judgement to be made
once, in review, rather than silently deferred.

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
is free. Ownership second: it must read the partition's current description, and the
name resolution that read needs, so it costs outbound calls by construction (§4).
That asymmetry is why §4 exists at all.

## Consequences

- Every row marked **unguarded** in §3.1–§3.3 is now a recorded defect against an
  accepted ADR rather than an undocumented inconsistency, and every one carries a
  Tracking issue: #371 implements §4, #372 and #373 are #369's existing sub-issues,
  #365 covers DLPAR, and #440, #441, #442, #448 and #449 were filed for the rows —
  §3.4b's included — that no #369 sub-issue reached. #369 must not close while any
  Tracking cell reads `none yet`.
- The #369 enforcement test has a concrete predicate (§5) — enumeration domains,
  guard set, reachability rule, and the three-set partition of the facade — so
  neither existing coverage nor the classification itself can silently regress: a
  new facade export belongs to no set and fails the test until someone classifies
  it. The cost is a maintained not-LPAR-mutating set in the test, which is the
  price of not making the check circular. The test also has to assert Domain B,
  which is not a facade check; the two arms are separate.
- `authorize_power_operations` adds a configuration field to the supported surface
  when #371 lands. That PR owns the CHANGELOG entry and the release-classification
  call under ADR 0029; this ADR changes no code and no signature.
- The setting's name and key are a reversible naming choice. If #371 renames them
  it must amend §4 in the same PR.
- Guarding the §3.2 operations adds one SSH login and two REST GETs per call to
  each (§4). They are low-frequency by construction, and the alternative is a facade
  consumer with no authorization boundary at all on adapter, storage and LPM
  mutations.
- `hmc_modify_lpar`'s two legs currently disagree (§3.2). Closing that means the
  `resources` leg gains a guard, which changes its cost profile — it is the one
  Reconfiguring operation a caller might invoke in a loop.
- `provision_lpar` calls `power_lpar` for its activation leg
  (`operations_provision.py:287`). With the setting on, that leg would authorize a
  partition the same workflow just created and stamped — the check passes but costs
  an SSH login for nothing. #371 owns the case, and the resolution stays inside §5's
  two mechanisms: the internal call passes `ownership_override=True`, which is
  audited, rather than a third call-site-conditional guard.
- The §3.4b rows stay unguarded until someone changes the operation signatures
  (#448, #449). They are visible in the exemption list with their reasons and their
  Tracking issues rather than absent from the inventory, and the 3.4a/3.4b split
  keeps "not a mutation" from reading as "a mutation we decided to allow".
- This ADR touches only `docs/adr/`. ADR 0011 gets no back-reference to 0092 here;
  adding one is a one-line edit that belongs to whichever PR next amends 0011.

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
