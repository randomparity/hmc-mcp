# Validation-first LPM design

Issue: #151  
Decision: [ADR 0018](../../adr/0018-validation-first-lpm.md)

## Contract

The existing migration operation accepts `validate_first=True` by default. It resolves the LPAR
and target once, submits `MigrateValidate`, waits for a terminal `JobOutcome`, and submits
`Migrate` only for `COMPLETED`, `COMPLETED_OK`, or `COMPLETED_WITH_WARNINGS`. Any other outcome
raises `HMCError` with the normalized validation status and error; migration is not called.

`validate_first=False` preserves direct migration submission. `wait=False` still returns after
the migration submission, but never weakens the validation gate. Both MCP paths expose
`JobOutcome`, reusing `jobs.py` timing, polling, status, timeout, and error semantics.

## Components

- `operations/lpm.py` owns sequencing and returns the existing `LpmResult` containing a
  `JobOutcome`.
- `server_tools/lpm.py` adds the public option and documents ordering/default/failure.
- `cli_commands/lpars.py` mirrors `--validate-first/--no-validate-first` and wait controls.
- Focused operation, MCP, CLI, and capability tests pin ordering, opt-out, failures, timeout,
  exception detail, stable shape, and read/write classification.
- README and MCP instructions describe the safe default narrowly.

## Error handling and verification

When `validate_first=True`, timeout and poll interval are validated before selector resolution or
either submission using effective waiting semantics (`validate_wait_timing(True, ...)`), even when
the migration itself uses `wait=False`. When `validate_first=False`, the existing rule remains:
timing is validated only when migration uses `wait=True`. Focused tests cover invalid timing for
validation-first `wait=False` and the direct opt-out path, and assert that neither path submits an
HMC job when its active timing is invalid. Validation submission/poll errors propagate.
Failed, exceptional, canceled, or timed-out normalized validation raises an actionable `HMCError`.
Tests assert no migration call in every blocked case and exact validation-before-migration order.
The final gate is `just verify`.
