# Publish MCP client conventions

**Goal:** Make final resource selector and asynchronous wait conventions visible to every MCP
client without changing tool behavior.

**Architecture:** Extend the single FastMCP `instructions` string constructed in `_app.py`. Pin
the public text through a focused test of `create_mcp().instructions`, so tests exercise the value
clients receive rather than an unrelated constant or source-text fragment.

**Tech stack:** Python 3.11+, FastMCP, pytest, uv, ruff, ty, prek.

## Global Constraints

- `*_name_or_uuid` accepts a resource name or UUID; `*_uuid` requires a UUID.
- SSH-passthrough tools resolve UUID selectors to HMC CLI names before command execution.
- On tools exposing common wait controls, `wait=False` is the default and returns the submitted
  job for later polling; `wait=True` polls to terminal completion.
- Install tools expose `wait_timeout_seconds=None`, deriving the client budget from
  `hmc_timeout_minutes` plus one poll interval. Other wait-capable tools default to
  `timeout_seconds=300`. `poll_interval=5` seconds is common.
- No runtime behavior, tool signature, dependency, migration, compatibility alias, or new tool.
- The host is `arm64`; declared targets are `amd64` and `arm64`; the relationship is `included`.

## Task 1: Pin and publish the client-visible conventions

**Files:**

- Create `tests/unit/test_mcp_instructions.py` for the client-visible instructions contract.
- Modify `src/hmc_mcp/_app.py` to add the conventions section.

**Interfaces:**

- Consumes `hmc_mcp._app.create_mcp() -> FastMCP` and its `instructions: str | None` attribute.
- Produces no new Python interface. MCP clients continue consuming FastMCP's instructions field.

**Steps:**

1. Create `tests/unit/test_mcp_instructions.py` with one test that calls `create_mcp()`, asserts
   `instructions` is not `None`, extracts the text between
   `## Resource addressing and asynchronous jobs` and `## Recommended workflows`, and compares
   the complete extracted block to one exact expected block containing the selector names,
   qualified timeout names and defaults, install-derived budget, `poll_interval=5`, and the
   `wait=False` submitted-job result. Assert the conventions heading precedes the workflows
   heading.
2. Run `uv run --no-sync pytest -q --no-cov tests/unit/test_mcp_instructions.py`. The focused
   run disables the repository-wide coverage threshold and should fail because the current
   instructions omit these conventions.
3. In `src/hmc_mcp/_app.py`, insert a `## Resource addressing and asynchronous jobs` section
   between composite tools and recommended workflows. State every rule in Global Constraints,
   qualifying timeout rules by the tools that expose them.
4. Run `uv run --no-sync pytest -q --no-cov tests/unit/test_mcp_instructions.py`. Expect all tests
   in the file to pass; the full suite in the next step enforces repository-wide coverage.
5. Run `just verify`. Expect all static checks, tests, smoke checks, builds, artifact checks, and
   CLI loading checks to pass.
6. Run `UV_NO_SYNC=1 uv run prek run --all-files`. Expect every hook to pass.
7. Commit the test and instruction update with the imperative Conventional Commit subject
   `docs(server): publish MCP client conventions`.

**Acceptance criteria:** The constructed FastMCP application exposes all final addressing and
wait semantics specified above, tests pin the actual client-visible value, and no runtime behavior
changes. Rollback is a normal `git revert` of the implementation commit; no cleanup or external
state is involved.

## Delivery

Review the complete branch against `main`, simplify only if doing so preserves the instruction
contract, rerun both guardrails, and open a PR that closes #148. Stop at green CI and a mergeable
PR; do not merge.
