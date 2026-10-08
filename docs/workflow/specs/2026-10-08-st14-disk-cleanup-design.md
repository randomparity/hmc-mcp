# ST14 stale test-disk cleanup (#1388)

## Problem and scope

ST14 uses `rmvlog` with a VIOS UUID under `-p`; generic missing-resource
messages become expected absence. Repair only stale-disk cleanup, its immediate
caller, the obsolete runner comment, and their tests. Exclude orphaned mappings
(owner #1387), as approved by the operator on 2026-10-07. Scope authority:
https://github.com/randomparity/hmc-mcp/issues/1388#issuecomment-6063102589.

## Design and success

Use existing `hmc_delete_virtual_disk`, passing the selected VIOS UUID, configured
VG UUID, exact test-disk name and managed-system scope. Its mapping guard and
ETagged exact-disk removal remain the mutation owner. Replace the harness's shell
delete and broad expected-error declaration; no compatibility path is needed.
Use existing `volume_listing` and `volume_names` for independent VIOS `--id`
`lsvg -lv` reads before deletion and after an accepted delete. Require a positive
integer VIOS partition ID before destructive ST14 work. Require the exact configured
group heading, full known header and structurally valid rows before accepting absence.
An exact absent name in a successful, valid listing skips deletion; a present disk
is deleted and must disappear on readback. Read/parse/delete failures or a surviving
disk produce FAIL and stop recreation and provisioning. A failed delete remains FAIL
even if it might have taken effect; a future run must obtain fresh evidence.
Retain the create and post-create listing behavior once stale-disk cleanup succeeds.

Existing deletion plus listing is the smallest supported implementation. A raw
`rmlv` path would duplicate the existing deletion/mapping boundary. No product API,
shared parser, configuration field, or numbered decision is introduced.

## Failure model

- Actors and deployments: authorized operator running ST14 on the configured VIOS;
  offline tests run in CI on amd64/arm64 Python 3.11–3.14.
- Invariants/assets: delete only the configured stale disk, preserve mapping guards,
  accept absence only from successful independent exact inventory, fail closed before
  dependent recreation/provisioning when that inventory or deletion fails.
- Accepted classes: concurrent independent administrator changes between listing and
  deletion are outside the serialized operator window; product ETag/mapping guards
  remain active. No retry or alternate storage target is selected.
- Covered elsewhere: orphaned mappings #1387; product delete validation and ETags
  existing storage operation/client tests; unrelated ST14 lifecycle behavior unchanged.

## Threat model

- Boundaries: existing operator config/VIOS identity to CLI and tool arguments;
  HMC command output to absence decision. No new actor or widened authorization.
- Actors/trust: trusted serialized operator; HMC responses may be errors or malformed.
- Controls: existing config name validation, positive integer partition ID, existing
  shell quoting in `volume_listing`, exact group/header and row validation, exact disk
  membership, existing product mapping/ETag checks. Failures use existing row redaction.
- Out of scope: compromised HMC fabricating valid inventory; external mutation outside
  the serialized run; orphan mapping repair belongs to #1387.

## Validation

Focused synthetic tests pin UUID/VG/name/system delete arguments and `--id` read
command, absence versus similarly named disk, error strings formerly masked,
malformed/wrong-group inventory, failed read/delete, surviving-disk readback, and
missing/invalid partition ID. New tests must fail against original code; a controlled
fault must demonstrate absence checking bites. Run focused runner tests, `just verify`,
and pinned all-files prek. Spec/comment prose has no executable consumer assertion.
Live proof with a stale run-owned disk remains pending the operator-approved
consolidated re-record round; offline results do not promote live observations.
