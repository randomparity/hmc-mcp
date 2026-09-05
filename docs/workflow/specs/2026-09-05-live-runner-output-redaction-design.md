# Live-runner failure-output redaction design

## Charter

- **Interaction:** interactive.
- **Scope identity:** improve the pre-release codebase through the desloppify
  execution queue.
- **Outcome:** redact sensitive values from live-runner failure output at a
  single ingestion boundary.
- **Completion criteria:** terminal and persisted failure output omit secret
  values, URL userinfo, bare hostnames, and local filesystem paths; ordinary
  diagnostics remain readable; the configuration bootstrap omits its path and
  hostname; focused tests and repository guardrails pass.
- **Provenance:** user requested the desloppify cleanup, selected option 1 on
  2026-09-05, confirmed that bare hostnames and local paths are redacted, and
  approved this design on 2026-09-05.
- **Exclusions:** successful tool payloads, general log redaction outside this
  script, and changes to HMC credential loading.
- **Surface:** `scripts/live_test_runner.py`, `tests/test_live_runner.py`, this
  spec, its implementation plan, and ADR 0120.
- **Ambiguities:** none.

## Decision

ADR 0120 governs this design. `RunState.call()` will pass caught exception text
and the formatted traceback through one private sanitizer before returning its
`FAIL` value. `record()` will therefore print and retain the same sanitized
text. The bootstrap acknowledgement will say only that configured credentials
were loaded. PASS and SKIP behavior is unchanged.

## Data flow and errors

`Client.call_tool()` may raise text controlled by a remote HMC or by local
configuration. The catch block combines the exception class, message, and
traceback; the sanitizer replaces sensitive substrings with stable explicit
markers, then the existing `record()` and JSON writer consume that value. The
sanitizer is total for string input: a nonmatching string is returned unchanged.

## Threat model

### Boundary inventory and actors

| Boundary | Data and actor | Control and failure leak |
| --- | --- | --- |
| HMC/tool failure to runner | Remote HMC or a compromised/intermediate service controls exception text | Sanitize before `RunState.call()` returns; preserve only safe diagnostic text. |
| Local configuration to terminal | Local operator configuration supplies path and host | Do not emit either identifier in bootstrap status. |
| Runner state to JSON/stdout | Local operator, CI collector, or later reader receives output | Store and render the already-sanitized failure value; no distinct rendering path. |

The design adds no authorization boundary and trusts the process only to apply
the sanitizer before it exposes caught failures.

### Out of scope

Successful tool results can intentionally contain operational identifiers and
remain unchanged because this runner needs their structured data. This change
does not sanitize output written by other scripts, files already created, or
values an operator deliberately supplies on the command line.

## Acceptance criteria

- Failure data with a password, URL userinfo, hostname, or absolute local path
  records only redaction markers and preserves its exception type and safe text.
- `record()` prints the sanitized failure text and JSON receives that same value.
- Bootstrap status contains neither resolved config path nor configured host.
- Existing PASS, SKIP, and ordinary failure tests remain green.
