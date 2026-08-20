# ADR 0058: Prefer the HMC V11 REST port with bounded legacy fallback

## Status

Accepted

## Context

HMC V11 serves REST on port 443, while older supported HMC releases may serve it
only on 12443. The issue body's version wording is reversed; the repository
owner's [source review][owner-review] corrects it using IBM's
[HMC Firewall Information][firewall] (V11 and later use 443; V10R3 and earlier
use 12443) and [HIPER APAR IJ56446][apar] (V11R1 changed REST access from 12443
to 443). A collaborator then [live-verified V11 on 443][v11-live] and
[V10R3 on 12443 with no REST service on 443][v10-live]. The current default of
12443 therefore hangs until the configured timeout against HMC V11. Operators
can already set `port` in a TOML profile or `HMC_PORT`, but an omitted port
cannot connect across the supported HMC range.

A failed logon attempt may create a server-side session before the client receives
a response. An automatic retry must therefore be narrow: it may improve legacy
compatibility, but it must not mask a response received from the selected HMC or
override an operator's explicit destination.

## Decision

The configuration default is port 443. `HMCClient.logon()` retries once on port
12443 only when all of these conditions hold:

- the configuration obtained no `port` from constructor arguments, environment,
  or TOML;
- the first port-443 logon raises `HMCTransportError` before returning an HTTP
  response; and
- no session token was obtained.

The retry replaces and closes the failed HTTP client before opening the legacy-port
client. HTTP responses, including authentication failures, never trigger fallback.
An explicit port is authoritative and never triggers fallback. A failed legacy
attempt follows the existing transport-error behavior.

## Consequences

- New implicit configurations prefer the HMC V11 port and still reach older HMCs.
- Existing explicit `12443` configurations keep their behavior.
- A failed implicit port-443 attempt can leave an HMC-side session that the client
  cannot identify or close; the bounded single retry accepts that server limitation.
- First connection to an older HMC adds the duration of one failed port-443
  logon attempt. httpx applies the scalar timeout per network phase rather than
  as a total-attempt deadline, so this is not a single wall-clock timeout bound.
- The client's effective REST destination can become 12443 while the immutable
  configuration value remains the requested default of 443.

## Considered & rejected

- **Keep 12443 as the default.** verified: IBM's HMC firewall documentation and
  APAR IJ56446, linked above, and the live V11 report on issue #243 establish
  that V11 REST uses 443, so the current default does not connect to the current
  release.
- **Use 443 without fallback.** judgment: this needlessly drops out-of-box
  compatibility with older supported HMC releases when a bounded transport-only
  retry can preserve it.
- **Fall back after any logon failure.** judgment: an HTTP response proves that the
  selected port reached an HMC; retrying authentication or application failures on
  another port would mask the actionable result and duplicate credential attempts.
- **Probe both ports before logon.** judgment: a separate probe adds a connection
  path that does not prove the logon exchange will work, while the real request must
  still handle transport failure.

[owner-review]: https://github.com/randomparity/hmc-mcp/issues/243#issuecomment-5332865205
[firewall]: https://www.ibm.com/support/pages/hmc-firewall-information
[apar]: https://www.ibm.com/support/pages/node/7251923
[v11-live]: https://github.com/randomparity/hmc-mcp/issues/243#issuecomment-5360371982
[v10-live]: https://github.com/randomparity/hmc-mcp/issues/243#issuecomment-5360433537
