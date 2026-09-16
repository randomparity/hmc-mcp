# Plan: govern `_lpar_job`'s operation segment

**Goal.** Refuse an `operation` outside the five LPM job operations this client submits, before
the uom path is built.

**Architecture.** `src/hmc_mcp/client/client_lpm.py` gains a module-level frozenset and one
membership check at the top of `LpmMixin._lpar_job`, which runs before `self.submit_job(...)` and
so before `core.HMCClient._request` builds anything. `tests/unit/test_request_path_safety.py`
gains the refusal's tests. No other file changes. Stack: Python 3.11+, `httpx`, `pytest`, `respx`,
`uv`, `just`. Spec: `docs/workflow/specs/2026-09-16-lpar-job-operation-design.md`. Record:
`docs/adr/0151-lpar-job-operation-is-a-closed-set.md`. Expected implementation size: 45–70
changed lines (S) — from the two files above: ~11 lines of source and ~59 of test.

## Global Constraints

- Bootstrap with `just setup` only; bare `uv sync` / `uv run` / `uv add` prune the `app` extra
  (`typer`, `cyclopts`) and break `just typecheck`.
- Guardrails: `just verify` **and** `uv run --no-sync prek run --all-files`, both exit 0, run bare
  — no `| tail`, no `>/dev/null`, no `|| true`. A focused `pytest -k` run exits 1 on the 90.5%
  `fail-under` coverage gate even when every selected test passes, so focused commands pass
  `--no-cov`.
- A refusal message names the argument and the permitted set, never the value (ADR 0143/0150).
  Outside the frozen surface, do not edit: `client_contracts.py`, `core.py`,
  `tests/lpar/test_lpm.py`, `_uom_path_sites()`.

## Task 1 — refuse an unlisted operation

**Interfaces.** Consumes `JobClient.submit_job(job_path: str, job_request_xml: str) ->
dict[str, Any] | None` (`client_contracts.py:350-352`), unchanged. Defines module-level
`_LPAR_JOB_OPERATIONS: frozenset[str]` in `client_lpm.py`. `_lpar_job`'s signature
`(self: LpmClient, lpar_uuid: str, operation: str, job_xml: str)` is unchanged, so the
`LpmClient` protocol needs no edit. Later tasks: none.

**Verification.** `FOCUSED` is
`uv run --no-sync pytest tests/unit/test_request_path_safety.py -k lpm_operation --no-cov -q`.
Red at step 2 is not `DID NOT RAISE`: unfixed, the call reaches `_request`, so eight of the nine
cases fail as `AssertionError: a refused LPM operation reached the transport` and
`../../web/HmcUser/root` fails as an uncaught `HMCError` from the waist guard — `HMCError`
subclasses `Exception`, not `ValueError` (`src/hmc_mcp/errors.py:17`).

- Contract: `_lpar_job` refuses an `operation` outside `_LPAR_JOB_OPERATIONS` before any request
  is built. Mode: `focused-test`. Observable: `ValueError`, `_http.build_request` never called.
  Test: `test_an_unlisted_lpm_operation_is_refused_before_any_request`. Green: `FOCUSED`.
- Contract: the message names the permitted set, not the value. Mode: `focused-test`. Observable:
  the message equals the step-1 string and excludes the rejected one. Test:
  `test_the_lpm_operation_refusal_names_the_permitted_set_not_the_value`. Green: `FOCUSED`.
- Contract: the five existing paths are unchanged on the wire. Mode: `focused-test`. Observable:
  the `respx` routes at `tests/lpar/test_lpm.py:102-154` still record a call on each exact path.
  Test: those five cases, unedited. Green: `uv run --no-sync pytest tests/lpar/test_lpm.py --no-cov -q`.

**Steps**

1. Append to `tests/unit/test_request_path_safety.py`, which already imports `asyncio` and
   `pytest` and defines `_client()` and `UUID_A` — add no import:

```python
# ---------------------------------------------------------------------------
# The LPM job operation segment (ADR 0151)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "operation",
    [
        # The `?`/`#` retargeting pair issue #809 carries, a second segment, a
        # traversal, the case a character grammar would have accepted (`PowerOff`
        # is real and not submitted here), casing, empty, and a CRLF value httpx
        # will not build a URL from (ADR 0148) -- refused here first, and so as a
        # `ValueError` rather than the waist's `HMCError`.
        "Migrate?group=None",
        "Migrate#/rest/api/web/HmcUser/root",
        "Migrate/extra",
        "../../web/HmcUser/root",
        "PowerOff",
        "migrate",
        "",
        "Migrate\r\nX-Evil: 1",
    ],
)
def test_an_unlisted_lpm_operation_is_refused_before_any_request(operation):
    """Refused by membership, before `submit_job` builds anything. Asserted
    against the transport too: a refusal that still built a request would leave
    the retargeted path in the HMC's audit log."""
    client = _client()
    sent: list[str] = []

    def _forbidden(*args, **kwargs):
        sent.append("request")
        raise AssertionError("a refused LPM operation reached the transport")

    client._http.build_request = _forbidden  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="^LPM job operation must be one of: "):
        asyncio.run(client._lpar_job(UUID_A, operation, "<JobRequest/>"))
    assert sent == []


def test_the_lpm_operation_refusal_names_the_permitted_set_not_the_value():
    """Equality, not a substring: dropping an operation from the set without
    dropping its call site cannot pass here. The transport is patched because
    unfixed this call would otherwise attempt a real request (ADR 0151)."""
    client = _client()

    def _forbidden(*args, **kwargs):
        raise AssertionError("a refused LPM operation reached the transport")

    client._http.build_request = _forbidden  # type: ignore[method-assign]

    with pytest.raises(ValueError) as error:
        asyncio.run(client._lpar_job(UUID_A, "secret-partition-name", "<x/>"))

    message = str(error.value)
    assert message == (
        "LPM job operation must be one of: Migrate, MigrateAbort, "
        "MigrateRecover, MigrateValidate, RemoteRestart"
    )
    assert "secret-partition-name" not in message
```

2. Confirm the expected red: `FOCUSED` → 9 failures, as described above.

3. In `src/hmc_mcp/client/client_lpm.py`, insert above `class LpmMixin:`:

```python
# The LPM job operations this client submits, and the whole of the namespace
# `_lpar_job` accepts. Membership rather than a character grammar, because unlike
# the uom *type* namespace (ADR 0143) this one is closed: `_lpar_job` is private
# and every caller below passes one of these literals (ADR 0151).
_LPAR_JOB_OPERATIONS = frozenset(
    {"Migrate", "MigrateValidate", "MigrateAbort", "MigrateRecover", "RemoteRestart"}
)
```

4. Replace `_lpar_job`'s body with:

```python
    async def _lpar_job(
        self: LpmClient, lpar_uuid: str, operation: str, job_xml: str
    ) -> dict[str, Any] | None:
        if operation not in _LPAR_JOB_OPERATIONS:
            allowed = ", ".join(sorted(_LPAR_JOB_OPERATIONS))
            raise ValueError(f"LPM job operation must be one of: {allowed}")
        return await self.submit_job(
            f"/rest/api/uom/LogicalPartition/{lpar_uuid}/do/{operation}", job_xml
        )
```

5. Confirm green: `FOCUSED` → `9 passed`. Then
   `uv run --no-sync pytest tests/lpar/test_lpm.py --no-cov -q` → all pass, that file unedited.

6. Run both guardrails bare, then commit source and test as one `fix(client):` commit.

**Acceptance criteria.** `_LPAR_JOB_OPERATIONS` holds exactly the five literals the call sites
pass; the three Verification contracts above hold; both guardrails exit 0. Rollback: revert.
