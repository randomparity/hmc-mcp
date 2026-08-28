# Validation-first LPM implementation plan

## Task 1 — Operation contract

Add failing tests for default ordering, successful terminal statuses, opt-out, and `wait=False`.
Use table-driven blocked-outcome tests for failed, exceptional, canceled, timed-out, and
unexpected/non-terminal normalized statuses; every row asserts the exact status/error in the
raised `HMCError` and zero migration submissions. Separately test propagation of validation
submission and polling exceptions. Add boundary tests proving invalid timing with
`validate_first=True, wait=False` fails before selector resolution or either HMC submission, while
unused invalid timing remains accepted with `validate_first=False, wait=False`. Implement
sequencing in `operations/lpm.py` using `JobOutcome`, `wait_for_submitted_job`, `job_outcome`, and
effective preflight validation equivalent to `validate_wait_timing(validate_first or wait, ...)`.

## Task 2 — MCP and CLI

Add failing handler/capability/CLI tests. Add `validate_first=True` to `hmc_migrate_lpar`, and
convert both `hmc_migrate_lpar` and `hmc_migrate_validate_lpar` to the stable `JobOutcome` return
shape. Assert that both handlers return that concrete shape for waited and non-waited successful
calls. Add and forward `--validate-first/--no-validate-first`, `--wait/--no-wait`, `--timeout`,
and `--interval` on the applicable migration CLI commands; test defaults, opt-out, and forwarding.
Run CLI-side effective timing validation before confirmation or client acquisition; tests prove
invalid default validation-first timing reaches neither boundary, while unused timing remains
accepted for direct non-waiting submission.
Document the sequence/default/failure in the docstring and MCP instructions.

## Task 3 — Documentation and verification

Update the README's migration examples/tool description. Run focused tests, Ruff, ty, and smoke,
then simplify the branch. Run adversarial and security reviews, fix every defensible finding, and
re-run each affected review until approved. Run `just verify` after all simplification and
review-driven changes as the final local gate before delivery.
