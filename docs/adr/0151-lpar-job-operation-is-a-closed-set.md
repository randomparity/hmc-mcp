# ADR 0151: The LPM job operation segment is a closed set

## Status

Accepted (2026-09-16)

## Context

`LpmMixin._lpar_job` interpolates `operation` raw into
`/rest/api/uom/LogicalPartition/{lpar_uuid}/do/{operation}` (`client_lpm.py:24-29`) and nothing
governs it. ADR 0147 named this site as the one other non-UUID segment built into a uom path
outside `core.py`, said it "needs a grammar of its own rather than this one", and deferred it.
Issue #828 is that deferral coming due.

The site is latent, not live. `_lpar_job` is private and all five callers are in the same module,
each passing a literal — `Migrate`, `MigrateValidate`, `MigrateAbort`, `MigrateRecover`,
`RemoteRestart` (`client_lpm.py:50`, `:71`, `:78`, `:86`, `:110`). A rule buys that the sixth call
site cannot introduce a caller-supplied value silently.

Two facts decide the shape. The namespace here is **closed**, which the uom type namespace is not:
ADR 0143 accepts that no published list of HMC type names exists and so had to write a character
grammar, while the operations this client submits are exactly the five its own code names. And the
drift test that would have caught this site cannot see it — `_uom_path_sites()` parses
`hmc_mcp.client.core` alone — so no test-time observation is available either. Widening that walk
is issue #828's other half, excluded here.

## Decision

**`_lpar_job` refuses an `operation` outside `_LPAR_JOB_OPERATIONS` — a module-level frozenset of
the five literals named above — by membership rather than by grammar, before the path is built.**

**Membership, not a grammar.** ADR 0147 forecast a grammar but deferred the question rather than
deciding it. `[A-Za-z][A-Za-z0-9]*` would admit `PowerOff` and `DeleteVirtualAdapters` — real HMC
job operations this client does not submit — where membership admits neither. A closed set is
available precisely because the argument is private and every caller is in the file.

**`ValueError`, at the site, naming no value.** ADR 0148 draws the line and ADR 0150 restates it:
a per-argument validator called where the segment is built raises `ValueError`, while a guard
running *at* the request waist raises `HMCError` because it holds a path it did not build. This
runs where the segment is built. The message names the permitted set — the actionable half — not
the rejected value, the leak rule `_reject_unknown_uom_type` and `_reject_over_long_path_value`
already follow. It is qualified — `LPM job operation must be one of: ...` — because
`cli_commands/lpar/migration.py:239` already emits a bare `operation must be one of: ...` for a
different `operation` on the same `lpar remote-restart` path; `jobs/requests.py:253` is the
in-repo precedent for the qualifier. The set lives in `client_lpm.py` rather than
`client_contracts.py`: it governs one function in one module and has no second caller.

## Consequences

- An `operation` outside the five is refused with a `ValueError` before `submit_job` is called,
  and so before any request is built. Nothing in this repository passes such a value, so no
  existing input is newly refused, and there is no wire-format change:
  `tests/lpar/test_lpm.py:102-154` routes each literal through `respx` and reads
  `route.calls.last`, so a sixth call site whose literal is missing reddens its own test.
- **The set has to be edited to add an operation** — the deliberate cost of closing the namespace.
- **The drift walker still cannot see this site.** The guarantee is a runtime refusal rather than
  a test-time observation, so it does not depend on the walk — but any future uom path built
  outside `core.py` keeps the same exemption. Issue #828's second half, unowned.
- ADR 0147's follow-up candidate for `operation` is closed; its seven other ungoverned segment
  arguments stay open and unowned.

## Considered & rejected

- **A character grammar, as ADR 0147 forecast.** verified: all five literals match `_UOM_TYPE`'s
  `[A-Za-z][A-Za-z0-9]*` (`client_contracts.py:39`), so a grammar accepts every live caller.
  judgment: it equally accepts every other HMC job operation and any invented name in that class.
- **Reuse `_reject_unknown_uom_type`.** verified: its message reads "must be an HMC resource type
  name" (`client_contracts.py:84-88`). judgment: `operation` is not a resource type, so it would
  describe a rule the value did not break.
- **Type `operation` as a `Literal`, as `AdapterType` does.** verified: the `LpmClient` protocol
  declares `operation: str` (`client_contracts.py:370-372`), which every call resolves through.
  judgment: widening the protocol is outside this surface, and a static type refuses nothing at
  runtime.
- **Echo the rejected value, as `validate_adapter_type` does.** judgment: "internal today" is the
  assumption this record exists to stop relying on.
- **Do nothing, recording literal-only callers as the contract.** verified: issue #828 offers this
  as the alternative. judgment: the five literals are already that convention; the defect is that
  nothing makes the sixth honour it.
- **Widen `_uom_path_sites()` to reach `client_lpm.py`.** verified: ADR 0147 measured eight
  unclassified names in the same walk over `src/hmc_mcp/client/*.py`. judgment: excluded by the
  operator; it needs its own PR to fix or classify what it finds.
