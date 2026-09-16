# Plan: translate `httpx.InvalidURL` at the `_request` waist

**Goal.** `HMCClient._request` refuses a URL httpx will not build as `HMCError`,
so no supported entry point raises a third-party exception type for it.
**Architecture.** `_request` (`src/hmc_mcp/client/core.py`) is the single waist
every REST call passes through; this adds a third `except` branch to it. ADR 0148
holds the decision and its grounds.
**Tech stack.** Python 3.11 (`.python-version`), httpx 0.28.1 (locked), pytest.
**Design.** [Spec](../specs/2026-09-15-invalid-url-at-the-request-waist-design.md),
[ADR 0148](../../adr/0148-unbuildable-request-urls-raise-hmc-error.md), issue #826.

## Global Constraints

- Bootstrap or re-sync a worktree with `just setup` only; never a bare `uv sync`,
  `uv run` or `uv add`, which prune the `app` extra (AGENTS.md). `just verify` is
  the full pre-push gate; `uv run --no-sync prek run --all-files` covers the hook
  step CI runs and `just verify` does not.
- `hmc_mcp.api` exports exactly six names (ADR 0118) — add no seventh. ADR 0148's
  filename and H1 must both carry `0148`. Line length 100 (ruff). No dependency.

Expected implementation size: 50–75 changed lines (S) — a 6-line `except` branch
plus a docstring sentence in `core.py`, a ~35-line test section, one import line,
and three existing comments corrected in place.

## Task 1 — refuse an unbuildable URL as `HMCError`

**Interfaces.** Consumes nothing and defines no new name: `HMCError` is already
imported in `src/hmc_mcp/client/core.py` and exported by `hmc_mcp.api`.
**Files.** Modifies that file's `HMCClient._request` and
`tests/unit/test_request_path_safety.py` (new section at end, one import line,
three comment corrections) — the whole change.

**Verification inventory.**

- *The waist's exception contract for an unbuildable URL, at `_request` and
  through the public `get_uom_path`.* Mode: focused-test. Observable: the type
  and message each call raises. Test: the parametrized case added by step 3. Red
  before the source edit: `httpx.InvalidURL` propagates, so `pytest.raises` fails
  with it. Green:
  `uv run --no-sync pytest tests/unit/test_request_path_safety.py -k httpx_refuses_to_build -q`
- *The two existing handlers still translate timeouts and transport errors, and
  the encoders still keep ADR 0145's and ADR 0146's values off the new handler.*
  Mode: focused-test. The existing cases in `tests/unit/test_client.py` and
  `tests/unit/test_response_limits.py`, plus
  `test_a_group_value_reaches_the_query_string_percent_encoded` and
  `test_a_quick_property_name_cannot_re_point_the_request`, stay green unchanged.
  Green: `uv run --no-sync pytest tests/unit/test_request_path_safety.py tests/unit/test_client.py tests/unit/test_response_limits.py -q`
- *ADR 0148's number matches its filename and H1.* Mode: focused-test. Green:
  `just adr-numbering`, exit 0 with no filenames printed.
- *No comment still says the exception escapes `_request`.* Mode: focused-test.
  Green: `rg -c "escapes ._request" tests/unit/test_request_path_safety.py`
  exits 1 with no matches; before step 7 it exits 0 reporting two.

**Steps.**

1. In `src/hmc_mcp/client/core.py`, locate `_request`'s `try` block, which begins
   immediately after its `_reject_dot_segments(method, path)` call.
2. In `tests/unit/test_request_path_safety.py`, change `from hmc_mcp.errors import
   HMCError` to `from hmc_mcp.errors import HMCError, HMCTransportError`.
3. Append this section to the end of that file:

   ```python
   # ---------------------------------------------------------------------------
   # The unbuildable URL (ADR 0148)
   # ---------------------------------------------------------------------------

   # Exactly the raw values the encoding tests above describe in prose and, before
   # ADR 0148, could only describe: each carries a character httpx refuses.
   _UNBUILDABLE_PATHS = (
       "/rest/api/uom/LogicalPartition?group=None\rX-Evil: 1",
       "/rest/api/uom/LogicalPartition?group=None\nX-Evil: 1",
       f"/rest/api/uom/LogicalPartition/{UUID_A}/quick/PartitionState\r\nX-Evil: 1",
       f"/rest/api/uom/LogicalPartition/{UUID_A}\tX-Evil: 1",
   )

   # The waist, and the public method ADR 0145 and ADR 0146 recorded as still
   # reaching it: `get_uom_path` hands its path to `_get` unchanged.
   _UNBUILDABLE_CALLS = (
       ("_request", lambda c, p: c._request("GET", p)),
       ("get_uom_path", lambda c, p: c.get_uom_path(p, "LogicalPartition")),
   )


   @pytest.mark.parametrize(
       "call", [c for _, c in _UNBUILDABLE_CALLS], ids=[n for n, _ in _UNBUILDABLE_CALLS]
   )
   @pytest.mark.parametrize("path", _UNBUILDABLE_PATHS)
   def test_a_url_httpx_refuses_to_build_is_refused_as_an_hmc_error(call, path):
       """The waist's exception contract holds for a URL httpx will not build."""
       client = _client()

       with pytest.raises(HMCError) as error:
           asyncio.run(call(client, path))

       # `HMCTransportError` subclasses `HMCError`, so `pytest.raises` alone would
       # accept the classification ADR 0148 rejects. httpx's reason is carried,
       # and the cause chain with it, without pinning httpx's wording; the path is
       # not, because this message reaches logs and the path holds the control
       # character httpx rejected.
       assert not isinstance(error.value, HMCTransportError)
       message = str(error.value)
       assert message.startswith("GET refused:")
       assert str(error.value.__cause__) in message
       assert path not in message
       assert not set(message) & set("\r\n\t")
   ```

4. Run the green command from the inventory above and confirm the expected red:
   all eight cases fail with `httpx.InvalidURL` raised instead of `HMCError`.
5. In `_request`, insert this handler as the first `except`, immediately after
   the `return await _read_bounded_response(...)` line, and add one sentence to
   the docstring naming the refusal and citing ADR 0148 (it currently describes
   only transport normalization and the dot-segment guard):

   ```python
           except httpx.InvalidURL as exc:
               # Not the path: it holds the character httpx refused and this
               # message reaches logs. httpx's reason renders that character
               # through `!r`, so it comes back escaped (ADR 0148).
               raise HMCError(
                   f"{method.upper()} refused: the request URL could not be built. {exc}"
               ) from exc
   ```
6. Re-run the command from step 4 and confirm all eight cases pass.
7. Correct the three comments that stated this gap in prose — above
   `_GROUP_VALUES`, inside
   `test_a_group_value_reaches_the_query_string_percent_encoded`, and in
   `test_a_quick_property_name_cannot_re_point_the_request`'s parameters — so
   each points at the new section instead of claiming the escape. Leave every
   surrounding assertion untouched.
8. Run `just lint`, `just typecheck` and `just test` — exit 0 from each — then
   commit: `fix(client): refuse an unbuildable request URL as HMCError`.

**Acceptance criteria.** `_request` has three `except` branches, the
`httpx.InvalidURL` one raising `HMCError` and interpolating no `path`, and its
docstring names the refusal; the new test holds ADR 0148's contract over all four
unbuildable paths through both calls; the `rg` check above finds no match; and
`just verify` and `uv run --no-sync prek run --all-files` both exit 0.
**Rollback.** Revert the commit; no state or dependency changes. **Deferrals.**
None.
