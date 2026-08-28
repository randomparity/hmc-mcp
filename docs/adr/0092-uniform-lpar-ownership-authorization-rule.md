# ADR 0092: Uniform ownership authorization rule for LPAR-mutating operations

## Status

Accepted (2026-08-25)

## Context

ADR 0011 established the advisory ownership protocol: a token
(`[hmc-mcp owner:<agent_id> created:<date>]`) stamped into the partition
description, and `authorize_lpar_mutation` (`src/hmc_mcp/operations/lpar/ownership.py:152`)
to reject a mutation of a partition another agent owns. ADR 0011 named the tools
that *stamp* and *read* the token. It never said which mutations must *check* it.

The result is coverage set by whoever wrote the operation. Inside one module,
`delete_lpar` (`operations/lpar/core.py:680`, guard at `:807`), `rename_lpar` (`:904`,
guard at `:920`) and the boot-order operations (guards at `:1422`, `:1476`) call the
guard; `power_lpar` (`:827`) — exported from the same facade — guards only
when the operator opts in (§4). Across
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
Server. #366 moved that determination's subject out of the tool body into
`operations/install.py`, where §3.4a now classifies both install operations; the
premise is stated there at `:232` and still in the tool docstring at
`server_tools/vios.py:240`.

Also out of scope: read operations; managed-system-, user- and cluster-scoped
mutations that name no partition (`create_volume_group`, `create_media_repository`,
`power_system`, `set_sriov_adapter_mode`, `set_pcm_preferences` — which rejects
every category but `ManagedSystem` at `operations/pcm.py:52` — and the user and
cluster tools); and operations that *create* a partition, which have no prior
owner to check and stamp instead (ADR 0011, ADR 0014).

**This section supersedes #369 on `upload_iso`.** #369 lists it as unguarded;
`upload_iso` (`operations/storage.py:445`) names a VIOS, a volume group and a
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

Exhaustive as of `b41e658`, and extended in place by §6 as later PRs add operations —
each such row names the issue that added it. That extension is a **reviewer obligation, not yet
mechanically enforced**: §5's partition test does not exist, and #369 owns it. Until it lands, the
anchor commit is where the tables were last verified exhaustive, not a standing guarantee that a
later export cannot arrive without a row. Guard-call sites are `authorize_lpar_mutation` unless
noted. "Unguarded" is a defect against this ADR, not a standing exemption; standing
exemptions are §3.4 only. The **Tracking** column names the issue that closes each
defect; a row with no issue must read `none yet` rather than be left blank, so the
gap is visible. No row reads `none yet`, and #369 must not close while one does.

The same obligation covers the citations: a PR that moves a definition cited in
§3, §4 or §5 re-verifies that `file:line` in the same change. §Context above is
exempt — it is the survey as it stood at `b41e658`, and its citations are read
against that commit rather than maintained forward.

#### 3.1 Destructive — guard unconditionally

| Operation | Location | Status | Tracking |
|---|---|---|---|
| `delete_lpar` | `operations/lpar/core.py:298` | guarded (`:313`) | — |
| `decommission_lpar` | `operations/decommission.py:606` | guarded (`:283`, `:637`, `:656`, via `authorize_decommission_lpar_ownership_snapshot`) | — |
| `rename_lpar` | `operations/lpar/core.py:410` | guarded (`:426`) | — |
| `set_lpar_ownership_description` | `operations/lpar/ownership.py:264` | guarded (`:281`) | — |
| `synchronize_lpar_profile` | `operations/lpar/configuration.py:29` | guarded (`:25`) | — |

`rename_lpar` is Destructive rather than Reconfiguring because the partition name
is the identity every consumer addresses, and the ownership token itself is keyed
by name — a rename silently detaches both. `set_lpar_ownership_description`
overwrites the token, so it is destructive of the protocol's one artifact.
`hmc_sync_lpar_profile` overwrites a named profile with the running configuration;
the previous profile definition is gone.

`hmc_sync_lpar_profile` delegates to the guarded operation above; the SSH helper is
only the transport boundary.

#### 3.2 Reconfiguring — guard unconditionally

| Operation | Location | Status | Tracking |
|---|---|---|---|
| `set_lpar_boot_order` | `operations/lpar/boot_order.py:63` | guarded (`:104`) | — |
| `clear_lpar_boot_order` | `operations/lpar/boot_order.py:125` | guarded (`:153`) | — |
| `assign_dedicated_pcie_slot` | `operations/pcie.py:183` | guarded (`:223`, via `_authorize_pcie_profile_request`) | — |
| `unassign_dedicated_pcie_slot` | `operations/pcie.py:203` | guarded (`:223`) | — |
| `assign_sriov_logical_port` | `operations/pcie.py:520` | guarded (`:371`, via `_resolve_lpar`) | — |
| `unassign_sriov_logical_port` | `operations/pcie.py:604` | guarded (`:371`) | — |
| `add_vnic` | `operations/ssh_network.py:748` | guarded (`:411`, via `_preflight_add:536` → `_resolve:405`) | — |
| `remove_vnic` | `operations/ssh_network.py:814` | guarded (`:411`, via `_resolve`) | — |
| `set_minimum_affinity_policy` | `operations/ssh_network.py:305` | guarded (`:318`) | — |
| `set_lpar_processors` | `operations/lpar/dlpar.py:418` | guarded (`:405`, via `_apply_dlpar_document:397` → `_resolve_and_authorize_lpar:328`) | — |
| `set_lpar_memory` | `operations/lpar/dlpar.py:454` | guarded (`:405`, via `_apply_dlpar_document`) | — |
| `apply_lpar_pcie_assignments` | `operations/assignments.py:275` | guarded by delegation to the PCIe/SR-IOV/vNIC operations above | — |
| `add_network_adapter` | `operations/adapters.py:33` | guarded (`:45`) | #372 |
| `add_vios_adapter` | `operations/adapters.py:62` | guarded (`:73`) | #372 |
| `delete_adapter` | `operations/adapters.py:84` | guarded (`:94`) | #372 |
| `map_storage` | `operations/storage.py:112` | guarded (`:124`) | #372 |
| `attach_disk_to_lpar` | `operations/provision.py:316` | guarded before the storage workflow (`:348`) | #372 |
| `mount_optical_media` | `operations/storage.py:696` | guarded (`:713`) | #440 |
| `unmount_optical_media` | `operations/storage.py:724` | guarded (`:763`) | #440 |
| `migrate_lpar` | `operations/lpm.py:320` | guarded after optional validation and before migration submission (`:360`) | #373 |
| `migrate_lpar_with_affinity_preflight` | `operations/lpm.py:222` | guarded by delegation to `migrate_lpar` | #373 |
| `abort_lpar_migration` | `operations/lpm.py:380` | guarded (`:392`) | #373 |
| `recover_lpar_migration` | `operations/lpm.py:405` | guarded (`:417`) | #373 |
| `remote_restart_lpar` | `operations/lpm.py:430` | guarded (`:446`) | #373 |

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
`PcieAssignmentUnavailableError` unconditionally at `operations/pcie.py:244`, right
after the guard, so neither can mutate anything at this commit. They count as
correctly-shaped coverage, not as protection of a live mutation.

The remaining direct entry points and their guard state are:

| Entry point | Location | Status | Tracking |
|---|---|---|---|
| `configure_lpar_msp` | `operations/lpar/configuration.py:43` | guarded (`:25`) | — |
| `configure_lpar_processor_compatibility` | `operations/lpar/configuration.py:58` | guarded (`:25`) | — |
| `hmc_modify_lpar` | `server_tools/lpars.py:166` | guarded by `operations/lpar/dlpar.py:35` before any write | #442 |
| `hmc lpar modify` (CLI) | `cli_commands/lpars.py:596` | guarded by `operations/lpar/dlpar.py:35` before any write | #442 |
| `detach_storage_mapping` | `operations/storage.py:171` | resolves the mapping's client LPAR and guards it before deletion (`:204`) | #448 |

`hmc_dlpar_proc` and `hmc_dlpar_mem` were rows in this table at `b41e658`. #365
extracted `set_lpar_processors` and `set_lpar_memory` from those tool bodies and
guarded both, so the pair left Domain B for the Domain A table above — the §6
transition this table exists to drive. The tools still exist and still carry the
same names and behaviour; they delegate. ADR 0094 records how the guard obtains a
managed-system name when the caller omits the optional selector.

`modify_lpar` closes the sharpest gap this ADR identified. The public operation
resolves and authorizes the partition once before its ordered rename, resource, and
assignment workflow (`operations/lpar/dlpar.py:35`). Both the MCP tool and CLI command
delegate their complete workflow to it, so an adapter cannot accidentally place one
kind of modification on the other side of the authorization boundary.

#### 3.3 Operational — decide explicitly

| Operation | Location | Status | Tracking |
|---|---|---|---|
| `power_lpar` | `operations/lpar/core.py:333` | guarded when opted in (`:372`, via `_resolve_and_authorize_lpar`); §4 | #371 |

`power_lpar` is the whole class. Both `hmc_power_on_lpar` (`server_tools/lpars.py:504`)
and `hmc_power_off_lpar` (`server_tools/lpars.py:615`) delegate to it, and so does the
CLI, so one decision covers every entry path.

#### 3.4 Standing exemptions

Operations registered `mutate` or `destructive` that ship without a guard call.
Together with §3.1–§3.3 this is the list the #369 enforcement test reads (§5). Split
by why: rows in 3.4a are outside §1's definition, rows in 3.4b are LPAR-mutating and
exempt anyway.

**3.4a — outside §1's definition** (no ownership decision to make)

| Operation | Reason |
|---|---|
| `create_and_stamp_lpar` (`operations/lpar/core.py:189`) | Creates the partition. No prior owner exists to authorize against; it stamps the token instead (ADR 0011). |
| `provision_lpar` (`operations/provision.py:483`) | Composite create-and-stamp. Its post-create legs act on the partition it just created and owns, inside one workflow. |
| `deploy_partition_template` (`operations/templates.py:93`) | Creates the partition and stamps it per ADR 0014. |
| `hmc_capture_lpar_console` (`server_tools/console.py:24`) | Holds a console session and releases it. Changes no partition existence, configuration or run state. |
| `hmc_backup_lpar_profiles` (`server_tools/profiles.py:35`) | Reads every profile and writes an HMC-side backup file; it does not mutate a partition or profile. |
| `hmc_migrate_validate_lpar` (`server_tools/lpm.py:147`) | Calls `validate_lpar_migration`, which submits an LPM validation job and changes nothing. The mutating migration operation has its own guard. |
| `install_lpar_os` (`operations/install.py:198`) | Added by #366. `installios` requires its `-p` partition to be a Virtual I/O Server, which ADR 0011 never stamps, so there is no ownership token to authorize against — the determination §1 already records for the `hmc_install_lpar_os` tool body this operation was extracted from. The operation *can be handed* a `LogicalPartition` selector and does not check the type locally; `installios` refuses a non-VIOS `-p` on the HMC, and because submission is detached that refusal reaches only the install log. That honesty gap is tracked by #460; it does not create an ownership decision, because a refused install mutates nothing. |
| `install_vios` (`operations/install.py:288`) | Added by #366. Same reason. Resolves its target through the `VirtualIOServer` feed, so a name selector cannot name a `LogicalPartition` at all; a UUID selector is passed through unchecked, with the same #460 caveat. |

**3.4b — LPAR-mutating, exempt because the signature cannot express the check**

| Operation | Reason | Tracking |
|---|---|---|
| `hmc_restore_lpar_profiles` (`server_tools/profiles.py:86`) | A per-partition decision is not expressible because the restore rewrites every profile. It therefore requires both a destructive managed-system grant, which produces the authorization audit record, and explicit `system_wide_restore_approved=true` acknowledgement before SSH is opened. | #449 |

**3.4b records a separate administrative authorization contract, not an unguarded
partition mutation.** A profile backup does not reveal which profiles the restore file
will replace, so claiming to authorize a caller-supplied subset would be false. The
managed-system grant establishes the administrator's scope and produces the ordinary
authorization audit record; the explicit acknowledgement prevents an ordinary
single-partition workflow from crossing into the system-wide operation accidentally.

`hmc_install_lpar_os` is absent from §3.1–§3.3 because §1 puts it out of scope:
`installios` requires a Virtual I/O Server partition. #366 proposed extracting a NIM
install operation covering LPARs as well as VIOS, and this paragraph made that
extraction conditional: if the operation can target a `LogicalPartition`, it is
Destructive under §2 and §6 requires it to be classified and guarded in the PR that
introduces it.

**Disposition of #366.** #366 shipped as a layering extraction only — it moved the
tool bodies into `operations.install` unchanged and added no LPAR-capable install
path. `install_lpar_os` can be *handed* a `LogicalPartition` selector, as the tool
always could, but `installios` refuses a non-VIOS `-p`, so no mutation of a
`LogicalPartition` is reachable through it. Both exports are therefore classified in
§3.4a rather than §3.1, and §6's recording obligation is discharged there. The
condition above is closed; it reopens only for an install path that can complete
against a `LogicalPartition`.

**What that closure rests on.** The refusal is ADR 0070's *assumption 5*, which
that ADR lists under "Assumptions and unverified behaviors" — none of which had
live-HMC verification. Confirming or refuting it in the next live-HMC window
therefore reopens this classification, not merely ADR 0070's scope note: if any
release has widened `installios` beyond VIOS-type targets, `install_lpar_os`
becomes Destructive under §2 and moves to §3.1 with a guard. Nothing detects the
widening on its own — submission is detached, so acceptance and refusal both
reach only the HMC-side log — so the pointer in ADR 0070's item 5 is the
detector, and it is deliberate.

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

**The selector stays optional.** `power_lpar` is one of the operations ADR 0063
decided **"Optional, not required"** for, and this flag does not change that. An
earlier revision of #371 made the selector required when the flag was on, because
the ownership token is read per managed system and nothing then derived a
partition's owning system. ADR 0094 (#365) removed that constraint: its
`_resolve_and_authorize_lpar` derives the owning system by the same bounded parent
discovery `find_partition_by_name` already applies to a fleet-ambiguous name — the
100-system cap, the timeout, and the "supply managed-system scope" remedy. #371
calls that shared chain rather than adding a second convention in the same module,
so ADR 0063 is left intact and no caller that omits the selector breaks when the
flag goes on.

Supplying the selector is still worth it: it replaces the fleet walk with one
managed-system read. `hmc_power_on_lpar` / `hmc_power_off_lpar` already accepted
it, and `hmc-mcp lpars power-on` / `power-off` gained `--system` so the remedy the
discovery failure message names is reachable from the CLI too.

That shared chain also closes the gap where a partition UUID paired with a
mismatched selector would read the token off a system the partition does not live
on: `_verify_partition_on_system` rejects the pairing, and the discovery branch
matches by UUID. #462 still owns the same gap on the guarded operations that take a
required selector and do not route through this chain.

**The cost, stated.** Guarding `power_lpar` costs **one SSH login plus two REST
GETs** on every call that does not carry `ownership_override=True`.

The SSH login is the chain `authorize_lpar_mutation` (`operations/lpar/ownership.py:152`) →
`ssh_commands.get_lpar_description` (`ssh_commands.py:1577`) →
`ssh.run_hmc_command` (`ssh.py:34`) → a fresh `asyncssh.connect` (`ssh.py:38`) per
invocation. `run_hmc_command` opens and closes its connection inside the call; the
only long-lived SSH connection in the package is the console path (`ssh.py:80`),
which commands do not share. There is no pool and no reuse. (With
`ownership_override=True` the guard returns at `operations/lpar/ownership.py:161` after
auditing, before the read — so **`authorize_lpar_mutation` itself** pays nothing.
A caller that resolves the ownership names first still pays the two REST GETs
below, because the audit record for an approved override names the system and the
partition. #371 corrected an earlier reading of this parenthetical that took the
override path to be free end to end; ADR 0094's `_resolve_and_authorize_lpar`
narrows it further — it skips the fleet walk on an override and pays one name read.)

The two REST GETs come from `resolve_lpar_ownership_names`
(`operations/lpar/ownership.py:169`), which the guard needs to turn UUIDs into the CLI names
the SSH command takes. It calls `_system_name` (`:581`) → `hmc.get_managed_system`
(`:591`) and `hmc.get_logical_partition` (`:582`) **unconditionally** — supplying
`system_name_or_uuid` does not avoid either, as `rename_lpar` (`:917`) and
`_authorize_pcie_profile_request` (`operations/pcie.py:218`) already demonstrate.

The two REST reads are the same order of work `power_lpar` already does
(`resolve_lpar_uuid` at `:904`, and a `get_quick_property` state check on power-on).
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
  (`operations/lpar/ownership.py:75`). This is an operator-approved exception to a *single*
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
`authorize_lpar_mutation` (`operations/lpar/ownership.py:152`) and
`authorize_decommission_lpar_ownership_snapshot` (`operations/lpar/ownership.py:211`). Nothing
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
- `modify_lpar` guards its complete workflow (§3.2), so resource-only calls now pay
  the ownership check's SSH and REST cost. It is the one Reconfiguring operation a
  caller might invoke in a loop.
- `provision_lpar` calls `power_lpar` for its activation leg
  (`operations/provision.py:287`). With the setting on, that leg would authorize a
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
