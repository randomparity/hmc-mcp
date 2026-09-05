# LPM job wait design

Issue: #150
Decision: [ADR 0017](../../adr/0017-stable-lpm-wait-outcomes.md)

## Goal and scope

Give LPM abort, recover, and remote-restart the standard optional wait controls
and one stable job-outcome contract in MCP and CLI. The change is limited to
`operations/lpm.py`, `server_tools/lpm.py`, `cli_commands/lpars.py`, focused LPM/CLI/schema
tests, and these design artifacts. It does not add #151's validate-first
sequence, change migrate or migrate-validate result shapes, or perform #145's
broad documentation pass.

## Contract

Each operation and MCP tool accepts `wait: bool = False`,
`timeout_seconds: int = 300`, and `poll_interval: int = 5`. CLI commands expose
the equivalent `--wait/--no-wait`, `--timeout`, and `--interval` options with
the same defaults. All three MCP tools always return `JobOutcome` with exactly
`job_id: str`, `status: str | None`, `timed_out: bool`, `error: str | None`, and
`job: dict[str, Any] | None`.

When waiting is disabled, the operation returns the normalized submission,
with `timed_out=False`. When waiting is enabled, it uses the existing
`wait_for_submitted_job` path, including the submitted SELF link, then calls
`job_outcome` with the submitted identifier and last observed entry. Terminal
success, terminal failure/exception, and timeout therefore retain the #141
field meanings. A waited submission without a usable identifier raises the
existing actionable `HMCError`; a non-waited submission without an identifier
uses an empty `job_id` while preserving the raw `job` value.

`LpmResult.job` is widened internally to carry either the legacy raw dictionary
used by migrate/migrate-validate or `JobOutcome` used by these three operations.
The MCP wrappers declare and return `JobOutcome`. The CLI presentation helper
serializes a `JobOutcome` as a dictionary and otherwise retains its current raw
job behavior.

## Data flow and validation

Each operation calls `validate_wait_timing` before selector resolution and
before any HMC request. With `wait=False`, unused negative timing values remain
accepted under the shared repository convention. With `wait=True`, negative
timeouts and non-positive intervals fail before remote work. After resolution
and submission, a private LPM helper either normalizes the submission directly
or waits and normalizes the last poll result.

The CLI validates timing before confirmation so invalid requested waits neither
prompt nor submit. It forwards the translated timing names to the operation.
Existing confirmation behavior remains unchanged.

## Error handling

Selector, submission, and polling errors propagate unchanged. Timeout is a
successful call returning `timed_out=True`, not an exception. Failed and
exception terminal states return their normalized status and nullable extracted
HMC error text. No fallback polling URL, retry policy, or validation-first
behavior is introduced.

## Security model

The existing boundary is an authenticated MCP or local CLI caller able to
request destructive LPM actions. This design widens only the duration controls
on those existing operations. `validate_wait_timing` rejects negative timeouts
and non-positive polling intervals before submission when waiting is requested;
the client timeout bounds polling duration, and the existing HMC authentication
and operation annotations remain unchanged. Errors expose only the same job
identifier, status, HMC error text, and raw job data already available to that
caller. Authorization, credential handling, rate limiting, and HMC-side action
semantics are explicitly out of scope.

## Tests

Focused operation/tool tests cover all three operations with `wait=False`, a
terminal successful poll, a terminal failed or exception poll, and a timeout;
the three outcome modes must have identical keys and field types. Parameterized
tests prove invalid active timings fail before resolution/submission. CLI tests
prove all three commands expose and forward `--wait`, `--timeout`, and
`--interval`, including a timeout result. Capability/schema tests pin the five
`JobOutcome` properties and standard input defaults for exactly these tools.
`just verify` is the final gate.

## Durable workflow context

- Branch: `feat/lpm-job-wait-150`
- Base branch: `main`
- Guardrail: `just verify`
- Host architecture: `arm64`
- Target architectures: none declared
- Architecture relationship: `no-target-declared`
