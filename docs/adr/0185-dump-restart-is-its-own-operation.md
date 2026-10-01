# 0185 — The dumprestart crash is its own operation, served by its own tool

## Status

Accepted (2026-10-01). Reverses the "own MCP tool" rejection in ADR 0164. ADR 0164's opt-in
(`allow_dump_restart`) stands, and so does everything else that record decided.

## Context

ADR 0164 made `operation=dumprestart` reachable through `hmc_power_off_lpar`. Since then,
any grant that admits that tool also admits crashing the partition and taking a platform dump
(#896). `allow_dump_restart` does not close the gap. The caller supplies it in the same call, so
it confirms intent but does not limit authority.

A served access policy can bind to only two things: a tool's name and its effect class.
`_resolve_tools` (`authorization/access_policy.py`) admits a tool when the grant either names it
in `tools` or lists its `effect` in `effects`. Nothing reads `ToolSecurity.operation`. The
registry pairs every operation id with exactly one tool, and `tool_module` refuses a duplicate
(`tool_registry.py`, "duplicate operation"). That leaves the tool as the only unit a policy can
grant or withhold.

The operator decided on 2026-10-01 that the crash gets an operation id distinct from
`lpar.power_off`. A policy can then grant one and withhold the other, and existing
`lpar.power_off` grants stop covering the dump. This record decides how.

## Decision

1. **A new MCP tool, `hmc_dump_restart_lpar`, with operation `lpar.dump_restart`**, effect
   `destructive`, target kind `lpar`. Its selectors are `lpar_name_or_uuid` and
   `system_name_or_uuid`, both bounded by a `targets` table, as `hmc_power_off_lpar`'s are. It
   calls `power_lpar` with `power_on=False`, `operation="dumprestart"`, `immediate=False` and
   `restart=False`. That is the document the live arm submitted for this step.
2. **`hmc_power_off_lpar` no longer reaches the crash.** Its `operation` admits `shutdown` and
   `osshutdown` only, and it drops `allow_dump_restart`. Without that flag, `power_lpar` refuses
   `dumprestart` even from a direct Python call that bypasses the served schema. A grant that
   admits `hmc_power_off_lpar` is therefore narrower than it was. That answers ADR 0164's
   objection that a split leaves such a grant unchanged.
3. **`allow_dump_restart` stays as the new tool's confirmation gate.** It defaults to `False`,
   and the call is refused without it, exactly as before.
4. **No policy-model change.** Grants, effect classes, `hmc_effective_permissions` and
   `ToolSecurity` are unchanged. `effects = ["destructive"]` admits both tools, as it admits
   every destructive tool. To withhold the crash, name the tools.

## Consequences

- **Authorization-contract change.** An existing grant that names `hmc_power_off_lpar` no
  longer permits `dumprestart`. Restoring it means adding `hmc_dump_restart_lpar` to the grant.
  An `effects = ["destructive"]` grant keeps the crash. The CHANGELOG records this.
- The served `authorization` record (ADR 0040) now names the crash by its tool. ADR 0180's
  `lpar-power-off` record is unchanged: the new tool reaches `power_lpar`, which writes it.
- The CLI (`hmcpctl lpars power-off --operation dumprestart --allow-dump-restart`) and the
  Python API (`power_lpar`) are unchanged. No access policy governs either; their caller holds
  the HMC credential.
- The MCP crash no longer carries `immediate` or `restart`. The live arm sent both as `false`,
  and no recorded use sends anything else. Adding them later is an additive parameter.
- `lpar.power_off` loses its `dump-restart` implemented-scope variant in
  `docs/capabilities/maturity.json`. `lpar.dump_restart` enters with that variant and no live
  evidence, so it reads `unevidenced` until the bare-CEC arm's opt-in dump step runs through
  the new tool.
- The tool count rises by one.

## Considered & rejected

- **Make the operation id depend on the call's `operation` argument.** verified:
  `_resolve_tools` and `_validate_grant_targets` in `authorization/access_policy.py` (main
  `7e0476d0`) read `security.effect`, `security.targets` and grant `tools`, never
  `security.operation`. judgment: a per-call id would bind nothing until grants gained an
  operations key. That is a policy-model change larger than the gap it closes.
- **A parameter-value dimension on `ToolSecurity`.** judgment: cost. The issue names it the
  most expensive shape, and the approved exclusions leave it unowned.
- **Add the tool but keep `dumprestart` on `hmc_power_off_lpar`.** judgment: fit. That is
  ADR 0164's own objection: the old grant would still reach the crash.
- **Record that the crash rides with `lpar.power_off`.** judgment: fit. The operator chose a
  distinct operation id.
- **Drop `allow_dump_restart` from the new tool, since its name already says "crash".**
  judgment: fit. The flag guards against an accidental call by a client already granted the
  tool, and its semantics are an approved exclusion.
- **Remove `dumprestart` from the CLI and `power_lpar` too.** judgment: fit. Those surfaces
  answer to the credential holder, not to an access policy, and kdive's contract relies on them.
