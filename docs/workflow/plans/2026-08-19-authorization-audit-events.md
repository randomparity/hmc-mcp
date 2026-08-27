# Implementation plan — structured authorization audit events (#224)

**Goal.** Emit one structured, redacted, single-line JSON record per authorization decision at the
MCP dispatch boundary, and converge the package's second audit emitter onto the same record.

**Architecture.** A new `src/hmc_mcp/audit.py` owns the record vocabulary, its rendering, and its
sink. `dispatch_scope.authorize` — the only place in the package that reaches an authorization
decision — emits exactly one record per decision. `target_scope` gains `denial_reason` so the
message and the reason code have one owner. `operations.lpar`'s ADR 0011 override record moves
onto the same logger. `server._serve_application` installs the sink.

**Tech stack.** Python 3.11+, stdlib `logging` and `json` only. No new dependency (epic #218
requirement 11).

Source of truth: [the design spec](../specs/2026-08-19-authorization-audit-events-design.md) and
[ADR 0040](../../adr/0040-authorization-audit-events.md). Where the two disagree **about
behaviour**, the spec wins and this plan is wrong. Identifiers in this plan — file paths, symbol
names, existing test names — were checked against this checkout, so where an identifier differs
the plan is the one that was verified.

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
- **Focused pytest runs pass `--no-cov`.** `pyproject.toml` sets
  `addopts = "--cov=hmc_mcp --cov-report=term-missing"` with `fail_under = 90` (ADR 0034), so a
  single-file run measures package coverage at a few percent and **exits 1 even when every test
  passes** — verified in this checkout: `pytest tests/unit/test_target_scope.py -q` prints
  `44 passed` and exits 1; with `--no-cov` it exits 0. Only the `just verify` runs in Tasks 5 and 6
  measure the floor, which is the only place it means anything.
- Exit codes are read **bare**, never through a pipe. These hosts run zsh, where `${PIPESTATUS[0]}`
  is empty (the array is `pipestatus`, 1-indexed), so a piped check silently reports 0.
- **Every sentinel credential literal carries `# pragma: allowlist secret`.** `just secrets` runs
  `detect-secrets-hook` over every tracked file against `.secrets.baseline`, its `KeywordDetector`
  fires on exactly this shape, and `just secrets` is inside `just static` *and* the prek commit
  hook — so an unmarked sentinel password blocks the commit, not just the gate. The convention is
  already in the tree (`tests/app/test_connection_authorization.py`, `tests/unit/test_config.py`,
  `tests/fixtures/config.example.toml`). Where a sentinel sits inside a multi-line TOML blob that
  cannot carry an inline comment, build the blob from f-string parts so the literal can.
- Conventional commits, imperative subject ≤72 chars, `Co-Authored-By: Claude Opus 5 (1M context)
  <noreply@anthropic.com>` trailer. Never squash; this branch is code.

## Files

| path | action | answerable for |
|---|---|---|
| `src/hmc_mcp/audit.py` | create | record vocabulary, rendering, truncation, the two emitters, the sink |
| `src/hmc_mcp/target_scope.py` | modify | `denial_reason` added; `target_denial` refactored to read it |
| `src/hmc_mcp/dispatch_scope.py` | modify | assemble and emit one record per decision |
| `src/hmc_mcp/operations/lpar.py` | modify | `_audit_lpar_ownership_override` body calls `audit` |
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

1. Add the isolation fixture to **`tests/conftest.py`** as `autouse=True`, covering the whole
   suite. Not a per-file import: `from audit_isolation import isolate_audit_logging` binds a name
   the module never references, which is ruff `F401` — this checkout has no `[tool.ruff]` section,
   so the default rule set applies and `just lint` fails in all three files. And not three files
   either: once Task 4 converges the override, **six** further test files emit an audit record by
   driving `ownership_override=True` (`tests/lpar/test_decommission_tool.py`,
   `tests/lpar/test_lpar_description.py`, `tests/app/test_server_tools.py`,
   `tests/app/test_capabilities.py`, `tests/unit/test_destructive_scope.py` among them), so any
   enumeration of "files that need it" is a list that goes stale. One autouse definition in
   `conftest.py` needs no enumeration. Write it before any test — it is the precondition for
   everything else:

   ```python
   @pytest.fixture(autouse=True)
   def _isolate_logging():
       logger = logging.getLogger(audit.AUDIT_LOGGER_NAME)
       saved = (list(logger.handlers), logger.level, logger.propagate,
                list(logging.root.handlers))
       logger.handlers[:] = []            # reset, not merely restore - see below
       logger.setLevel(logging.NOTSET)
       logger.propagate = True
       try:
           yield
       finally:
           logger.handlers[:] = saved[0]
           logger.setLevel(saved[1])
           logger.propagate = saved[2]
           logging.root.handlers[:] = saved[3]
   ```

   **It resets at setup as well as restoring at teardown**, because after Task 5 the audit tests
   are no longer the only callers of `install_audit_sink`. `server._serve_application` calls it,
   and four existing tests drive that function in-process —
   `tests/app/test_capability_ceiling.py:489,504,553` and
   `tests/app/test_connection_authorization.py:418`. Any of those running first leaves
   `propagate = False` and a stderr handler attached for the rest of the session, and a fixture
   that only *restores* would faithfully restore that contamination: every `caplog`-based audit
   assertion would then capture nothing and pass vacuously. Resetting at setup is what makes each
   audit test start from the pristine tree the spec's isolation section describes.

2. Write spec tests 1–6, **6b**, 7, 8, 8a, **8b**, 9–13, 14, 14a, 14b and 15 as failing tests.
   **14c is Task 3's**, not this task's: it asserts that a raising renderer and a raising logger
   leave `dispatch_scope.authorize`'s outcome and exception type unchanged, which is unobservable
   until Task 3 wires emission into `authorize`.

   **Test 14b asserts record *content*, never the absence of an exception.** Step 7's guard
   deliberately swallows everything, so "`record_authorization` returned `None`" is true of the
   correct code *and* of code mutated by M7 — the record simply vanishes. 14b must emit a record
   whose selectors are `ABSENT` and `UNREADABLE`, capture the emitted line, and assert it parses
   and carries `state` of `"absent"`/`"unreadable"` with `value: null`. That is the only form M7
   can redden. The same reasoning applies to any test written against a guarded path: a totality
   guard turns "did not raise" into a claim about nothing.
   6b and 8b are called out because an earlier draft of this plan lost them between the task
   lists: 6b pins the `<unresolved>` profile-name collision, and 8b is the scan that makes A4 an
   invariant rather than a three-value sample. 6a is **Task 2's** — it needs `audit_state`, which Task 2 adds. Run
   `uv run --no-sync pytest --no-cov tests/unit/test_audit.py -q`. **Expect: collection error, `No module named
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
5. Add `_connection(token, resolved)` returning the `connection` object: `state` is `"absent"` for
   `None` or `""`, `"present"` for any other `str`, `"unreadable"` otherwise; `selector` is
   `_value(token)`; `resolved` is the argument unchanged — `None` already means "never resolved",
   so no third parameter is needed to say so.
6. Add `_attribution(claim: Any, source: str)` returning
   `{"claim": _value(claim), "source": source, "verified": False}`. **One builder, two call sites**,
   because the two records genuinely read different things and the two-value `source` vocabulary is
   the point: `record_authorization` passes
   `(os.environ.get(ATTRIBUTION_ENV), f"environment:{ATTRIBUTION_ENV}")` and
   `record_ownership_override` passes `(agent_id, "config:agent_id")`. A no-argument builder could
   not produce the override's attribution at all.
7. Add `_emit(level, build)` — taking the **builder**, not the built payload, so the guard covers
   both halves of the rule. ADR 0040 says "building one and writing one are both total"; a guard
   wrapped around `json.dumps` alone leaves every `_value`, `_connection` and `_attribution` call
   outside it, and those run while the payload is being assembled:

   ```python
   def _emit(level: int, build: Callable[[], dict[str, Any]]) -> None:
       try:
           message = json.dumps(build(), ensure_ascii=True)
           logging.getLogger(AUDIT_LOGGER_NAME).log(level, message)
       except Exception:  # noqa: BLE001 - an audit record must never fail a call
           pass
   ```

   The blanket catch is the ADR's total-emission rule. It is justified inline, not silently: this
   runs inside `authorize`, ahead of the denial and the handler, so any escape would fail an
   authorized call and replace ADR 0038/0039's client-facing error with something else.
8. Add `record_authorization` and `record_ownership_override`. Each passes `_emit` a
   zero-argument closure that builds its payload in the documented field order — so every helper
   call (`_value`, `_connection`, `_attribution`) runs **inside** the guard — at `WARNING` (deny,
   and every override) or `INFO` (allow). `record_authorization` renders **each `AuditTarget.value` through `_value`**, and
   `record_ownership_override` renders `system` and `lpar` through it: truncation has exactly one
   owner, inside `audit`, and `AuditTarget` stays a transport for the raw extraction rather than a
   place a caller could forget to bound. Without this the target values — one of the three
   caller-supplied classes — reach the record untruncated.
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
10. Run `uv run --no-sync pytest --no-cov tests/unit/test_audit.py -q`. **Expect: all pass.**
11. Run `just static`. **Expect: exit 0, "All checks passed!" from ruff and ty.**
12. Record each spec-numbered test's pytest node id beside its number in a comment block at the
    top of `tests/unit/test_audit.py`. Task 6 reads it.
13. Commit: `feat(audit): add the authorization audit record and its stderr sink`.

### Acceptance

Spec tests 1–6, 6b, 7, 8, 8a, 8b, 9–13, 14, 14a, 14b, 15 pass — **not 6a, which is Task 2's**.
`audit.py` contains no `from .`
or `from hmc_mcp` import — assert it in test 8a's file by reading the source.

**Inventory check before moving on:** the union of the task lists must cover every numbered item
in the spec's Testing section exactly once. Run it as a checklist, not from memory — losing three
items is how this task's list was wrong the first time.

---

## Task 2 — `target_scope.denial_reason`

Modifies `src/hmc_mcp/target_scope.py` and `tests/unit/test_target_scope.py`. Behaviour-preserving:
the four-case selection moves into one function that both the message and the reason code read.

### Steps

1. Add spec tests 21, 21a and **6a** to `tests/unit/test_target_scope.py` as failing tests. **This
   task owns them**, not Task 3: both are function-level assertions about `denial_reason` and
   `targets_permitted` agreeing arm by arm, with no application involved. The spec files them under
   its Boundary heading, which is where the ambiguity came from. Run
   `uv run --no-sync pytest --no-cov tests/unit/test_target_scope.py -q`. **Expect: `AttributeError: module
   'hmc_mcp.target_scope' has no attribute 'denial_reason'`.**
2. Add `from .audit import Reason, State` to `target_scope`'s imports, then two functions.
   First `audit_state(value: str | _Unresolved) -> State`, beside `_value` where the two singletons
   already live: `"present"` for a `str`, `"absent"` for `ABSENT`, `"unreadable"` for `UNREADABLE`.
   This is spec test 6a's seam and Task 3's generator calls it — without it the mapping is an
   inline conditional in `dispatch_scope`, one module away from the singletons it interprets, and
   6a has nothing to assert against. It mirrors `denial_reason` importing `Reason`.
   Then `denial_reason(security, extracted) -> Reason` holding the existing four-case order:
   UNREADABLE selector → `"target-selector-unreadable"`; `not security.exhaustive_targets` →
   `"target-unboundable"`; ABSENT selector → `"target-selector-absent"`; otherwise
   `"target-not-granted"`.
3. Refactor `target_denial` to `reason = denial_reason(security, extracted)` and branch on the
   code, one template per code. Do not duplicate the case selection.
4. Run `uv run --no-sync pytest --no-cov tests/unit/test_target_scope.py tests/app/test_target_authorization.py -q`.
   **Expect: all pass, including the pre-existing tests — the messages are unchanged.**
5. Run `just static`. **Expect: exit 0.**
6. Record the node ids for tests 21 and 21a in a comment at the top of
   `tests/unit/test_target_scope.py`.
7. Commit: `refactor(target-scope): give the denial case selection one owner`.

### Acceptance

Every pre-existing target-denial message test still passes unmodified. That is what makes this
behaviour-preserving rather than a rewrite.

---

## Task 3 — emit from `dispatch_scope.authorize`

Modifies `src/hmc_mcp/dispatch_scope.py`; creates `tests/app/test_authorization_audit.py`.

### Steps

1. Write `tests/app/test_authorization_audit.py` with the same autouse isolation fixture as
   Task 1, then spec tests **16–20, 14c, 22–26, 27, 28, 31–35** as failing tests. 21 and 21a are
   Task 2's; 14c moves here because it needs `authorize` to emit. Include the non-empty-capture
   precondition on 22–26, and mark every sentinel credential literal
   `# pragma: allowlist secret` — see the note in Task 5 step 1.

   **Test 31 needs the same isolation test 26d needs, for the same reason.** Its claim is that a
   record at `INFO` with no sink installed does not reach stderr, and the mechanism is
   `logging.lastResort`'s level filter — which `Logger.callHandlers` consults only after finding
   zero handlers on the whole ancestor walk. pytest's logging plugin keeps a `LogCaptureHandler` on
   the root logger, so under the default harness `lastResort` is never reached and the test passes
   for an unrelated reason. Clear `logging.root.handlers` for the duration and read `capsys`, or
   drive it as a subprocess. Run
   `uv run --no-sync pytest --no-cov tests/app/test_authorization_audit.py -q`. **Expect: failures asserting a record
   that is never emitted.**
2. Set `dispatch_scope`'s imports to exactly this — the final set, not a delta, so an unused name
   cannot survive as a ruff `F401` and a missing one cannot survive as a `NameError`:

   ```python
   from . import audit
   from .access_policy import AccessPolicy
   from .connection_scope import (
       ConnectionScopeError, connection_denial, connection_permitted, selected_connection,
   )
   from .target_scope import (
       audit_state, denial_reason, selected_targets, target_denial, targets_permitted,
   )
   from .tool_registry import Authorize, ToolSecurity
   ```

   `ConnectionScopeError` is the easy one to miss: `selected_connection` already comes from that
   module but the exception does not. `ABSENT` is deliberately **not** imported — step 3's
   generator calls `audit_state` instead. Then wrap only the `selected_connection` call:

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
           state=audit_state(value),
           value=value if isinstance(value, str) else None,
       )
       for kind, argument, value in extracted
   )
   ```

   `audit_state` comes from `target_scope` (Task 2), which owns the singletons it interprets, so
   `ABSENT` no longer needs importing here.

4. Add one `record_authorization` call on the permit path and one on each denial path. Write the
   full keyword set — in particular `resolved=`, which is the one argument needing a helper the
   implementer has had no other reason to call:

   ```python
   # permit path, immediately before `return`
   audit.record_authorization(
       policy=policy.name, tool=name, effect=security.effect,
       decision="allow", reason="permitted",
       token=token, resolved=audit.resolved_connection(connection), targets=audited,
   )
   # target denial, immediately before `raise target_denial(...)`
   audit.record_authorization(
       policy=policy.name, tool=name, effect=security.effect,
       decision="deny", reason=denial_reason(security, extracted),
       token=token, resolved=audit.resolved_connection(connection), targets=audited,
   )
   # connection denial, immediately before `raise connection_denial(...)`
   audit.record_authorization(
       policy=policy.name, tool=name, effect=security.effect,
       decision="deny", reason="connection-not-granted",
       token=token, resolved=audit.resolved_connection(connection), targets=audited,
   )
   ```

   Extract a local closure over the four constant keywords if `authorize` approaches the
   complexity limit of 8 or the line limit of 100.
5. Run `uv run --no-sync pytest --no-cov tests/app/test_authorization_audit.py tests/app/test_connection_authorization.py
   tests/app/test_target_authorization.py -q`. **Expect: all pass.**
6. Run `just static`. **Expect: exit 0.**
7. Record the node ids for this task's spec-numbered tests at the top of
   `tests/app/test_authorization_audit.py`.
8. Commit: `feat(dispatch-scope): emit one audit record per authorization decision`.

### Acceptance

Spec tests 16–20, 14c, 22–26, 27, 28, 31–35 pass. Every pre-existing authorization test still
passes. Re-run the Task 1 inventory check now that three tests have moved between tasks.

---

## Task 4 — converge the ownership override

Modifies `src/hmc_mcp/operations/lpar.py` and `tests/unit/test_ownership.py`.

**`Refs #268`, deliberately not `Closes #268`.** The convergence does discharge #268's substance,
and the natural instinct is a closing keyword. The campaign orchestrator ruled otherwise:
`closingIssuesReferences` on this PR must stay exactly `[224]` so the merge check reads
unambiguously, and the orchestrator closes #268 by hand post-merge citing the commit. #271 already
carves out the one part convergence does not discharge — the override record still does not name
which HMC it applied to.

### Steps

1. Add the shared `isolate_audit_logging` autouse fixture from Task 1 to
   `tests/unit/test_ownership.py` — it reads the audit logger like the other two files and would
   otherwise inherit whatever `_serve_application` left behind once Task 5 lands. Then rewrite
   `tests/unit/test_ownership.py::test_authorize_lpar_mutation_override_is_audited` as
   spec test 26a, asserting a JSON record on `hmc_mcp.audit` and **no** record on
   `hmc_mcp.operations.lpar`, first asserting the capture is non-empty — an "absent" assertion is
   trivially true of an empty capture. Repoint its sibling
   `…_normal_access_has_no_override_audit` at the new logger — against the old name it would pass
   vacuously. Add 26b–26e. Run `uv run --no-sync pytest --no-cov tests/unit/test_ownership.py -q`. **Expect: the
   rewritten tests fail; the record is still on the old logger.**
2. Add `from . import audit` to `operations.lpar`'s imports, then replace
   `_audit_lpar_ownership_override`'s body:

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
3. Run `uv run --no-sync pytest --no-cov tests/unit/test_ownership.py -q`. **Expect: all pass.**
4. Run `just static`. **Expect: exit 0.**
5. Record the node ids for tests 26a–26e at the top of `tests/unit/test_ownership.py`.
6. Commit: `refactor(operations-lpar): converge the override record onto the audit sink`.

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
   those four variables are absent before the first frame.

   **Child environment and executable, settled here because the spec's wording invites two
   readings.** Copy `os.environ`, delete the four names, set `HOME` — a copy, not a from-scratch
   mapping. An explicitly built environment carries no `PATH`, and the child is the `hmc-mcp`
   console script (this package ships no `__main__.py`), so it would not be found at all. Resolve
   it once with `shutil.which("hmc-mcp")` and assert it is not `None` in the same setup block that
   asserts the two config files exist and the four variables are gone.

   **Mark the whole module POSIX-only** — `pytestmark = pytest.mark.skipif(os.name != "posix", ...)`
   — not just Run B. Both halves of the fixture are POSIX assumptions: `config_dir()` on win32
   resolves from `APPDATA` first and `Path.home()` there reads `USERPROFILE`, not `HOME`, so on
   Windows the **parent** would resolve `config_dir()` to the developer's real `%APPDATA%\hmc-mcp`
   and overwrite their `config.toml` — carrying the fixture's sentinel password — and their
   `access-policy.toml`. Deleting `APPDATA` from the *child* env does not protect the parent that
   writes the files.

   **Subprocess contract — this is the suite's first long-lived `hmc-mcp serve` child.** No
   existing test spawns one (`tests/app/test_serve.py` and `test_capability_ceiling.py` use
   `CliRunner` with `main_stdio`/`main_http` patched), and `pyproject.toml` configures no
   `pytest-timeout`, so a blocking read on a child that never answers hangs `just verify` and every
   CI leg with no diagnostic. Required: a bounded per-frame read deadline; a fixture that
   `terminate()`s then `kill()`s the child in `finally` and asserts it exited; and on deadline
   expiry, fail with the child's captured stderr attached rather than raising a bare timeout.

   **Note the red step is narrower than the task.** L5 is expected to *pass* before the sink is
   installed — with no sink, no record is written, which is what L5 asserts is harmless. The
   confirm-it-fails step covers L1–L4 only. Run
   `uv run --no-sync pytest --no-cov tests/app/test_authorization_audit_live.py -q`. **Expect: failure — no record on
   stderr, because the sink is not installed on the serve path yet.**
2. Add `install_audit_sink()` to `server._serve_application`, immediately before its existing
   `_warn(...)` call.
3. Run `uv run --no-sync pytest --no-cov tests/app/test_authorization_audit_live.py -q`. **Expect: all pass, or skip
   on non-POSIX for Run B.**
4. Write `docs/authorization-audit.md`: both record shapes, the reason-code table, the logger name,
   the level split, how to route or silence, the merged-descriptor caveat, the
   `<default>`/`<unresolved>` collision, and the instruction to skip a non-parsing line. It must
   also state that `attribution.claim` is the **raw** environment value — unvalidated, bounded at
   128 characters, JSON-escaped — and may therefore be wider and stranger than the 1-64 printable
   ASCII contract `docs/environment-variables.md` documents for `HMC_AGENT_ID`, which is
   `config.validate_agent_id`'s rule for configuration and is deliberately bypassed here.
   Without that sentence the two documents contradict each other.
5. Add the descriptor-merge caveat and a pointer to that document beside README's existing
   "never stdout" sentence in the startup-warnings section.
6. Run `just verify` **bare**. **Expect: exit 0; pytest green, coverage at or above the 90% floor,
   smoke reporting the tool count, build and artifact validation clean.**
7. Record the node ids for L1–L5 at the top of `tests/app/test_authorization_audit_live.py`.
8. Commit: `feat(server): install the audit sink on both serve transports`.

### Acceptance

A13's live proof passes on the branch head. `just verify` exits 0. `docs/authorization-audit.md`
describes what the code does — no phantom features.

---

## Task 6 — mutation verification

No production change. Applies M1–M10 one at a time, records which test ids redden, reverts each.

### Steps

0. **Prerequisite, discharged in Tasks 1–5, not here:** as each spec-numbered test is written, its
   pytest node id is recorded beside the number in a comment at the top of its file. Six of the ten
   mutation rows must-redden tests that Tasks 1 and 2 own — M3 needs 3 and 4, M4 needs 2, M5 needs
   15, M6 needs 14, M7 needs 14b, and 21/21a are Task 2's — so scoping this to Tasks 3–5 would
   leave most of the table unmappable. Each of Tasks 1–5 carries the instruction as its own final
   step. The spec's
   M1–M10 table holds spec numbers (`23, 24`, `14b, 17`, `15, L2`), not node ids, and only the
   person who just wrote them can map one to the other — which is exactly the perishable knowledge
   this task exists to turn into a durable artifact. Without that mapping Task 6 is not runnable by
   anyone else.
1. For each of M1–M10 in the spec's table: apply the mutation with an editor, run the node ids in
   its "must redden" column with `--no-cov`, record the observed result, then `git restore` the
   file. Confirm the restore actually reverted — `git diff --stat` empty — before the next row; a
   scripted edit that silently matched nothing is how a mutation "passes" without ever applying.
2. **Any mutation that reddens nothing is a finding**, not a pass — it means the test asserts a
   structural property while claiming to prove a redaction. Fix the test or reword the claim.
   **A test changed under this step is committed**, with a message naming the mutation that
   exposed it, so the remediation is in the history rather than folded silently into an earlier
   commit. If more than three of the ten rows need remediation, stop and report it: that is a
   signal the test suite is asserting structure throughout while claiming to prove redaction, and
   it is a design question rather than ten local edits.
3. Confirm the tree is clean: `git status --short`. **Expect: empty.**
4. Run `just verify` bare. **Expect: exit 0.**
5. Put the mutation/node-id table, with the observed result per row, in the PR body. Apart from
   any step-2 remediation commit, this task commits nothing.

### Acceptance

Every row of M1–M10 has an observed result. A5 and charter criterion 6 have an artifact rather
than an assurance.

---

## Rollback

Tasks 1–5 are one commit each on `feat/audit-events-224`; Task 6 commits only if step 2 finds
something. Nothing migrates, nothing persists, no
public tool contract changes, and no schema moves: `git revert` of the range restores the previous
behaviour exactly. The one externally visible change a revert would undo is the `hmc_mcp.audit`
logger and the ownership record's shape — neither has shipped in any release.
