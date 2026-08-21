# Plan: #330 — bind uvicorn, uvicorn.access and mcp to the bounded sink

Spec: `docs/superpowers/specs/2026-08-21-bounded-sink-third-party-loggers-spec.md` ·
ADR: `docs/adr/0051-fastmcp-logging-through-the-bounded-sink.md` (amendment) +
`docs/adr/0043-non-blocking-stderr-diagnostics.md` (Consequences). Branch
`feat/bounded-sink-330`, BASE `main`. Guardrails: `just test`, `just smoke`,
`just verify` (run `just verify` before push). Non-interactive git per AGENTS.md.

## Task 1 — Generalize the install (TDD red first)

Files: `src/hmc_mcp/server.py`, `tests/app/test_connection_authorization.py`,
`tests/conftest.py`.

0. Fixture first: `isolate_audit_logging` (tests/conftest.py) snapshots and restores
   only the audit logger, root handlers and the fastmcp logger; the install will now
   mutate three more process-global loggers. Extend it to snapshot-and-reset
   handlers, level and propagate for `uvicorn`, `uvicorn.access` and `mcp` exactly as
   `_PRISTINE_FASTMCP` does for `fastmcp` (their pristine state is empty handlers,
   NOTSET, propagate=True). Without this the first serving test leaks the sink onto
   every later module in the session.

1. Write failing tests (place beside the existing ADR 0051 sink tests, after
   `test_the_sink_is_installed_even_when_fastmcp_logging_is_disabled`):
   - `test_the_served_path_takes_fastmcps_handlers_off_fd_2` (existing) — update to
     the new helper name; behavior unchanged for `fastmcp`.
   - New: after `_serve(...)`, each of `fastmcp`, `uvicorn`, `uvicorn.access`, `mcp`
     has exactly one handler, an `_AuditHandler` whose formatter is a
     `StreamSafeFormatter` with prefix `f"{name}: "`; none targets `sys.stderr`
     directly (reuse `_targets_stderr`).
   - New: `uvicorn` and `uvicorn.access` are at level INFO with `propagate=False`;
     `fastmcp` and `mcp` levels/propagation untouched by the install.
   - New: an INFO record on `uvicorn.access` renders exactly once through the sink
     (drain `audit._SINK`, assert one `uvicorn.access: ` line in `_stderr(capsys)`,
     and assert no duplicate line).
   - New: a WARNING record on `mcp` reaches stderr through the sink with the `mcp: `
     prefix. WARNING, not INFO: `mcp` stays handlers-only at NOTSET, its effective
     level is root's WARNING, and an INFO record is dropped at the logger before any
     handler runs — the same gate `lastResort` sat behind, so the binding changes
     where records go, not which records exist.
   - Update `test_installing_the_sink_twice_leaves_one_handler` to loop over all four
     loggers (idempotence per logger).
2. Implementation in `server.py`:
   - Replace `install_fastmcp_stderr_sink` with
     `install_third_party_stderr_sinks`: iterate
     `_SUNK_LOGGERS: Final = ("fastmcp", "uvicorn", "uvicorn.access", "mcp")`;
     per logger: remove all existing handlers, add one `sink_handler()` with
     `StreamSafeFormatter(_FASTMCP_LINE_FORMAT, f"{name}: ")`. For `uvicorn` and
     `uvicorn.access` additionally `setLevel(logging.INFO)` and `propagate = False`
     (ADR 0051 amendment: mirrors uvicorn's own LOGGING_CONFIG; documented exception
     to only-the-handlers). `fastmcp`/`mcp` untouched beyond handlers.
   - Keep the existing docstring's argument structure, updated for the wider set.
   - `_serve_application` calls the new name in place of the old.
   - Clean cutover: no alias for the old name; update every reference (grep
     `install_fastmcp_stderr_sink` across src/ and tests/).
3. Run `just test` (red→green), then `just smoke`.

Acceptance: new tests fail before the change and pass after; no reference to the old
name remains.

## Task 2 — main_http passes log_config=None

Precondition already satisfied on this branch: `uvicorn==0.52.1` is pinned in the `app`
extra (commit e346376), because Task 2's lever rests on version-specific uvicorn source
facts. Verify with `grep uvicorn pyproject.toml`; do not re-pin.

Files: `src/hmc_mcp/server.py`, `tests/app/test_connection_authorization.py`.

1. Failing test: stub `uvicorn.Server.serve` with an async no-op (monkeypatch),
   call `server.main_http(policy, host="127.0.0.1", port=0)`, then assert:
   - `uvicorn`/`uvicorn.access` still carry exactly the one sink handler each (Config
     construction did not add a default `StreamHandler(stderr)` or stdout handler);
   - levels still INFO, propagate still False.
2. Implementation: module constant
   `_UVICORN_CONFIG: Final = {"log_config": None}` with a docstring citing ADR 0051's
   amendment (uvicorn 0.52.1 `config.py` skips `dictConfig` on null config);
   `main_http` passes `uvicorn_config=_UVICORN_CONFIG` to `.run()`.
3. `just test` green.

Acceptance: the serve path constructs `uvicorn.Config` with `log_config=None`; no
default handler lands after the install.

## Task 3 — Docs already amended; verify consistency

ADRs 0043/0051 are already amended on this branch (commits 0c589ba..HEAD). Verify no
stale references remain: grep for `install_fastmcp_stderr_sink` in docs/ and src/;
the ADR names `install_third_party_stderr_sinks`. Fix any stragglers.

## Task 4 — Guardrails + review loop

`just verify`. Then the branch review loop (/review-loop --base main) per the
workflow; fix defensible findings; commit per fix. Then /simplify if warranted,
re-verify, push, PR with body referencing `Closes #330`.

Rollback: single-feature branch; revert the branch's commits as one contiguous
range, newest first: `git revert --no-edit <oldest>~1..<newest>` (`A..B` selects
commits reachable from B but not A, so the older end needs the `~1`). Reverting the
full range — not only the code commits — keeps ADR 0043/0051's closed-residual text
in agreement with the code; reverting code alone would strand records claiming
residuals the reverted code no longer closes.
