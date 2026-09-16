# Plan: translate `httpx.InvalidURL` at the `_request` waist

**Goal.** `HMCClient._request` refuses a URL httpx will not build as `HMCError`,
so no supported entry point raises a third-party exception type for it.

**Architecture.** `_request` (`src/hmc_mcp/client/core.py`) is the single waist
every REST call passes through. It already refuses a malformed path form before
its `try` (`_reject_dot_segments`, raising `HMCError`) and translates two httpx
families inside it (both to `HMCTransportError`). This adds a third handler,
raising `HMCError` to match the sibling refusal.

**Tech stack.** Python 3.11 (`.python-version`), httpx 0.28.1 (locked), pytest.
**Design.** [Spec](../specs/2026-09-15-invalid-url-at-the-request-waist-design.md),
[ADR 0148](../../adr/0148-unbuildable-request-urls-raise-hmc-error.md), issue #826.

## Global Constraints

- Bootstrap or re-sync a worktree with `just setup` only; never a bare `uv sync`,
  `uv run` or `uv add`, which prune the `app` extra (AGENTS.md).
- `just verify` is the full pre-push gate; `uv run --no-sync prek run --all-files`
  covers the hook step CI runs and `just verify` does not.
- `hmc_mcp.api` exports exactly six names (ADR 0118) — add no seventh. ADR 0148's
  filename and H1 must both carry `0148`. Line length 100 (ruff). No new
  dependency.

Expected implementation size: 40–60 changed lines (S) — a 6-line `except` branch
in `core.py`, a test section of roughly 30 lines, one import line, and three
existing comments corrected in place.

## Task 1 — refuse an unbuildable URL as `HMCError`

**Interfaces.** Consumes nothing and defines no new name: `HMCError` is already
imported in `src/hmc_mcp/client/core.py` and exported by `hmc_mcp.api`.
**Files.** Modifies that file's `HMCClient._request` and
`tests/unit/test_request_path_safety.py` (new section at end, one import line,
three comment corrections). This is the whole change.

**Verification inventory.**

- *The waist's exception contract for an unbuildable URL.* Mode: focused-test.
  Observable: the type and message of the exception escaping
  `await client._request("GET", path)`. Test: `tests/unit/test_request_path_safety.py::test_a_url_httpx_refuses_to_build_is_refused_as_an_hmc_error`.
  Red before the source edit: `httpx.InvalidURL` propagates, so
  `pytest.raises(HMCError)` fails with it. Green:
  `uv run --no-sync pytest tests/unit/test_request_path_safety.py -k httpx_refuses_to_build -q`
- *The encoders still keep ADR 0145's and ADR 0146's values off the new handler.*
  Mode: focused-test. `test_a_group_value_reaches_the_query_string_percent_encoded`
  and `test_a_quick_property_name_cannot_re_point_the_request` stay green,
  assertions unchanged. Green:
  `uv run --no-sync pytest tests/unit/test_request_path_safety.py -q`
- *ADR 0148's number matches its filename and H1.* Mode: focused-test. Green:
  `just adr-numbering`, exit 0 with no filenames printed.

**Steps.**

1. Read `_request` in `src/hmc_mcp/client/core.py` and locate its `try` block,
   which begins immediately after the `_reject_dot_segments(method, path)` call.

2. In `tests/unit/test_request_path_safety.py`, change `from hmc_mcp.errors
   import HMCError` to `from hmc_mcp.errors import HMCError, HMCTransportError`.

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


   @pytest.mark.parametrize("path", _UNBUILDABLE_PATHS)
   def test_a_url_httpx_refuses_to_build_is_refused_as_an_hmc_error(path):
       """The waist's exception contract holds for a URL httpx will not build.

       `not HMCTransportError` is the assertion that can fail on its own: that
       type subclasses `HMCError`, three call sites retry elsewhere on it, and it
       is what a future httpx giving `InvalidURL` a shared base would produce
       here (ADR 0148). The message is asserted free of the CR, LF or tab httpx
       rejected, because it reaches logs.
       """
       client = _client()

       with pytest.raises(HMCError) as error:
           asyncio.run(client._request("GET", path))

       assert not isinstance(error.value, HMCTransportError)
       message = str(error.value)
       assert message.startswith("GET refused:")
       assert not set(message) & set("\r\n\t")
   ```

4. Run the green command from the inventory above and confirm the expected red:
   all four cases fail with `httpx.InvalidURL` raised instead of `HMCError`.

5. In `_request`, insert this handler as the first `except`, immediately after
   the `return await _read_bounded_response(...)` line:

   ```python
           except httpx.InvalidURL as exc:
               # Not the path: it holds the character httpx refused and this
               # message reaches logs. httpx's reason names that character
               # through `!r`, so it renders escaped (ADR 0148).
               raise HMCError(
                   f"{method.upper()} refused: the request URL could not be built. {exc}"
               ) from exc
   ```

6. Re-run the command from step 4 and confirm all four cases pass.

7. Correct the three comments that stated this gap in prose, leaving every
   surrounding assertion untouched: each clause claiming the exception escapes
   `_request`'s handlers becomes a pointer to the new section. The sites are the
   comment above `_GROUP_VALUES`, the one inside
   `test_a_group_value_reaches_the_query_string_percent_encoded`, and the one in
   `test_a_quick_property_name_cannot_re_point_the_request`'s parameters.

8. Run `just lint`, `just typecheck` and `just test` — exit 0 from each — then
   commit: `fix(client): refuse an unbuildable request URL as HMCError`.

**Acceptance criteria.** `_request` has three `except` branches, the
`httpx.InvalidURL` one raising `HMCError` and interpolating no `path`; the new
test asserts the exception type, the non-`HMCTransportError` type, the method in
the message and the absence of CR, LF and tab from it, over all four unbuildable
paths; no comment in the test module still claims the exception escapes
`_request`; `just verify` and `uv run --no-sync prek run --all-files` both exit 0.
**Rollback.** Revert the commit; no state or dependency changes. **Deferrals.**
None.
