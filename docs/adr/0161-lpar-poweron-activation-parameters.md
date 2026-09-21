# ADR 0161: LPAR PowerOn activation parameters are closed vocabularies validated at the builder

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
`jobs/requests.py:15-18`. Refusal is `ValueError` naming the sorted permitted
set, matching ADR 0158's style at `operations/lpar/core.py:498-500`.

**The partition profile is a UUID the caller supplies, named
`partition_profile_uuid` above the builder and `--partition-profile` on the CLI.**
Never `profile`, which is the connection profile on both surfaces.

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
editing one `Literal` and the tests that enumerate it. `tests/unit/test_xml_escaping.py`
grows its generated cases automatically for `bootmode` and `profile_uuid`, but
not for `operation_type`: `_is_closed_vocabulary` tests `get_origin(...) is
Literal`, which an optional `Literal | None` fails, so that refusal needs its own
test. Two behaviours stay unproven until hardware: whether a fresh partition
activates without a profile, and whether `bootmode=sms` reaches `open firmware`.

## Considered & rejected

- **Put the vocabularies in `operations/lpar/core.py` beside
  `ProcessorCompatibilityMode`.** verified: that alias is consumed by
  `server_tools/lpar/configuration.py:404` and `cli_commands/lpar/config.py:412`,
  so both placements have precedent. judgment: these values are job-document
  parameters, and the builder that emits them is the only thing that can refuse
  one before the wire.
- **Validate in `power_lpar` instead of the builder.** judgment: `power_lpar`
  is not the only caller of the builder, and a document builder that trusts its
  arguments is the gap #867 opens.
- **Accept a profile name and resolve it.** verified: `rg
  LogicalPartitionProfile src/hmc_mcp` returns one docstring hit
  (`server_tools/systems/core.py:244`); no profile feed read exists. The
  reference defines the parameter as "the uuid of the profile"
  (`docs/refs/hmc-rest-api-p11/jobs/logicalpartition-jobs/039-poweron_logicalpartition-job.md:52`).
- **Use the table's second spelling — `BootMode`, `LogicalPartitionProfileUUID`.**
  verified: only the first spelling appears in the document's own JSON and XML
  samples (`:99-120`, `:155-190`); the second appears at `:62-72` in no sample.
- **Hold `OperationType` back until #868.** judgment: the operator chose all four
  surfaces; `activate` is a real explicit-intent value, and widening a `Literal`
  later is cheaper than threading a parameter through five call sites later.
- **Do nothing.** verified: `bootmode` is fixed at `norm` at
  `jobs/requests.py:65`, so `sms` and `of` are unreachable and the `open
  firmware` state at `operations/partition_state.py:5-17` is readable but never
  produced.
