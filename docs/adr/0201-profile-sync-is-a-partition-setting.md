# 0201 — Profile synchronization is a partition setting with a mode

## Status

Accepted (2026-10-05), issue #627.

## Context

`hmc_sync_lpar_profile` runs `chsyscfg -r lpar -m <system> -i "name=<lpar>,sync_curr_profile=1"`
and documented it as a one-shot save of the running configuration over the current profile.
The reference defines `sync_curr_profile` as a partition setting: `0` disables
synchronization of the current configuration with the active profile, `1` enables it, and `2`
suspends it until the next activation or apply
(`docs/refs/hmc-commands-p10/commands/chsyscfg.md`, attribute `sync_curr_profile`).

A live capture on V10R3 M1060 (2026-10-05, not-activated test partition) confirmed it: the
command exited 0, every profile record was byte-identical afterwards, and the partition's
`sync_curr_profile` field changed from `0` to `1` and stayed there until set back. That capture
covers a not-activated partition only. On an activated partition, by the reference's definition,
enabling synchronization keeps the active profile in step with the running configuration, so it
can overwrite that profile; no capture covers that case. The tool offered no way to set the
setting back, and the live harness enabled it in ST10 and ST15 on every run. A one-shot save of
the running configuration is `mksyscfg -r prof -o save`, which issue #637 owns.

## Decision

`hmc_sync_lpar_profile` and `operations.lpar.configuration.synchronize_lpar_profile` take
`mode: Literal["enable", "disable", "suspend"] = "enable"`, rendered as `sync_curr_profile`
`1`, `0` and `2`. The default keeps every existing call's command unchanged. The tool and
docstrings describe the setting, and the enable mode keeps a warning that on an activated
partition it can overwrite the active profile. The ownership guard (ADR 0092), the `destructive`
effect and the `lpar` target kind are unchanged, so authorization is unchanged.

## Consequences

- A caller can reverse the setting through hmcpctl, so the live harness can restore its baseline.
- The tool name keeps the word "sync"; its description carries the meaning. Renaming is not
  part of this decision.
- The MCP input schema gains one optional field; the generated tool docs change with it.

## Considered & rejected

- **Correct the documentation only.** judgment: leaves an irreversible setting change with no
  supported way back, which the live round trip itself needs.
- **Leave the code and record a failed observation.** judgment: the defect is small and in the
  verified surface; epic #620 requirement 13 asks verification children to fix such defects.
- **Reimplement the tool as `mksyscfg -r prof -o save`.** judgment: that is the
  save-current-configuration operation issue #637 owns, and it would silently change what
  existing callers get.
