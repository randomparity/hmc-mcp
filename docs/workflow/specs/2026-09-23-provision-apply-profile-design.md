# Provision applies the created profile (#999)

Issue #999 · branch `fix/provision-apply-profile-999` · base `main` · follows
[the #939 spec](2026-09-23-apply-created-profile-design.md).

## Problem

`provision_lpar` creates through `create_and_stamp_lpar` with `apply_profile=None`, so on the
SSH (`mksyscfg`) path the profile is never applied and the next leg, the REST network adapter
write, fails with `REST0269` on a partition with no current configuration.

## Scope

Operator decision (2026-09-23): provision always applies on the `mksyscfg` path; no opt-out,
CLI option, MCP parameter or tool-docs change. This supersedes the #939 spec's "provision is
unchanged" line.

- `provision_lpar` passes `apply_profile=True`. #939's mechanism is reused unchanged: only the
  SSH path runs `chsyscfg -o apply`, and the REST path leaves `apply_step` as `None`.
- When `creation.apply_step` is present, `apply_profile` is inserted into the step-name list
  after `create` and the step is appended after the `create` step (also after a `create`
  error for a create with no UUID), so `_failed_provision_result` keeps skipping by position.
- An `error` step stops the workflow: every step after `apply_profile` is `skipped`,
  `workflow_completed` is `false`, and no network call is made. `create_and_stamp_lpar`'s
  unapplied-profile warning passes through.
- Dry-run and REST-path step lists are unchanged. No ownership transition.
- CHANGELOG `Fixed` entry.

### Failure model

1. Actors and deployments: an operator (`lpars provision`) or MCP agent (`hmc_provision_lpar`)
   provisioning on an HMC whose REST create returns 406 (V10R3 lab).
2. Invariants: a created partition is never deleted; a failed apply is an `error` step with
   `workflow_completed: false`; no step after it runs.
3. Accepted: a `mksyscfg`-path dry run does not list `apply_profile` (path unknown before the
   create); the REST path gets no apply (excluded).
4. Covered elsewhere: live `REST0269` confirmation (#879); adapter changes lost on profile
   power-on (#981); harness PASS on failed apply (#997).

## Success

1. SSH path: steps read `create` `ok`, `apply_profile` `ok`, then `network` onward; the apply
   runs before the network call.
2. Apply `error`: every later step is `skipped`, `workflow_completed` is `false`, the network
   route is not called.
3. REST path: no `chsyscfg`, no `apply_profile` step (existing tests unchanged).

## Validation

- Success 1: `focused-test` in `tests/lpar/test_provision_tool.py`, 406 create with patched
  `create_lpar_via_cli` and `apply_lpar_profile_via_cli` recording order; red before the change
  (no apply call, no step). Green: `uv run --no-sync pytest tests/lpar/test_provision_tool.py`.
- Success 2: `focused-test`, same file, apply raising `HMCCLIError`; red before the change
  (network runs). Same green command.
- Success 3: `focused-test`, same file, REST create: apply not awaited, no `apply_profile` step;
  existing REST-path tests stay green.
- No-UUID create with an apply step: `focused-test`, same file, `create`, `apply_profile`, then
  each remaining step `skipped` once. Same green command.
- Accepted: #939's apply-error warning names only "assignment steps" (`core.py`, not edited);
  its rewording is a follow-up candidate returned to the campaign.
