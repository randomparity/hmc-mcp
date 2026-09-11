# Configurable HMC response ceiling

## Problem and authority
Issue #771 requires a supported override of #770's bounded response size; #768 asks
for a default in the tens of MB. Scope is frozen by `q771-2e941cb7` on #771.
The operator approved the child and exclusion/owner pairs on 2026-09-10.
On resume, the operator approved exactly `max_response_bytes` (Python/TOML),
`HMC_MAX_RESPONSE_BYTES` (environment), 32 MiB default, and integer greater than zero.

## Design
Add `max_response_bytes: int = Field(default=32 * 1024 * 1024, gt=0, ...)` to
`HMCConfig`. Preserve ordinary Pydantic integer conversion, constructor over
environment precedence, environment over TOML precedence, and isolated
`from_mapping` defaults. Configuration source code needs no special case.
`_request` passes `self.config.max_response_bytes` to `_read_bounded_response`.
Both declared-length and streamed-size comparisons use that argument; remove the
old constant. No duplicated enforcement or error-body policy is introduced.
See [ADR 0134](../../adr/0134-configurable-hmc-response-ceiling.md).

## Success
- C1 (#771): default is 33554432 bytes; positive integer overrides resolve through
  constructor, environment, mapping and TOML; zero, negatives, fractional values,
  nonnumeric text and null fail model validation.
- C2 (#771): explicit constructor values beat environment; environment, including
  case-insensitive names, beats TOML; mapping omissions ignore ambient values.
- C3 (#771/#770): clients use their resolved ceiling for declared and streamed
  responses, accepting exact boundaries and closing on overflow. The existing
  cancellation, encoding and independent error-body contracts remain unchanged.
- C4 (#771): the guarded environment reference names type, default, byte units,
  profile key, memory consequence, and inability to disable the bound with zero.
- C5 (#771): `just verify` and `uv run --no-sync prek run --all-files` pass.

## Global Constraints
Python 3.11–3.14; CI targets amd64 and arm64. No new dependencies. Keep transport
enforcement and independent error-body truncation owned by #770. No ISO-download,
listing-limit, server-side HMC-limit, or parser/schema changes.

## Failure model
- Actors and deployments: local CLI/MCP operators and Python consumers configure
  clients; remote HMC responses cross the existing HTTP boundary.
- Invariants and assets at stake: positive configured byte bound, precedence,
  isolated library construction, process availability and response cleanup.
- Accepted failure classes: operators can deliberately set a ceiling above
  available memory; the setting limits response bytes, not total process memory.
  Pydantic's existing integer coercion remains in use. Post-construction mutation
  or validation-bypassing model construction is trusted caller behavior, as for
  existing configuration fields.
- Covered elsewhere: transport enforcement/encoding/cancellation/error-body cap
  by #770; ISO downloads by existing bounded-download implementation; listing
  semantics by #768/#770; server limits by HMC API; parsers by future domain work.

## Threat model
- Added boundary: operator-controlled setting enters validated HMCConfig. Existing
  widened boundary: HMC responses may be admitted up to the operator's ceiling.
- Actors: configuration is trusted operator input; HMC response bytes and headers
  remain untrusted. Python consumers are trusted to use validated models.
- Controls: Pydantic integer validation plus `gt=0`; existing single bounded reader
  enforces the resolved value and emits size/limit errors while closing responses.
- Out of scope: a hostile operator raising their own bound and the independently
  owned failure classes listed above; no global RSS budget is promised.

## Validation
Use config tests for C1/C2 and the existing MockTransport response suite for C3.
Drive distinct configured clients, declared and streamed exact/over boundaries,
and error diagnostics at a larger configured ceiling. Reuse the env guard for C4;
manually read the operator prose. The full guardrails establish C5 on the host;
GitHub's matrix supplies the other interpreter and architecture arms.
