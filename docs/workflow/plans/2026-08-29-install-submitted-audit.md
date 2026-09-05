# Plan: deliver detached install PIDs through the audit stream

Goal: implement ADR 0109 with one post-submit audit event. The existing audit records
module owns rendering and bounds; the install operation owns ordering around the SSH call.
Python 3.11–3.14, amd64 and arm64, no new dependencies, base `main`.

1. In `tests/unit/test_audit.py` and `tests/unit/test_install_operations.py`, add failing
   assertions for the `install-submitted` vocabulary, PID field, served-sink delivery,
   matching correlation fields, and no success event on a raised submit. Run the named
   tests with `uv run --no-sync pytest ...`; expect failure before implementation.
2. In `src/hmc_mcp/audit/records.py`, add `install-submitted` to `Event` and implement
   `record_install_submitted(*, system: str, partition: str, pid: int, log_path: str,
   host: str, agent_id: str) -> None` using the shared bounded renderer at warning level.
3. In `src/hmc_mcp/operations/install.py`, call that builder only after
   `run_installios` returns. Re-run the focused tests; expect all to pass.
4. Update ADRs 0102/0109, `docs/authorization-audit.md`, `CHANGELOG.md`, and their drift
   expectations. Run `just test`, `just tool-docs-check`, `just doc-freshness`, then
   `just verify` and `UV_NO_SYNC=1 uv run prek run --all-files`; expect exit 0.

Rollback is a git revert: there is no persistence or migration. The audit vocabulary is
additive, and consumers must ignore unknown events under the existing stability contract.
