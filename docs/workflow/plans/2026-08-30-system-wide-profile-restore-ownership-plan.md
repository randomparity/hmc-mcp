# System-wide profile restore ownership implementation plan

Issue: [#449](https://github.com/randomparity/hmc-mcp/issues/449)

## Scope checkpoint

Implement the reviewed system-wide restore guard from ADR 0092 and the design specification. The
human-authorized expansion is limited to the truthful `lpar-profile-restore` audit operation,
`docs/authorization-audit.md`, and their contract tests. The restore command syntax remains out of
scope.

## Implementation

1. Add failing operation tests for current-partition authorization, malformed inventory, opaque
   backup refusal, and audited override behavior.
2. Add `_authorize_system_lpar_profile_restore` and `restore_system_lpar_profiles` in the LPAR
   operations layer, reusing the bulk ownership feed and existing description guard.
3. Add failing tool tests for pre-SSH denial and override delegation, then route
   `hmc_restore_lpar_profiles` through the guarded operation with `ownership_override=False`.
4. Add `lpar-profile-restore` to `OwnershipOperation`; update audit vocabulary tests and
   `docs/authorization-audit.md` so the contract remains exact.

## Verification

Run the focused ownership, profile, audit-document, and ADR citation tests; verify new tests bite
with a controlled production fault; then run `just verify` and
`UV_NO_SYNC=1 uv run --no-sync prek run --all-files`.
