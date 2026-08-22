# ADR 0063: Source-system selectors for the fleet-ambiguous lpar tools

## Status

Accepted (2026-08-21). Amends ADR 0035 in part and ADR 0039 in part: the
public-contract change both records declined for these tools is adopted by
operator decision. Closes the case-1 and case-2 residuals of #259; the case-3
residual stands and is restated here.

## Context

An `lpar_name_or_uuid` names a partition *within* a managed system; partition
names collide across systems. ADR 0035's derived-selector design shipped every
tool whose only partition identity was `lpar_name_or_uuid` without a
managed-system argument, and ADR 0039 adopted kind-local matching knowing that
`lpar = ["db-01"]` therefore reached db-01 on every system the granted
connection reaches — recording it as a residual with a UUID-based operator
remedy rather than widen the public contract. Three groups were named:

1. **Twenty lpar tools with no `system_name_or_uuid`** (`hmc_delete_adapter`,
   `hmc_dlpar_mem`, `hmc_dlpar_proc`, `hmc_modify_lpar` since retired from this
   list by later work, `hmc_power_on_lpar`, `hmc_install_lpar_os`, and their
   siblings — 21 at the time of writing, `hmc_lpar_summary` among them).
2. **The LPM tools bound the destination only.** `hmc_migrate_lpar`,
   `hmc_migrate_validate_lpar`, and `hmc_remote_restart_lpar` declare
   `(lpar, lpar_name_or_uuid)` and `(managed_system,
   target_system_name_or_uuid)`, so a grant naming `{ lpar = ["db-01"],
   managed_system = ["S2"] }` authorized evacuating a partition called db-01
   off *any* source system into S2.
3. **`metric_resource`** is disambiguated by a non-selector `category`
   argument.

The mitigation for cases 1 and 2 was exact but brittle: list UUIDs, which are
fleet-unique, and rewrite the policy whenever a partition is recreated. The
operator has decided to close cases 1 and 2 in code.

## Decision

**Every tool that declares an `lpar` selector declares a `managed_system`
selector.** The parameter is the established disambiguator spelling,
`system_name_or_uuid: str | None = None`, appended to each handler signature
and threaded into resolution — `resolve_lpar_uuid` and
`find_partition_by_name` have carried the keyword since ADR 0015, so the
wiring passes it through rather than inventing a second resolution path. The
three `vios_partition_id` tools (`hmc_add_vscsi_adapter`,
`hmc_add_vfc_adapter`, `hmc_attach_disk_to_lpar`) get the declaration too even
though they stay `exhaustive_targets=False`: the slot number remains an
identity no table can bound (ADR 0044), but the lpar identity they act on is
now declared, extracted, and audited like any other.

**The three LPM signatures gain the source system as the same optional
`system_name_or_uuid`.** It lands beside `target_system_name_or_uuid`, giving
each tool two selectors of kind `managed_system`. Kind-local matching needs no
new machinery: one allowlist entry must match *both* endpoints, which is the
conjunction the "every declared selector must match" rule already expresses.
The role collision ADR 0036 flagged resolves safely under that rule now that
the source is declared at all.

**Optional, not required.** The selector follows the
`hmc_power_off_lpar` / `hmc_modify_lpar` precedent: these are retrofitted
disambiguators on operations that previously searched fleet-wide, and the
supported Python API (ADR 0029) calls these functions positionally. Required
parameters would break those callers while buying nothing authorization-wise —
ADR 0039 denies an omitted optional selector under any table, so an operator
who narrows `targets` already forces callers to supply it. Under
`all-targets`, or outside a policy, omission keeps today's fleet-wide search,
which bounded parent discovery (100-system cap, timeout) already contains.

**Case 3 remains open and owned.** `resource_name_or_uuid` is still meaningful
only together with a `category` argument that is not in
`REQUIRED_TARGET_ARGUMENTS` and cannot appear in a `targets` table. Closing it
needs either an argument-keyed table (rejected in ADR 0039 because epic #218
requirement 3 asks for kinds) or a per-tool category split; neither is decided
here. The residual is recorded rather than silently inherited, exactly as ADR
0039 left it.

## Consequences

The ADR 0039 rule is total over the `lpar` kind instead of partial: on all 48
lpar-selector tools a table-constrained call must supply *and* match a system.
A grant that names one of these tools beside a table without a
`managed_system` key now refuses to load — correct, since the grant could
never authorize the tool — where before it loaded and simply did not bind the
system.

Policy files granting any of these tools under a table need no edit if they
already carried `managed_system`; grants that relied on the residual see calls
denied until the caller supplies the selector. Denial messages may now name
`system_name_or_uuid` where they previously had nothing to name.

The public surface moved twice over: MCP tool schemas gain a parameter, and
the ADR 0029 signature digest moves with it. Both are recorded in the same
change that caused them.

Guardrails: G15 (`test_no_exhaustive_tool_accepts_an_identity_its_selectors_cannot_bound`)
is unchanged and still passes — the new parameters are declared selectors, not
unbounded identities — and a new coverage test fails the build if a future
tool declares `lpar` without `managed_system`.

## Considered & rejected

- **Required selector on the retrofitted tools.** Rejected above: breaks
  positional Python-API callers for no authorization gain under a table.
- **Keying the `targets` table by argument name**, separating
  `system_name_or_uuid` from `target_system_name_or_uuid` and giving
  `metric_resource` its `category`. Still rejected for ADR 0039's reasons —
  epic #218 requirement 3 asks for kinds, and the file would tie to Python
  parameter names. It remains the only shape that could close case 3.
- **Adding the selector only to the destructive subset.** A read against a
  withheld partition is a disclosure (ADR 0039), so read tools needed the
  bound as much as mutations did; splitting by effect class would recreate the
  per-kind narrowing ADR 0039 rejected.
- **Accept and document** (the pre-change state). Superseded by the operator
  decision this record captures.
