# Apply the created partition profile (#939)

## Problem

On the SSH create path (`mksyscfg`, taken when the REST create returns HTTP 406) the HMC writes
only the partition profile. Until that profile is applied or the partition is activated, the
partition has no current configuration: REST adapter writes fail with `REST0269`, the partition
has no `AssociatedPartitionProfile` link, and the create result shows zero memory and processors.
`lpars create` reports success without saying any of this.

## Scope

Operator decision (2026-09-23, campaign b4e2417ebb): `lpars create` applies the new profile by
default after `mksyscfg`, as a reported step, with a `--no-apply` opt-out.

- `ssh/lpar.py` gains `DEFAULT_PROFILE_NAME = "default_profile"` (now also the default of
  `create_lpar_via_cli`'s `profile_name`) and `apply_lpar_profile_via_cli(config, system_name,
  lpar_name, profile_name=DEFAULT_PROFILE_NAME)`, which runs
  `chsyscfg -r lpar -m <sys> -o apply -p <lpar> -n <profile>` with each value `shlex.quote`d.
  This is the command verified in the #879 window (rc 0, the partition stays not activated).
- `LparCreation` gains `apply_profile: bool | None = None`; `LparCreationResult` gains
  `apply_step: WorkflowStep | None = None`. On the SSH path only, `create_and_stamp_lpar` runs
  the apply after `mksyscfg` and before the LPAR read-back and ownership stamp, and sets
  `apply_step` to `apply_profile` `ok` (result: the profile name), `error` (result: the
  `HMCCLIError` text; the partition is not rolled back), or `skipped` when `apply_profile` is
  `False`. `None` (not requested; `lpars provision`) adds no step and no warning. A `skipped`
  or `error` step adds a warning that the profile is unapplied and the current configuration
  reads as empty; the `error` warning also names the recovery (apply `default_profile` or
  activate with it, then redo skipped assignments). The REST path leaves `apply_step` as `None`
  (excluded; operator owns it).
- `workflows.create_lpar` appends `apply_step` after `create`. An `error` marks every assignment
  step `skipped` and returns `workflow_completed: false`, the same stop rule the assignment steps
  already follow.
- Entry points: CLI `lpars create --no-apply`; MCP `hmc_create_lpar(apply_partition_profile:
  bool = True)` (`profile` already names the connection profile). Both pass
  `apply_profile=`. `lpars provision` passes nothing (`None`) and is unchanged.
- Docs: `docs/cli.md` create example note; `docs/recipes/bare-cec-lpar.md` step 2 paragraph;
  regenerated `docs/tools/` and capability inventory; one CHANGELOG `Changed` entry. #981 is
  named as the separate current-configuration gap.
- No ownership transition: the apply belongs beside `mksyscfg` in `create_and_stamp_lpar`,
  which alone knows the SSH path ran and the CLI system name.

## Failure model

1. Actors and deployments: an operator or MCP agent creating an LPAR on an HMC whose REST
   create returns 406 (V10R3 M1060 lab), via `lpars create` or `hmc_create_lpar`.
2. Invariants: a created partition is never deleted by this change; an apply failure is visible
   as an `error` step with `workflow_completed: false`; no assignment runs after a failed apply.
3. Accepted: the apply runs before assignments, so profile-only assignments made later are not
   in the current configuration until activation (power-on with the profile uses the profile).
   A REST-created partition gets no apply step (excluded path). The `skipped` result on
   `--no-apply` still shows the empty current configuration, disclosed by the warning.
4. Covered elsewhere: live confirmation of `REST0269` clearing and the bare-cec
   `AssociatedPartitionProfile` lookup (#879); REST adapter changes discarded by a profile
   power-on (#981); `lpars provision`'s same gap (follow-up candidate).

Threat model: no new trust boundary. The partition and system names already reach `mksyscfg`;
the added command quotes them with `shlex.quote`, the same control `create_lpar_via_cli` uses.
The profile name is a constant. The opt-out is a boolean.

## Success

1. The SSH create path sends `mksyscfg` then the exact `chsyscfg ... -o apply -p ... -n
   default_profile` command, the steps read `create`, `apply_profile` `ok`, and the returned
   LPAR is the read-back taken after the apply.
2. With the opt-out, no `chsyscfg` is sent, the step is `skipped`, and a warning names the
   unapplied profile.
3. An apply `HMCCLIError` yields an `apply_profile` `error` step, the unapplied-profile warning
   with its recovery clause, skipped assignment steps, and `workflow_completed: false`.
4. The REST path sends no `chsyscfg` and reports no `apply_profile` step.
5. The CLI flag and MCP parameter reach `LparCreation.apply_profile`.
