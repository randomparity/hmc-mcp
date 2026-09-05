# Actionable job-status implementation plan

## Scope

- `src/hmc_mcp/jobs.py`: make terminal classification exhaustive and normalize fallback errors.
- `src/hmc_mcp/operations/health.py`: consume the actionable classification through the existing
  failed-status interface.
- `tests/unit/test_job_lifecycle.py`: prove the partition and every outcome class.
- `tests/system/test_fleet_health.py`: prove every actionable terminal status is visible.

## Task 1 — Lock the classification with failing tests

Add a partition assertion covering every accepted terminal status. Parameterize outcome tests over
all actionable statuses and assert terminal, non-clean results. Update fleet-health coverage to
expect canceled and warning statuses while excluding success and non-terminal values. Run the
focused tests and confirm they fail against the current classification.

## Task 2 — Implement the smallest contract-preserving correction

Restrict the success set to clean success, derive the actionable failure set from the terminal
set, and add a status-specific fallback when HMC detail is absent. Keep HMC detail precedence and
all result shapes unchanged. Run the focused tests until green.

## Task 3 — Verify consumers and guardrails

Search all status-set consumers to confirm the stricter success meaning is appropriate. Run
`just test`, `just smoke`, and `just verify`; inspect the final tracked diff and clean status.
