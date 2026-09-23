# ADR 0161: LPAR PowerOn activation parameters are closed vocabularies refused before the wire

## Status

Accepted (2026-09-21)

## Context

`power_on_lpar_job()` takes no arguments and emits three constants, so no caller
can pick the profile a partition activates against or the boot mode it activates
into. Issue #867 makes `bootmode`, `LogicalPartitionProfile` and `OperationType`
reachable; epic #866 leaves `OperationType=netboot` to #868, which cannot settle
its companion parameters from the reference corpus.

Three questions have viable answers. Where do the vocabularies live and who
refuses a bad value; what identifies the partition profile; and how much of
`OperationType` ships before netboot exists.

## Decision

**`jobs/requests.py` owns `BootMode` and `PowerOnOperationType` as `Literal`
aliases with matching `frozenset`s, and `power_on_lpar_job` validates membership
before building XML** — the shape `RemoteRestartOperation` already uses at
`jobs/requests.py:15-16`. Refusal is `ValueError` naming the sorted permitted
set, matching ADR 0158's style at `operations/lpar/core.py:554-556`.

**The partition profile is a UUID the caller supplies, named
`partition_profile_uuid` above the builder and `--partition-profile` on the CLI.**
Never `profile`, which is the connection profile on both surfaces.

**`power_lpar` verifies the profile is contained by the target partition before
submitting**, reading that partition's own `LogicalPartitionProfile` feed through
the existing `list_child`. `hmc_power_on_lpar` carries the decorator's default
`exhaustive_targets=True`, which ADR 0039 defines as meaning every resource acted
on is a declared selector's value or is derived by the server through the HMC's own
containment from one. A caller-supplied profile UUID is in neither
`REQUIRED_TARGET_ARGUMENTS` nor `UNBOUNDED_ARGUMENTS`, so `targets_permitted` never
compares it against the grant; without this read a narrow `targets = {lpar = [...]}`
grant would bound nothing here. The read is what makes the profile a derived,
contained resource, and it happens only when a profile is supplied.

**`OperationType` ships on all four surfaces with `activate` as its only member.**
`power_on_lpar_job`, `power_lpar`, `power_on_lpar`, `hmc_power_on_lpar` and
`hmc-mcp lpars power-on` all accept it; #868 widens the `Literal` rather than
threading it.

Every new parameter defaults so that a call passing none of them emits today's
document byte for byte.

## Consequences

`bootmode` reaches `sms` and `of`, so the `open firmware` state in
`operations/partition_state.py` becomes reachable. A caller holding only a
profile name cannot use `--partition-profile` until name-to-UUID resolution
exists; the epic owns that. Adding a boot mode or an operation type means
editing one `Literal` and the tests that enumerate it.
`tests/unit/test_xml_escaping.py` grows its generated cases automatically for all
three parameters: `_unwrap` (`:96-106`) strips the optional before
`_is_closed_vocabulary` (`:146`) tests it, so `operation_type` is covered too.

Changing the tool's parameters restates its signature, which two committed
artifacts record. `docs/capabilities/operations.json` stores
`str(inspect.signature(handler))` and `scripts/check_capability_inventory.py`
compares it by string equality inside `just verify` and the `capability-inventory`
hook, so that record is updated in the same change. The `Args:` block is the
source of the rendered MCP schema descriptions, and
`tests/app/test_lifecycle_schema_descriptions.py` requires a non-empty
description for every `hmc_power_on_lpar` parameter, so each new parameter earns
an `Args:` entry. Two behaviours stay unproven until hardware: whether a fresh
partition activates without a profile, and whether `bootmode=sms` reaches
`open firmware`.

## Considered & rejected

- **Put the vocabularies in `operations/lpar/core.py` beside
  `ProcessorCompatibilityMode`.** verified: that alias is consumed by
  `server_tools/lpar/configuration.py:404` and `cli_commands/lpar/config.py:412`,
  so both placements have precedent. judgment: these values are job-document
  parameters, and the builder that emits them is the only thing that can refuse
  one before the wire.
- **Validate in `power_lpar` *instead of* the builder.** verified: the two sites are
  not equivalent in coverage, though an earlier draft of this record assumed they
  were. `power_lpar`'s already-running early return consumes the activation
  parameters and returns without building a document, so a builder-only check never
  runs there; a branch review reproduced an invalid boot mode being accepted on that
  path. judgment: the answer is both rather than either — `validate_power_on_activation`
  holds the sets and the wording, the builder calls it as the wire-level guarantee,
  and `power_lpar` calls it before the state read. Validating *only* in `power_lpar`
  is still rejected: `jobs.power_on_lpar_job` is re-exported for direct use, so the
  builder cannot trust its arguments.
- **Accept a profile name and resolve it.** verified: before this change `rg
  LogicalPartitionProfile src/hmc_mcp` returned one docstring hit
  (`server_tools/systems/core.py:244`) and no profile feed read existed anywhere.
  The reference defines the parameter as "the uuid of the profile"
  (`docs/refs/hmc-rest-api-p11/jobs/logicalpartition-jobs/039-poweron_logicalpartition-job.md:52`).
  judgment: the containment read this record adds lists a partition's profiles but
  does not resolve a name to a UUID, and the epic owns that.
- **Use the table's second spelling — `BootMode`, `LogicalPartitionProfileUUID`.**
  verified: only the first spelling appears in the document's own JSON and XML
  samples (`:99-120`, `:155-190`); the second appears at `:62-72` in no sample.
- **Hold `OperationType` back until #868.** judgment: the operator chose all four
  surfaces; `activate` is a real explicit-intent value, and widening a `Literal`
  later is cheaper than threading a parameter through five call sites later.
- **Declare `partition_profile_uuid` unbounded and set `exhaustive_targets=False`.**
  verified: 13 tools already declare it, so the idiom exists, and
  `tool_registry.py:538` is where the `True` default comes from. judgment: it is the
  strictly correct classification, but it removes `hmc_power_on_lpar` from every
  narrow `targets` grant, costing existing operators an access they have today to
  close a gap the containment read closes without that cost.
- **A UUID-shape guard plus a written residual.** verified: the corpus addresses a
  profile as a child of a partition
  (`docs/refs/hmc-rest-api-p11/managed-system/170-logical-partition-profile.md:27`),
  so the *kind* is contained. judgment: the *instance* is not — a well-formed UUID
  from another partition still passes — and resting on the HMC to refuse it is the
  reasoning ADR 0044 explicitly declined for `backup_name`.
- **Do nothing.** verified: `bootmode` was fixed at `norm` at
  `jobs/requests.py:65` before this change, so `sms` and `of` are unreachable and the `open
  firmware` state at `operations/partition_state.py:5-17` is readable but never
  produced.
