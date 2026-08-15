# MCP client convention instructions design

## Goal

Expose the repository's final resource-addressing and asynchronous wait conventions in the
FastMCP instructions that every connected MCP client receives. This is a documentation contract
change only: tool signatures, validation, submission, and polling behavior remain unchanged.

## Scope and authority

Issue #148 requires the client-visible instructions to explain resource selectors and the common
wait controls after issue #147 settled their names. Accepted ADR 0025 is the authority for the
renamed installation timeout, while the registered tool schemas on current `main` establish the
remaining final names and defaults.

The permitted implementation surface is `src/hmc_mcp/_app.py` and focused instruction contract
tests. Quest workflow records are permitted supporting artifacts. New tools, behavior changes,
compatibility aliases, summary tools, dependencies, migrations, and another ADR are excluded.

## Instruction contract

Add a short conventions section before the workflow recommendations so clients learn the shared
rules before applying them:

- A parameter ending in `*_name_or_uuid` accepts either the resource name or UUID.
- A parameter ending in `*_uuid` requires a UUID.
- SSH-passthrough tools accept those public selectors and resolve UUIDs to HMC CLI names before
  invoking the command.
- For tools exposing the common controls, `wait=False` is the default and returns the submitted
  job for later polling; `wait=True` polls for terminal completion.
- `wait_timeout_seconds` is the client-side polling budget for install tools and defaults to
  `None`, which derives the budget from `hmc_timeout_minutes` plus one poll interval.
- Other wait-capable tools use `timeout_seconds=300` by default.
- `poll_interval=5` is the default number of seconds between status requests.

The wording distinguishes the install-specific `wait_timeout_seconds` from the general
`timeout_seconds`; describing one universal timeout name or default would contradict the live
schemas. It also says the convention applies only when a tool exposes these controls.

## Data flow and failure handling

`create_mcp()` continues to construct FastMCP from `_new_mcp`. The new prose is part of the same
`instructions` string and therefore flows through FastMCP's normal server initialization to MCP
clients. There is no new runtime branch or failure mode. A stale or incomplete convention is
caught by tests that inspect the constructed application's actual `instructions` value.

## Testing

Add a focused test under `tests/unit/` that creates the MCP application and compares the complete
client-visible conventions block with one exact expected block. It also asserts that the block
appears before `## Recommended workflows`. The expected block pins the selector suffixes, wait
field names, qualified defaults, install-derived timeout meaning, and submitted-job behavior
together, so an omission, duplicate contradictory wording inside the section, or misplaced block
fails as one contract. The test must fail against the pre-change instructions, then pass after the
prose is added. Run the focused test, `just verify`, and the separately CI-gated
`UV_NO_SYNC=1 uv run prek run --all-files` command.

## Delivery context

- Branch: `feat/publish-mcp-conventions-148`
- Base branch: `main`
- Host architecture: `arm64`
- Target architectures: `amd64`, `arm64`
- Architecture relationship: `included`
- Guardrails: `just verify`; `UV_NO_SYNC=1 uv run prek run --all-files`
- ADR index: absent; no new ADR is required because ADR 0025 already governs the final public
  parameter decision and this change introduces no competing architectural choice.
