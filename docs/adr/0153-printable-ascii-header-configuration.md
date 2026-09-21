# ADR 0153: Printable ASCII for header configuration

## Status

Accepted (2026-09-16)

## Context

`HMCConfig.schema_version` and `audit_memento` reach HTTP request headers.
Unlike `agent_id`, neither rejects control characters or non-ASCII at
construction. Issue #839 identifies a permanent configuration defect that can
reach the retryable transport classification ADR 0148 distinguishes from local
request refusal.

Source inspection, not a network reproduction: h11's header validation rejects
CR/LF via `LocalProtocolError`; httpcore maps that to its `LocalProtocolError`,
and httpx maps it to `httpx.LocalProtocolError`, a `TransportError` subclass.
`HMCClient._request` wraps that family as `HMCTransportError`. Changing routes
cannot correct the configured character. This does not claim that h11 rejects
every character outside the range chosen below.

## Decision

Validate both fields at HMCConfig construction with one field validator. Accept
only characters U+0020 through U+007E, matching `agent_id`'s printable-ASCII
constraint without importing its ownership-token grammar. Empty strings remain
accepted. Invalid values raise Pydantic's `ValidationError` (a `ValueError`),
naming the field and directing the caller to use printable ASCII. The validator
message does not interpolate the supplied value.

`from_mapping` invokes the same validation without environment-derived values,
as ADR 0096 specifies. Ordinary environment and profile precedence is unchanged.
Validate `audit_memento` even when `agent_id` overrides its effective value.

## Consequences

- Controls, DEL and non-ASCII fail before request construction, rather than
  depending on transport refusal or encoding errors.
- Printable punctuation and spaces remain accepted, as do the empty schema
  version (header omitted) and empty audit memento. This is a character-range
  contract, not validation of HMC semantics or the full HTTP header grammar.
- Agent ownership grammar and general retry classification remain unchanged.
  Post-construction mutation and validation-bypassing model APIs are not covered.

## Considered & rejected

- **Do nothing.** verified: `config.py` at `45ef18be` declares both fields as
  strings without a character validator; `client/core.py` forwards them to
  headers and translates `httpx.TransportError` to `HMCTransportError`.
- **Catch local protocol errors at the request waist.** judgment: later refusal
  cannot name the configuration field as directly and broadens retry policy.
- **Reuse the whole agent identifier validator.** verified:
  `validate_agent_id` at `45ef18be` also forbids printable punctuation, spaces,
  reserved identifiers and values longer than 64 characters. Those ownership
  constraints are not this header contract.
- **Sanitize invalid values or validate only environment input.** judgment:
  silent rewriting hides configuration mistakes; source-specific validation
  leaves library construction with a different contract.
