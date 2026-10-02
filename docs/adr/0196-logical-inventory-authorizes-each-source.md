# 0196 — Logical inventory authorizes and reports each source on its own

## Status

Accepted (2026-10-02), issue #1220. Partially supersedes ADR 0189 Decision 2, for
`hmc_inventory` only. ADR 0189 otherwise stands, including Decision 2 for every mutating
logical tool.

## Context

ADR 0189 Decision 2 runs a logical action only when the policy permits every tool it delegates
to. The H1 spec gives `hmc_inventory` the same delegated row as `hmc_plan_lpar`, which includes
`hmc_list_vios` and `hmc_get_vios_storage_detail`. Inventory returns no VIOS data.

The same spec also gives inventory a `sources` field with per-source `ok`, `unavailable` and
`denied` statuses. If any withheld tool refused the whole call, `denied` would be unreachable.

A served policy binds only a tool's name and its effect. Target scope is checked per call
(ADRs 0038 and 0039). Of the delegated tools:

- `hmc_list_systems` and `hmc_capacity_report` have no target selector, so only an
  `all-targets` grant admits them;
- `hmc_list_lpars` and `hmc_list_lpar_ownership` bind one managed system.

## Decision

1. **Inventory delegates only to the tools whose data it returns:** `hmc_list_systems`,
   `hmc_list_lpars`, `hmc_capacity_report` and `hmc_list_lpar_ownership`. The H1 spec's
   "plan; inventory" row becomes two rows, and the plan row is unchanged.
2. **Each source is authorized as its delegated tool, for each system, before that source is
   read.** A denied source is reported as `denied` and returns none of its data. The other
   sources still answer. The `hmc_inventory` grant is still necessary and never sufficient.
3. **Enumerating systems needs `hmc_list_systems` authority.** Without it, inventory reads
   nothing and asks for `systems` selectors. Each selector is authorized as `hmc_list_lpars`
   with the caller's value, before it is resolved. That matches what a direct call with the same
   value would be admitted to.
4. **`hmc_inventory` itself registers as `read`, `target_kind="console"`, not exhaustive**, like
   `hmc_operation_status`. Its own grant is therefore `all-targets`, and the delegated tools'
   grants carry the target bound.

## Consequences

- An operator can give an agent a partial view, for example partitions without capacity. The
  result says which part is withheld, and nothing leaks into it.
- Each delegated decision writes its own ADR 0040 record. An enumeration page can write up to
  33: one for systems, one for capacity, and two for each of up to 16 systems.
- On the enumeration path, partitions are checked by UUID. A targets table that names systems
  only by name therefore denies them there, and the caller passes the names as `systems`.
- Renaming one of the four tools changes inventory's effective authority, so a test pins the
  mapping (ADR 0189 Consequences).

## Considered & rejected

- **All-or-nothing over the spec's six tools.** judgment: fit. It refuses a read for VIOS tools
  whose data the tool never returns, and it makes the spec's `denied` status unreachable. The
  operator chose per-source denial on 2026-10-02.
- **Enumerate every system and silently drop those the policy hides.** judgment: fit. It reads
  the systems feed the policy withholds through `hmc_list_systems`, and it writes a deny record
  for every hidden system. The operator rejected it on 2026-10-02.
- **Give `hmc_inventory` a selector so a targets table can bind it directly.** verified:
  `REQUIRED_TARGET_ARGUMENTS` in `src/hmcpctl/tool_registry.py` (main `74bb5cb8`) maps only
  scalar arguments such as `system_name_or_uuid`. A list argument of up to 16 selectors has no
  selector shape. judgment: it would also duplicate the bound the delegated tools already
  carry.
