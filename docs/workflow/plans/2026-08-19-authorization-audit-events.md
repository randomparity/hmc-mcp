# Implementation plan — structured authorization audit events (#224)

**Goal.** Emit one structured, redacted, single-line JSON record per authorization decision at the
MCP dispatch boundary, and converge the package's second audit emitter onto the same record.

**Architecture.** A new `src/hmc_mcp/audit.py` owns the record vocabulary, its rendering, and its
sink. `dispatch_scope.authorize` — the only place in the package that reaches an authorization
decision — emits exactly one record per decision. `target_scope` gains `denial_reason` so the
message and the reason code have one owner. `operations_lpar`'s ADR 0011 override record moves
onto the same logger. `server._serve_application` installs the sink.

**Tech stack.** Python 3.11+, stdlib `logging` and `json` only. No new dependency (epic #218
requirement 11).

Source of truth: [the design spec](../specs/2026-08-19-authorization-audit-events-design.md) and
[ADR 0040](../../adr/0040-authorization-audit-events.md). Where this plan and the spec disagree,
the spec wins and this plan is wrong.

## Global constraints

- **`audit.py` imports nothing from `hmc_mcp`.** `target_scope` imports `Reason` from it, so any
  import back is a cycle. One exception, and only one: `audit` reads
  `os.environ.get("HMC_AGENT_ID")` itself.
- **Every caller-supplied value is truncated to 128 characters** — `MAX_VALUE_LENGTH` — with no
  marker.
- **`json.dumps(..., ensure_ascii=True)`**, one line, written as `message + "\n"` in a single
  `write` call.
- **Emission never raises into `authorize`.** Building and writing are both total.
- **Reserved logger.** Only `audit` resolves `hmc_mcp.audit`.
- Repo limits: ≤100 lines per function, cyclomatic complexity ≤8, 100-character lines.
- Guardrail: `just verify`, run **bare** — no pipes, no `|| true`. `just static` is the fast
  subset while iterating.
- Conventional commits, imperative subject ≤72 chars, `Co-Authored-By: Claude Opus 5 (1M context)
  <noreply@anthropic.com>` trailer. Never squash; this branch is code.

## Files

| path | action | answerable for |
|---|---|---|
| `src/hmc_mcp/audit.py` | create | record vocabulary, rendering, truncation, the two emitters, the sink |
| `src/hmc_mcp/target_scope.py` | modify | `denial_reason` added; `target_denial` refactored to read it |
| `src/hmc_mcp/dispatch_scope.py` | modify | assemble and emit one record per decision |
| `src/hmc_mcp/operations_lpar.py` | modify | `_audit_lpar_ownership_override` body calls `audit` |
| `src/hmc_mcp/server.py` | modify | `_serve_application` installs the sink |
| `tests/unit/test_audit.py` | create | rendering, truncation, sink, totality |
| `tests/unit/test_target_scope.py` | modify | `denial_reason` case order |
| `tests/unit/test_ownership.py` | modify | override test repointed to the new record |
| `tests/app/test_authorization_audit.py` | create | boundary, redaction, attribution, checkout claims |
| `tests/app/test_authorization_audit_live.py` | create | live proof Runs A and B |
| `docs/authorization-audit.md` | create | operator-facing contract |
| `README.md` | modify | descriptor-merge caveat + pointer |

## Interfaces

Defined by Task 1, consumed by Tasks 2–5. Exact signatures:

```python
AUDIT_LOGGER_NAME: Final[str] = "hmc_mcp.audit"
MAX_VALUE_LENGTH: Final[int] = 128
ATTRIBUTION_ENV: Final[str] = "HMC_AGENT_ID"
DEFAULT_RENDERING: Final[str] = "<default>"
UNRESOLVED_RENDERING: Final[str] = "<unresolved>"

Reason = Literal[
    "permitted", "configuration-unreadable", "connection-not-granted",
    "target-selector-unreadable", "target-unboundable",
    "target-selector-absent", "target-not-granted",
]
REASONS: frozenset[str]                    # = frozenset(get_args(Reason))
State = Literal["present", "absent", "unreadable"]

@dataclass(frozen=True)
class AuditTarget:
    kind: str
    argument: str
    state: State
    value: str | None

def resolved_connection(value: str | None) -> str: ...
    # None -> DEFAULT_RENDERING; "" (connection_scope.UNRESOLVED) -> UNRESOLVED_RENDERING;
    # otherwise the profile key unchanged.

def record_authorization(
    *,
    policy: str,
    tool: str,
    effect: str,
    decision: Literal["allow", "deny"],
    reason: Reason,
    token: Any,                              # the caller's raw connection argument
    resolved: str | None,                    # resolved_connection(...), or None if never resolved
    targets: tuple[AuditTarget, ...] | None, # None == selectors never extracted
) -> None: ...

def record_ownership_override(*, system: str, lpar: str, agent_id: str) -> None: ...

def install_audit_sink() -> None: ...
```

Task 2 adds to `target_scope`:

```python
def denial_reason(security: ToolSecurity, extracted: tuple[Selected, ...]) -> Reason: ...
```

---

## Task 1 — `audit.py`: the record, its rendering, and its sink

Creates `src/hmc_mcp/audit.py` and `tests/unit/test_audit.py`. Testable alone: this module imports
nothing from the package, so no policy, config file, or application is needed.

### Steps

1. Write `tests/unit/test_audit.py` with the autouse isolation fixture **first**, before any test
   — it is the precondition for everything else in the file:

   ```python
   @pytest.fixture(autouse=True)
   def _isolate_logging():
       logger = logging.getLogger(audit.AUDIT_LOGGER_NAME)
       saved = (list(logger.handlers), logger.level, logger.propagate,
                list(logging.root.handlers))
       try:
           yield
       finally:
           logger.handlers[:] = saved[0]
           logger.setLevel(saved[1])
           logger.propagate = saved[2]
           logging.root.handlers[:] = saved[3]
   ```

2. Write spec tests 1–8, 8a, 14, 14a, 14b, 14c, 15 and 9–13 as failing tests. Run
   `uv run pytest tests/unit/test_audit.py -q`. **Expect: collection error, `No module named
   'hmc_mcp.audit'`.** That is the confirm-it-fails step.
3. Create `src/hmc_mcp/audit.py` with the constants, `Reason`, `REASONS`, `State`, `AuditTarget`,
   and `resolved_connection` from the Interfaces block.
4. Add the value renderer:

   ```python
   def _value(raw: Any) -> str | None:
       """One caller-supplied value, truncated. None when there is nothing to render."""
       if raw is None:
           return None
       return raw[:MAX_VALUE_LENGTH] if isinstance(raw, str) else None
   ```

   A non-`str` renders `None` rather than its `repr()`, for the reason `target_scope.target_denial`
   already declines to render one: an arbitrary object's `repr()` is not the caller's token and can
   carry anything.
5. Add `_connection(token, resolved, extracted_targets_known)` returning the `connection` object:
   `state` is `"absent"` for `None` or `""`, `"present"` for any other `str`, `"unreadable"`
   otherwise; `selector` is `_value(token)`; `resolved` is the argument, or `None` when the
   selectors were never extracted.
6. Add `_attribution()` returning
   `{"claim": _value(os.environ.get(ATTRIBUTION_ENV)), "source": f"environment:{ATTRIBUTION_ENV}",
   "verified": False}`.
7. Add `_emit(level, payload)`:

   ```python
   def _emit(level: int, payload: dict[str, Any]) -> None:
       try:
           message = json.dumps(payload, ensure_ascii=True)
           logging.getLogger(AUDIT_LOGGER_NAME).log(level, message)
       except Exception:  # noqa: BLE001 - an audit record must never fail a call
           pass
   ```

   The blanket catch is the ADR's total-emission rule. It is justified inline, not silently: this
   runs inside `authorize`, ahead of the denial and the handler, so any escape would fail an
   authorized call and replace ADR 0038/0039's client-facing error with something else.
8. Add `record_authorization` and `record_ownership_override`, each building its payload in the
   documented field order and calling `_emit` at `WARNING` (deny, and every override) or `INFO`
   (allow).
9. Add `_AuditHandler` and `install_audit_sink`:

   ```python
   class _AuditHandler(logging.Handler):
       def emit(self, record: logging.LogRecord) -> None:
           stream = sys.stderr
           if stream is None:
               return
           try:
               stream.write(record.getMessage() + "\n")
               stream.flush()
           except (OSError, ValueError):
               pass

   def install_audit_sink() -> None:
       logger = logging.getLogger(AUDIT_LOGGER_NAME)
       logger.propagate = False
       if not logger.handlers:
           logger.addHandler(_AuditHandler())
       if logger.level == logging.NOTSET:
           logger.setLevel(logging.INFO)
   ```

   `sys.stderr` resolved at emit time, `None` returning early, `OSError` and `ValueError` caught:
   the same three guards `server._warn` applies, for the same three reasons (#221).
10. Run `uv run pytest tests/unit/test_audit.py -q`. **Expect: all pass.**
11. Run `just static`. **Expect: exit 0, "All checks passed!" from ruff and ty.**
12. Commit: `feat(audit): add the authorization audit record and its stderr sink`.

### Acceptance

Spec tests 1–8, 8a, 9–13, 14, 14a, 14b, 14c, 15 pass. `audit.py` contains no `from .` or
`from hmc_mcp` import — assert it in test 8a's file by reading the source.

---

## Task 2 — `target_scope.denial_reason`

Modifies `src/hmc_mcp/target_scope.py` and `tests/unit/test_target_scope.py`. Behaviour-preserving:
the four-case selection moves into one function that both the message and the reason code read.

### Steps

1. Add spec tests 21 and 21a to `tests/unit/test_target_scope.py` as failing tests. Run
   `uv run pytest tests/unit/test_target_scope.py -q`. **Expect: `AttributeError: module
   'hmc_mcp.target_scope' has no attribute 'denial_reason'`.**
2. Add `denial_reason(security, extracted) -> Reason` holding the existing four-case order:
   UNREADABLE selector → `"target-selector-unreadable"`; `not security.exhaustive_targets` →
   `"target-unboundable"`; ABSENT selector → `"target-selector-absent"`; otherwise
   `"target-not-granted"`.
3. Refactor `target_denial` to `reason = denial_reason(security, extracted)` and branch on the
   code, one template per code. Do not duplicate the case selection.
4. Run `uv run pytest tests/unit/test_target_scope.py tests/app/test_target_authorization.py -q`.
   **Expect: all pass, including the pre-existing tests — the messages are unchanged.**
5. Run `just static`. **Expect: exit 0.**
6. Commit: `refactor(target-scope): give the denial case selection one owner`.

### Acceptance

Every pre-existing target-denial message test still passes unmodified. That is what makes this
behaviour-preserving rather than a rewrite.

---

## Task 3 — emit from `dispatch_scope.authorize`

Modifies `src/hmc_mcp/dispatch_scope.py`; creates `tests/app/test_authorization_audit.py`.

### Steps

1. Write `tests/app/test_authorization_audit.py` with the same autouse isolation fixture as
   Task 1, then spec tests 16–21a, 22–26, 27, 28, 31–35 as failing tests. Include the
   non-empty-capture precondition on 22–26. Run
   `uv run pytest tests/app/test_authorization_audit.py -q`. **Expect: failures asserting a record
   that is never emitted.**
2. In `authorize`, wrap only the `selected_connection` call:

   ```python
   try:
       connection = selected_connection(token, tool=name)
   except ConnectionScopeError:
       audit.record_authorization(
           policy=policy.name, tool=name, effect=security.effect,
           decision="deny", reason="configuration-unreadable",
           token=token, resolved=None, targets=None,
       )
       raise
   ```

3. After `extracted = selected_targets(...)`, build the audit targets once:

   ```python
   audited = tuple(
       audit.AuditTarget(
           kind=kind, argument=argument,
           state=("present" if isinstance(value, str)
                  else "absent" if value is ABSENT else "unreadable"),
           value=value if isinstance(value, str) else None,
       )
       for kind, argument, value in extracted
   )
   ```

4. Add one `record_authorization` call on the permit path (`reason="permitted"`,
   `decision="allow"`) and one on each denial path, with
   `reason=denial_reason(security, extracted)` for the target denial and
   `reason="connection-not-granted"` for the connection denial. Extract a local closure if
   `authorize` approaches the complexity limit.
5. Run `uv run pytest tests/app/test_authorization_audit.py tests/app/test_connection_authorization.py
   tests/app/test_target_authorization.py -q`. **Expect: all pass.**
6. Run `just static`. **Expect: exit 0.**
7. Commit: `feat(dispatch-scope): emit one audit record per authorization decision`.

### Acceptance

Spec tests 16–21a, 22–26, 27, 28, 31–35 pass. Every pre-existing authorization test still passes.

---

## Task 4 — converge the ownership override

Modifies `src/hmc_mcp/operations_lpar.py` and `tests/unit/test_ownership.py`. `Refs #268`.

### Steps

1. Rewrite `tests/unit/test_ownership.py::test_authorize_lpar_mutation_override_is_audited` as
   spec test 26a, asserting a JSON record on `hmc_mcp.audit` and **no** record on
   `hmc_mcp.operations_lpar`. Repoint its sibling
   `…_normal_access_has_no_override_audit` at the new logger — against the old name it would pass
   vacuously. Add 26b–26e. Run `uv run pytest tests/unit/test_ownership.py -q`. **Expect: the
   rewritten tests fail; the record is still on the old logger.**
2. Replace `_audit_lpar_ownership_override`'s body:

   ```python
   def _audit_lpar_ownership_override(
       hmc: HMCClient, system_name: str, lpar_name: str
   ) -> None:
       audit.record_ownership_override(
           system=system_name,
           lpar=lpar_name,
           agent_id=hmc.config.agent_id or "hmc-mcp",
       )
   ```

   The two call sites are untouched. `_logger` stays — six other call sites in the file use it.
3. Run `uv run pytest tests/unit/test_ownership.py -q`. **Expect: all pass.**
4. Run `just static`. **Expect: exit 0.**
5. Commit: `refactor(operations-lpar): converge the override record onto the audit sink`.

### Acceptance

Spec tests 26a–26e pass. No `extra=`-based audit emission remains in the package: assert it by
grepping `src/hmc_mcp` for `hmc_agent_id`.

---

## Task 5 — install the sink, document it, and prove it live

Modifies `src/hmc_mcp/server.py`, `README.md`; creates `docs/authorization-audit.md` and
`tests/app/test_authorization_audit_live.py`.

### Steps

1. Write `tests/app/test_authorization_audit_live.py` implementing the spec's Run A (L1–L4) and
   Run B (L5), including the fixture that derives the config directory from
   `hmc_mcp.config.config_dir()` **after** redirecting `HOME`, deletes `HMC_HOST`, `HMC_PROFILE`,
   `XDG_CONFIG_HOME` and `APPDATA` from the child environment, and asserts both files exist and
   those four variables are absent before the first frame. Mark Run B POSIX-only. Run
   `uv run pytest tests/app/test_authorization_audit_live.py -q`. **Expect: failure — no record on
   stderr, because the sink is not installed on the serve path yet.**
2. Add `install_audit_sink()` to `server._serve_application`, immediately before its existing
   `_warn(...)` call.
3. Run `uv run pytest tests/app/test_authorization_audit_live.py -q`. **Expect: all pass, or skip
   on non-POSIX for Run B.**
4. Write `docs/authorization-audit.md`: both record shapes, the reason-code table, the logger name,
   the level split, how to route or silence, the merged-descriptor caveat, the
   `<default>`/`<unresolved>` collision, and the instruction to skip a non-parsing line.
5. Add the descriptor-merge caveat and a pointer to that document beside README's existing
   "never stdout" sentence in the startup-warnings section.
6. Run `just verify` **bare**. **Expect: exit 0; pytest green, coverage at or above the 90% floor,
   smoke reporting the tool count, build and artifact validation clean.**
7. Commit: `feat(server): install the audit sink on both serve transports`.

### Acceptance

A13's live proof passes on the branch head. `just verify` exits 0. `docs/authorization-audit.md`
describes what the code does — no phantom features.

---

## Task 6 — mutation verification

No production change. Applies M1–M10 one at a time, records which test ids redden, reverts each.

### Steps

1. For each of M1–M10 in the spec's table: apply the mutation with an editor, run the test ids in
   its "must redden" column, record the observed result, then `git restore` the file.
2. **Any mutation that reddens nothing is a finding**, not a pass — it means the test asserts a
   structural property while claiming to prove a redaction. Fix the test or reword the claim, and
   say which in the PR body.
3. Confirm the tree is clean: `git status --short`. **Expect: empty.**
4. Run `just verify` bare. **Expect: exit 0.**
5. Put the mutation/test-id table in the PR body. Nothing is committed by this task.

### Acceptance

Every row of M1–M10 has an observed result. A5 and charter criterion 6 have an artifact rather
than an assurance.

---

## Rollback

Every task is one commit on `feat/audit-events-224`. Nothing migrates, nothing persists, no
public tool contract changes, and no schema moves: `git revert` of the range restores the previous
behaviour exactly. The one externally visible change a revert would undo is the `hmc_mcp.audit`
logger and the ownership record's shape — neither has shipped in any release.
