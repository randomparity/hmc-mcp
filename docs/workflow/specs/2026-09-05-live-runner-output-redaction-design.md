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

ADR 0120 governs this design. `RunState.record()` will pass all `FAIL` data,
whether returned by `RunState.call()` or supplied directly by a subtask, through
one private sanitizer before it appends, prints, or serializes the value.
`main()` will also sanitize its pre-state configuration exception immediately
before printing it. The bootstrap acknowledgement will say only that configured
credentials were loaded. PASS and SKIP behavior is unchanged.

## Data flow and errors

`Client.call_tool()` may raise text controlled by a remote HMC or by local
configuration. The catch block combines the exception class, message, and
traceback; direct subtask failures can carry the same information. `record()`
sanitizes either source before the existing JSON writer consumes it. Before
state creation, `main()` sanitizes the configuration exception only for its
terminal rendering. The sanitizer is total for string input: a nonmatching
string is returned unchanged.

## Threat model

### Boundary inventory and actors

| Boundary | Data and actor | Control and failure leak |
| --- | --- | --- |
| HMC/tool or direct subtask failure to runner | Remote HMC, a compromised/intermediate service, or local subtask context controls failure text | Sanitize every `FAIL` value in `RunState.record()`; preserve only safe diagnostic text. |
| Pre-state configuration failure to terminal | Local config path and validation context control exception text | Sanitize the exception in `main()` immediately before printing; no JSON result exists yet. |
| Local configuration to terminal | Local operator configuration supplies path and host | Do not emit either identifier in bootstrap status. |
| Runner state to JSON/stdout | Local operator, CI collector, or later reader receives output | Store and render the already-sanitized failure value; no distinct rendering path. |

The design adds no authorization boundary and trusts the process only to apply
the sanitizer before it exposes any failure.

### Out of scope

Successful tool results can intentionally contain operational identifiers and
remain unchanged because this runner needs their structured data. This change
does not sanitize output written by other scripts, files already created, or
values an operator deliberately supplies on the command line.

## Acceptance criteria

- Failure data with a password, URL userinfo, hostname, or absolute local path
  records only redaction markers and preserves its exception type and safe text.
- `record()` prints the sanitized failure text and JSON receives that same value.
- A missing configuration file prints no path while retaining an actionable
  configuration error.
- Bootstrap status contains neither resolved config path nor configured host.
- Existing PASS, SKIP, and ordinary failure tests remain green.
