# ADR 0065: Forbid `"` and `\` in `HMC_AGENT_ID`

## Status

Accepted

## Context

ADR 0011 folds `HMC_AGENT_ID` into the ownership token stamped on every
created LPAR (`[hmc-mcp owner:<agent_id> created:<date>]`) and into the
`X-Audit-Memento` header. `validate_agent_id` guards the value at config
construction time, but its forbidden set grew from the audit-token format:
commas and equals signs corrupt the HMC CLI `-i` parser, brackets break the
token framing, `/` is refused by the REST API, colons make the token formats
ambiguous, spaces corrupt the `-i` record. Double quotes and backslashes were
never added — yet both are poison for the composed stamp:

- `"` is the HMC CLI `-i` attribute-record escape (the same reason
  `validate_caller_token`, added with ADR 0064, refuses it).
- `\` has unverified behaviour inside an `-i` record (ADR 0045), so it cannot
  be assumed safe.

An operator could therefore configure an agent_id such as `agent"x` that
passes construction-time validation but makes the composed ownership stamp
fail `validate_lpar_description`. Because `stamp_lpar_ownership` is
deliberately best-effort (ADR 0011: a failed stamp must not fail a create
whose LPAR already exists), the ValueError was swallowed and degraded to a
skipped stamp. The result was permanent, silent loss of ownership attribution
for *every* LPAR that agent created — exactly the failure mode the advisory
protocol depends on not happening silently (issue #386).

## Decision

Add `"` and `\` to `validate_agent_id`'s forbidden character set, with
reasons mirroring `validate_caller_token`:

- `"`: double quotes are the HMC CLI `-i` record escape.
- `\`: backslash behaviour inside an HMC CLI -i record is unverified
  (ADR 0045).

The rejection stays at configuration construction time (`ValueError` from
`HMCConfig`, via the existing `agent_id` field validator), so a bad value
fails fast with a message naming the offending character instead of disabling
stamping for the process's whole lifetime.

This is a public-contract tightening of `HMC_AGENT_ID` values (ADR 0011):
values that previously constructed fine are now refused at startup.

The defensive pre-flight `validate_lpar_description` inside
`stamp_lpar_ownership`'s best-effort catch remains unchanged: with no
config-legal character left that the description grammar rejects, it has no
expected config-driven case, but it still bounds transport-era surprises and
must not fail the owning create after the LPAR exists.

## Consequences

- An `HMC_AGENT_ID` containing `"` or `\` fails server start-up with a
  `ValueError` naming the character, instead of silently skipping every
  ownership stamp that agent would have written.
- The ownership-stamp grammar and the config validator can no longer drift
  apart on these two characters; `validate_agent_id` and
  `validate_caller_token` now refuse the same delimiter set (modulo the
  caller-specific whitespace rule).
- Operators upgrading who already run with such an agent_id must pick a new
  one; the error message states the reason. Existing LPARs stamped before the
  misconfiguration was noticed may lack tokens — the advisory protocol already
  treats an absent token as "no claim" (ADR 0011).
- No migration or shim: the old acceptance was a defect surface (issue #386),
  not a supported format.
- The best-effort degrade in `stamp_lpar_ownership` keeps covering SSH and
  network failures; it no longer needs to cover any known agent_id case.

## Considered & rejected

**Surface the skip loudly instead of forbidding the characters** (warn on
every create, keep accepting the values). Rejected: the warning fires per
create and is easily missed; the failure is deterministic and knowable at
configuration time, which is where it should be rejected. Mirrors why
`validate_caller_token` raises before any HMC traffic rather than degrading.

**Extend ADR 0011's consequences section instead of a new ADR.** Rejected:
ADR 0011 records the original advisory protocol decision; this is a separate,
later public-contract change to the same variable, and the ADR index stays
one decision per record.

**Also forbid other shell-sensitive characters (`` ` ``, `$`, `'`).**
Rejected without evidence: no recorded HMC CLI behaviour makes them unsafe in
an `-i` record or description, and `validate_lpar_description`'s delimiter
table plus printable-ASCII enforcement already bound what reaches the HMC.
Forbidding characters without a recorded failure mode narrows the contract
for nothing.
