# 0198 — LPAR planning delegates to the reads it makes, all or nothing

## Status

Accepted (2026-10-02), issue #1221. Partially supersedes ADR 0189 Decision 2 for
`hmc_plan_lpar` only, by replacing the H1 spec's plan row. ADR 0189 otherwise stands, including
its all-or-nothing rule, which this record keeps for planning. ADR 0196 is unaffected.

## Context

ADR 0189 Decision 2 runs a logical action only when the policy permits every tool in its row.
The H1 spec's plan row names `hmc_list_systems`, `hmc_list_lpars`, `hmc_capacity_report`,
`hmc_list_vios`, `hmc_get_vios_storage_detail` and `hmc_list_lpar_ownership`.

Planning must check a VLAN and a volume group's free space and disk names. Those come only from
`hmc_list_virtual_networks` and `hmc_list_volume_groups`. `hmc_get_vios_storage_detail` returns
mappings only (`client/client_systems.py`, `get_vios_storage_detail`). Planning creates a
partition and reads no ownership stamp.

## Decision

1. **The plan row is the tools whose data planning reads:**
   - `hmc_list_systems`, only when `placement` enumerates;
   - `hmc_list_lpars`;
   - `hmc_capacity_report`;
   - `hmc_list_virtual_networks`;
   - `hmc_list_vios`;
   - `hmc_list_volume_groups`;
   - `hmc_get_vios_storage_detail`, only when the storage already exists.

   `hmc_list_lpar_ownership` leaves the row.
2. **Permission is all or nothing.** A row tool the policy withholds refuses the call before
   any read, naming the tool. The `hmc_plan_lpar` grant is necessary and never sufficient.
3. **Target scope is per read.** Each read is admitted through `dispatch_authorizer` as its
   tool, for the system or VIOS it reads. A system is spelled as the caller's selector when the
   caller named it and as its UUID when placement enumerated it (ADR 0196's rule); a VIOS is
   spelled as its UUID. A denied target becomes a `denied` blocker naming the tool, and nothing
   is read from it. One denied candidate therefore does not hide the others.

## Consequences

- A policy that withholds volume-group listing cannot plan. The denial says which tool to grant.
- `hmc_capacity_report` is a console tool, and the policy compiler refuses it in a targets
  table. Planning under a targets table therefore needs a second grant of
  `hmc_capacity_report` at `all-targets`, or the call is refused by Decision 2. The table must
  list VIOSes by UUID, because target scope matches spellings literally. A `denied` capacity
  blocker arises only when that grant does not cover the call's connection.
- Renaming any of the seven tools changes planning's authority, so a test pins the row (ADR 0189
  Consequences).
- Each admission writes its own ADR 0040 record. With 16 candidates and several VIOSes each, one
  call can write a few hundred records.

## Considered & rejected

- **Keep the H1 row.** judgment: fit. The VLAN and free-space checks the issue requires would
  read tools the operator never granted, or become `unverified`.
- **Per-source denial, as ADR 0196.** judgment: fit. A plan built from withheld sources has
  nothing to resolve. The operator chose all or nothing on 2026-10-02.
- **Refuse the whole call on a target denial.** judgment: fit. On the placement path, one
  denied system would hide every candidate the caller may use.
