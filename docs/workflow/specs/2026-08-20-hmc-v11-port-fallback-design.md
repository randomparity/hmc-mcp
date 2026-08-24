# HMC V11 REST-port fallback design

## Scope authority

- Interaction: interactive.
- Scope identity: https://github.com/randomparity/hmc-mcp/issues/243;
  `q243-8836eb74`.
- Outcome: support the HMC V11 REST port change while retaining compatibility
  with older HMCs.
- Completion criteria: `port` remains configurable by TOML profile and
  `HMC_PORT`; when omitted, REST connection attempts use 443 first and fall back
  to 12443 only when the 443 transport cannot establish a usable session; an
  explicitly configured port is used without fallback; operator documentation
  and automated tests cover both paths.
- Provenance: issue #243 and the operator's 2026-08-20 quest decision.
- Exclusions: quick-property work remains in #244; no live HMC mutation or
  credential use; no unrelated transport or configuration changes.
- Surface: connection configuration/profile loading, REST session establishment
  and cleanup, related tests and fixtures, README/environment/config docs, and
  the design records required by the workflow.
- Ambiguities: empty.

## Architecture and decision

[ADR 0059](../../adr/0059-prefer-hmc-v11-rest-port-with-bounded-legacy-fallback.md)
governs the connection policy. `HMCConfig.port` defaults to 443. Pydantic's
`model_fields_set` is the existing provenance signal: observed with the locked
environment, an omitted field is absent, while constructor and `HMC_PORT` values
include `port`; TOML values enter as constructor values in `load_profile` and are
therefore also included.

`HMCClient` snapshots whether the configured port was implicit during
construction. Its initial `httpx.AsyncClient` uses the configured 443 destination.
`logon()` performs the existing logon exchange. Only an `HMCTransportError` from
that exchange, while the port was implicit and still equals 443, activates one
legacy retry. The failed HTTP client is closed before a replacement client is
created for 12443. The replacement retains TLS verification, timeout, audit
header, and all other request behavior. The configuration object is not mutated.

`HMCClient` owns a private active REST base derived from its current HTTP client.
It starts at the configured base and changes to 12443 with client replacement.
Existing mixins use that active base when they expand a relative path or resource
identifier into an absolute URL. Absolute links supplied by the HMC remain
untouched. The narrow protocols used to type those mixins declare the private
base without adding a supported public API member.

The retry is part of session establishment, not generic request handling. Later
request failures never switch destinations. An HTTP logon response of any status
also never switches destinations. A failed fallback follows the existing
transport-error behavior, which does not add a password, response body, or session
token to the message.

## Alternatives

The selected approach is preferred over preserving 12443 because it works with
the current HMC release, and over unconditional 443 because the project still
supports older HMCs. A generic request-level retry or preflight probe would widen
the behavior beyond session establishment and was rejected by ADR 0059.

## Components and data flow

1. `HMCConfig` resolves constructor, environment, or TOML `port` values exactly as
   today; omission yields 443 and an absent `port` field-set entry.
2. `HMCClient.__init__` records whether legacy fallback is eligible and creates
   the initial HTTP client.
3. `logon()` builds the credential document once and attempts port 443.
4. On the eligible transport-only failure, it closes the failed client, creates
   a 12443 client with the same options, updates the private active REST base,
   and repeats the same logon request once.
5. Success stores the token on the legacy client. A failed fallback preserves
   the existing transport-error behavior. Context-manager cleanup closes
   whichever client is current.
6. PCM relative-link expansion and locally generated storage/network relationship
   links use the private active REST base. Ordinary relative requests continue to
   resolve through the current HTTP client's matching base URL.

## Error and cleanup contract

- Explicit ports, including explicit 443, surface their first transport failure.
- HTTP 4xx/5xx responses surface the existing `HMCError` without fallback.
- XML construction and logon-response parsing errors surface without fallback.
- The legacy client is constructed only after the first client's ordinary
  `aclose()` call returns. A close exception or caller cancellation therefore
  aborts fallback before a second transport is opened; existing caller and
  context-manager cleanup semantics otherwise remain unchanged.
- A successful fallback produces exactly one usable local session token.
- After successful fallback, subsequent relative requests and locally generated
  absolute REST links use 12443; absolute links returned by the HMC are preserved.
- A failed first exchange may have created an unreachable server-side session;
  this accepted residual is documented for operators.

## Security and trust boundaries

### Boundary inventory

- Existing boundary widened: local operator-controlled TOML/environment values
  select an HMC network destination. Existing Pydantic integer validation and
  precedence rules remain the control.
- Existing boundary changed: an omitted port permits one additional credentialed
  TLS logon attempt to the same configured host on fixed port 12443. The host,
  credentials, TLS policy, timeout, and headers remain unchanged.
- Added boundaries: none; the fallback port is a literal and adds no caller-
  controlled destination component.

### Actor model and controls

The trusted actor is the local operator who supplies host, credentials, and
configuration. The untrusted party is the network path and remote endpoint. TLS
verification remains controlled by `verify_ssl`; fallback never weakens it.
Fallback is limited to an implicit port, a transport-layer failure, one literal
destination port, and one retry. Error text excludes credentials and response
bodies. Explicit operator selection always wins.

### Out of scope

This change does not detect a malicious endpoint, repair disabled TLS
verification, reclaim an HMC-side session whose response was lost, or alter MCP
authorization. Those risks are existing operator/network concerns or impossible
without a returned session token.

## Test strategy and acceptance

Automated tests must prove:

- bare `HMCConfig` defaults to 443;
- TOML and `HMC_PORT` continue to select explicit ports;
- an implicit 443 transport failure closes that client, retries 12443 once, and
  stores the returned session token;
- a first-client close exception and cancellation while that close is suspended
  both propagate without constructing or requesting through a 12443 client;
- explicit 443 and explicit 12443 transport failures do not retry;
- a port-443 HTTP error response does not retry;
- a second transport failure preserves existing sanitized transport-error behavior;
- a post-fallback request and each of the five current locally generated PCM,
  storage, and network URL sites use 12443 rather than configured 443;
- normal 443 success and existing request/session behavior remain unchanged;
- README, configuration examples, and environment-variable docs describe the
  default, fallback, explicit override, extra failed-attempt latency, and possible
  unreachable session residual.

Focused tests run with
`uv run --no-sync pytest -q --no-cov tests/unit/test_config.py tests/unit/test_client.py`.
Repository completion requires `just test`, `just smoke`, and `just verify` on
the host architecture `x86_64`. CI targets `amd64` and `arm64` on Python
3.11–3.14; the change is architecture-neutral.
