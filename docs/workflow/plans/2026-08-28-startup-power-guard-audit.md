# Implement startup power-guard audit records

Goal: emit durable per-connection effective power-guard records from serve startup. The server
reuses `resolve_power_guards` and hands its closed rows to the audit record builder selected by
ADR 0107. Python, pytest, and the existing logging sink are the complete stack.

## Global Constraints

Python versions and architectures remain those declared by the repository and CI. Add no
dependency, migration, public API export, MCP surface, or configuration key. Run `just verify`
and `UV_NO_SYNC=1 uv run --no-sync prek run --all-files`.

## Task 1: Define and render the audit event

Files: `src/hmc_mcp/audit/records.py`, `tests/unit/test_audit.py`.

Interfaces: add `record_power_ownership_guard(*, connection: str,
authorize_power_operations: bool | None, source: str, detail: str | None) -> None`; extend
`Event` with `power-ownership-guard`. The server task consumes this function.

1. Add a failing unit test that captures the exact warning-level JSON shape for true, false,
   and unresolved values and verifies long strings are bounded.
2. Run `uv run --no-sync pytest tests/unit/test_audit.py -q`; expect the new test to fail
   because the function/event does not exist.
3. Implement the minimal builder through existing `_value` and `emit`.
4. Re-run the command; expect all tests to pass. Commit the tested event contract.

## Task 2: Emit records during serve bootstrap

Files: `src/hmc_mcp/server.py`, `tests/app/test_authorization_audit.py`.

Interfaces: import `resolve_power_guards` and call Task 1's builder after sink installation.
`_serve_application(enable_arbitrary_command, access_policy, audit_level)` retains its signature
and return contract.

1. Add failing app tests for a policy withholding `hmc_effective_permissions`: two reachable
   profiles emit two records with their distinct effective values; malformed configuration
   emits unresolved without preventing `_serve_application` from returning.
2. Run the focused app tests; expect no `power-ownership-guard` records.
3. Add one startup loop over `resolve_power_guards(access_policy)` and record every row.
4. Re-run focused tests; expect pass. Commit the startup integration.

## Task 3: Document and verify the contract

Files: `docs/authorization-audit.md`, `docs/environment-variables.md`,
`tests/test_authorization_audit_doc.py`, `CHANGELOG.md` if its rules require an entry.

Interfaces: the audit document mirrors `audit.EVENTS`; the environment document points to the
new event. No source interface changes.

1. Update the audit event section and JSON sample, then replace the obsolete environment note.
2. Extend documentation tests only where the existing vocabulary checks do not already bite.
3. Run `uv run --no-sync pytest tests/test_authorization_audit_doc.py -q`; expect pass.
4. Run `just verify` and `UV_NO_SYNC=1 uv run --no-sync prek run --all-files`; expect both pass.
5. Commit documentation and any generated artifacts. Rollback is `git revert` of these additive
   commits; no cleanup or data migration is needed.
