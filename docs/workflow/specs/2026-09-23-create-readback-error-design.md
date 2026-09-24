# A failed read-back after mksyscfg still reports the create (#1014)

Issue #1014 · branch `fix/create-readback-error-1014` · base `main` · follows
[the #999 spec](2026-09-23-provision-apply-profile-design.md).

## Problem

On the `mksyscfg` path `create_and_stamp_lpar` creates the partition, applies its profile, then
reads it back by name. An `HMCError` from that read-back escapes, so `lpars create` fails and
`lpars provision` reports a `create` error with `resource_created: false` and no
`apply_profile` step, although the partition exists.

## Scope

- In the CLI branch only, catch `HMCError` from `find_partition_by_name` after the create and
  apply. Treat it as the existing `created_lpar is None` result: `resource_created=True`,
  `lpar=None`, `ownership_stamped=None`, the apply step, and the apply warnings.
- The ownership-stamp-skipped warning names the read-back error in place of
  "create returned no LPAR body". A `None` read-back keeps its current wording.
- `stamp_policy='required'` (decided here, per the dispatch triage): raise `HMCError`, as the
  `None` branch does, chained from the read-back error. The message names the partition, says
  it exists (created by `mksyscfg`) but is unstamped, includes the read-back error, and gives
  the re-stamp or delete advice. Before this change the bare read-back error escaped.
- Provision is unchanged: its no-UUID branch reports `create` `error`, then the apply step.
- `workflows.create_lpar` is unchanged; when `lpar` is `None` it still emits no PCIe assignment
  steps (pre-existing, outside the surface; a follow-up candidate). No ownership transition. REST-path read-back is excluded (operator); live confirmation #879.
- CHANGELOG `Fixed` entry.

### Failure model

1. Actors and deployments: an operator (`lpars create`, `lpars provision`) or MCP agent on an
   HMC whose REST create returns 406.
2. Invariants: a partition `mksyscfg` created is reported as created; on that path the apply
   step is kept whenever the apply ran.
3. Accepted: a non-`HMCError` read-back failure still propagates: a `TypeError` is a defect,
   and the ambiguous-name `ValueError` needs a same-name create racing this one after the
   pre-create name check. `HMCCLIError` is a subclass and is caught.
4. Covered elsewhere: REST create path (operator); live evidence (#879).

## Success

1. `mksyscfg`-path create, read-back raises `HMCError`: result has `resource_created=True`, `lpar=None`, the
   apply step, and a warning containing the read-back error text.
2. Provision in the same case: `resource_created=True`, `create` `error`, `apply_profile`
   step present, later steps `skipped`.
3. `stamp_policy='required'` in the same case raises `HMCError` whose message contains the
   partition name and the read-back error.

## Validation

- Success 1: `focused-test` in `tests/lpar/test_lpar_http406.py`, 406 create whose second name
  search answers HTTP 500; red before the change (`HMCError` raised). Green:
  `uv run --no-sync pytest tests/lpar/test_lpar_http406.py`.
- Success 2: `focused-test` in `tests/lpar/test_provision_tool.py`, same fault through
  `_provision_via_406`; red before the change (`resource_created` false, no apply step). Green:
  `uv run --no-sync pytest tests/lpar/test_provision_tool.py`.
- Success 3: `focused-test` in `tests/lpar/test_lpar_http406.py`, `create_and_stamp_lpar` on a
  stub client whose REST create raises 406 and whose read-back raises; red before the change
  (message lacks "mksyscfg"). Same green command as Success 1.
- CHANGELOG entry: `task-test-not-applicable`, prose with no executable consumer.
