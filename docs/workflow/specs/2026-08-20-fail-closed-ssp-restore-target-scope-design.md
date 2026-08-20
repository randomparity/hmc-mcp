# Fail-closed SSP restore target scope

## Goal

Make `hmc_restore_vios` unavailable to grants constrained by a `targets` table because an
SSP restore can act on a cluster beyond the selected VIOS. Only an `all-targets` grant may
authorize this destructive tool.

## Scope authority

Issue #282 and its live-HMC verification establish that `rstviosbk` requires an explicit
backup type and that `-t ssp` selects a Shared Storage Pool configuration restore. Accepted
[ADR 0044](../../adr/0044-containment-decides-unbounded-arguments.md) already decides the
result if that condition is established: `hmc_restore_vios` flips to
`exhaustive_targets=False`. Issue #289 continues to own the incorrect command names and
flags; this change does not repair or redesign the restore API.

## Design

Pass `exhaustive_targets=False` explicitly in the existing `hmc_restore_vios` tool
declaration. This reuses the registry and access-policy mechanisms already responsible for
fail-closed target authorization. No new selector, backup-type argument, validation path,
or compatibility mechanism is introduced.

Update both exact-set security guardrails so `hmc_restore_vios` is classified with other
tools whose selectors do not name every resource they can affect. Update ADR 0041's
mechanically checked inventory from 28 to 29 non-exhaustive ordinary tools, from 26 to 27
reachable tools, and its reconciled ADR 0039 population from 27 to 28. Those numeric edits
are a direct dependency: `test_the_recorded_unboundable_count_matches_the_registry` rejects
the metadata change while the accepted record reports the old measured inventory. Replace the obsolete
test that coupled the old exhaustive declaration to catalog-name validation with a test
that pins the two independent facts: the restore is non-exhaustive because SSP scope can
exceed one VIOS, while `backup_name` remains outside `UNBOUNDED_ARGUMENTS` because ADR 0044
still governs identifier containment. Existing access-policy tests prove that a
non-exhaustive tool is denied to target-scoped grants and allowed only through
`all-targets`.

The accepted ADR is not rewritten: its conditional consequence is already the governing
record, and the issue's live-HMC comment is the evidence that satisfies it. This spec
records that connection without mutating an accepted decision record.

## Behavior and failure contract

- A grant naming `hmc_restore_vios` and carrying a `targets` table fails policy compilation
  with the existing guidance to use `all-targets`; no dispatch or authorization audit occurs.
- An effect-derived grant can compile with a `targets` table because it may select both
  exhaustive and non-exhaustive tools. At dispatch, that grant denies `hmc_restore_vios`
  through the existing target-authorization path and records the existing denial audit.
- A grant naming `hmc_restore_vios` with `all-targets` retains the existing authorization
  path.
- Handler parameters, validation, SSH command construction, and runtime errors are
  unchanged.
- `backup_name` remains a bounded identifier name and remains subject to its existing
  catalog-name validation. It does not become a globally unbounded argument.

## Threat model

### Boundary inventory

The existing access-policy boundary accepts an operator-authored grant and decides whether
an MCP caller may dispatch `hmc_restore_vios`. The design adds no boundary and widens none;
it narrows the existing boundary by changing one tool's metadata. The existing handler
boundary still accepts caller-controlled VIOS, backup name, profile, and optional system
identities, but their parsing and command construction are unchanged.

### Actor model

The untrusted actor is an authenticated MCP caller whose authority is limited by the
operator's access policy. The operator and repository-supplied tool metadata are trusted to
define that limit. The HMC is trusted to execute the selected restore semantics reported by
its own command help.

### Controls

`ToolSecurity.exhaustive_targets=False` is the existing control. A named-tool grant using a
targets table is rejected while the policy is compiled; an effect-derived grant is denied
at dispatch before the handler runs. Exact-set metadata tests prevent the declaration from
drifting silently. Existing
backup-name validation and shell quoting continue to control the handler boundary; this
change neither weakens nor duplicates them. Denial uses the existing authorization error
and audit behavior only on the dispatch path, so it discloses no new data.

### Out of scope

Correct command names and flags remain with #289. A separate restore API that refuses SSP,
or a cluster selector that makes SSP narrowly grantable, would change the public contract
and is not required by ADR 0044. Live restore execution is excluded because it can
reconfigure a cluster; command-help evidence is sufficient for this metadata correction.

## Testing

First change the exact expected non-exhaustive set, its selector-bearing subset, and the
focused `hmc_restore_vios` classification test so they fail against the current declaration.
The failure must show the restore is still exhaustive or missing from the non-exhaustive
set. Then add the explicit metadata argument, update ADR 0041's guarded inventory counts,
and rerun the focused metadata and legacy-policy tests. Existing access-policy and target-
authorization tests pin the compile-time and dispatch-time denial paths. Run `just test`,
`just smoke`, and `just verify` before delivery.

## Acceptance criteria

1. `TOOL_SECURITY["hmc_restore_vios"].exhaustive_targets` is false and the source
   declaration is explicit.
2. The exact non-exhaustive-tool guardrail includes `hmc_restore_vios` and explains the
   cluster effect that a VIOS selector cannot bound.
3. The focused guardrail continues to assert that `backup_name` is not a member of
   `UNBOUNDED_ARGUMENTS`, keeping identifier containment separate from effect scope.
4. Existing authorization and repository guardrails pass without changing the restore
   handler's public parameters or command construction.
5. ADR 0041's mechanically checked inventory reports 29 non-exhaustive ordinary tools, 27
   reachable tools, and a reconciled ADR 0039 population of 28.

## Resume facts

- Branch: `feat/fail-closed-ssp-restore-282`
- Base branch: `main`
- Guardrails: `just test`; `just smoke`; `just verify`
- Scope token: `q282-a264e390`
- Current phase: design
- Open findings: none
- Review deferrals: none
