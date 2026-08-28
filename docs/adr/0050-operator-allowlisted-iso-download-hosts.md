# 0050 — `hmc_upload_iso` downloads only from operator-allowlisted hosts

## Status

Accepted (2026-08-20)

## Context

ADR 0049 made an `http(s)` URL the only source `hmc_upload_iso` accepts, and said
in its own Consequences that this did not close the tool: the server still
fetches a caller-supplied URL from its own network position. That is #303, and
this is its change.

The caller chooses the destination; the MCP server supplies the reachability.
That reaches what the caller cannot: cloud instance-metadata endpoints
(`169.254.169.254`), services bound to loopback on the server host, and hosts
inside the server's segment. Everything bounding the fetch before this change,
read from source:

| Guard | Value | Stops |
|---|---|---|
| Scheme | `http`, `https` (`_require_http_url`) | non-HTTP schemes only |
| Redirects | followed, `max_redirects=5` | nothing — see below |
| Timeouts | 30 s connect / 300 s read | a hang, not a fetch |
| Size | 100 GiB | nothing an SSRF payload reaches |

Two of those are worth stating plainly. **Redirects were followed**, so a check
applied to the URL the caller passed is defeated by a permitted host answering
`302 http://169.254.169.254/…`; bounding the count at five bounds the wrong
thing. And the 100 GiB size bound is sized for ISOs — an instance-metadata
response is a few hundred bytes.

What a caller is *guaranteed* to learn is the response's SHA-256 and exact size
(returned as `sha256` and `media_size_bytes`), plus status/connect-error text and
timing. Not the body: `hmc_list_optical_media` returns `MediaName`, `MediaSize`,
and `MediaType` only, so recovering content requires mounting the media in a
guest the caller controls. The guaranteed primitive is an internal-network fetch
with a digest-and-size oracle, escalating to exfiltration only for a caller who
also controls an LPAR.

The access policy cannot close this, for the same reason it could not close #261:
no `TargetKind` names "a network endpoint the server can reach" (ADR 0039), so no
`targets` allowlist bounds `iso_source` under any design. `iso_source` is
classified as a payload-source argument precisely because it is outside the
target dimension.

Reaching the fetch is not unauthenticated: the tool is `effect="mutate"` with
`target_kind="vios"`, so it needs a `mutate` grant naming a real VIOS.

## Decision

**Two parts, both in the fetch path.**

**1. The URL's host must be on an operator-configured allowlist, and an unset
allowlist permits nothing.**

`HMCConfig.iso_url_allowlist` (`HMC_ISO_URL_ALLOWLIST`, or `iso_url_allowlist` in
a TOML profile) is a comma-separated list of authorities, each `host` or
`host:port`. `operations_storage._require_allowlisted_iso_url` compares the
URL's host and effective port against it, and `upload_iso` calls it as its second
statement — after the scheme check, before `resolve_vios_uuid`, before any socket.
An entry without a port permits any port on that host; an entry with one permits
only that port, which is what lets an operator permit a single loopback ISO
server without permitting every service bound to loopback.

Only an operator knows which ISO servers are legitimate, so only an operator can
say. The setting is validated at config load: an entry carrying a scheme, a path,
credentials, or an unusable port is a `ValueError` naming the entry, because an
operator who writes `https://iso.example.internal/isos/` and gets a silently
empty allowlist would conclude the feature is broken and widen it.

**`iso_url_allowlist` is empty by default and empty means refuse everything.**
There is no safe default destination — the whole defect is that the server's
network position is not the caller's — so a default that fetched anything would
be the defect with a new name. The refusal message names the variable, the TOML
key, and an example value, because a fail-closed default that produces an opaque
error is a support burden rather than a control.

**2. Redirects are refused, not followed.** `follow_redirects=False`, and any 3xx
raises. The explicit refusal is necessary: `raise_for_status()` does not treat 3xx
as an error, so an unfollowed redirect would otherwise be staged and imported as
if its body were the ISO. Every 3xx is refused rather than only what
`httpx.Response.is_redirect` counts (which additionally requires a `Location`
header) — a bare 3xx body is not an ISO either. `MAX_REDIRECTS` is deleted; there
is no redirect to bound.

Part 2 is what makes part 1 mean anything: **the URL fetched is the URL checked.**
That is asserted, not assumed — `test_download_iso_refuses_a_redirect_instead_of_following_it`
drives the download through a real `httpx.AsyncClient` on a recording
`httpx.MockTransport`, answers `302 http://169.254.169.254/…`, and asserts the
transport saw exactly one request, to the URL that was checked.

### DNS: what this does and does not close

**A name-based allowlist has no check-then-fetch resolution window.** The classic
DNS-rebinding TOCTOU needs a check on a *resolved address* and a second
resolution at connect time, so that the answer can change in between. This check
resolves nothing: `_require_allowlisted_iso_url` calls `urlparse` and compares
strings. The only resolution in the operation is the one httpx performs while
connecting, and there is no earlier answer for it to disagree with. Verified by
construction and by test — the refusal tests trap `socket.getaddrinfo`,
`socket.create_connection`, and `socket.socket.connect`, and a refusal reaches
none of them.

**What remains open is DNS *control*, not DNS rebinding, and it is left open.**
An attacker who can make an allowlisted name resolve to an address of their
choosing — by controlling the record, by poisoning the resolver the MCP server
host uses — gets back exactly the pre-#303 primitive: the fetch lands on
`169.254.169.254` or a loopback service, and the caller gets that body's SHA-256
and size, and its bytes imported into the media repository as media. The
allowlist does not bound that, because the allowlist is a set of names and the
attacker has taken one of the names.

It is left open deliberately. Closing it means the operator declaring addresses
rather than names — a second configuration field, an IP allowlist that has to be
maintained as ISO servers move, and a check that has to keep working through
CDNs and DHCP — which is more surface than the residual is worth, and #303 is a
P2. The precondition it demands (DNS control over a host the operator has
explicitly trusted) is strictly stronger than the one this change removes (any
caller holding a `mutate` grant). It is **#322**, filed rather than forgotten.

## Consequences

**Every existing caller of `hmc_upload_iso` breaks on upgrade until an operator
sets `HMC_ISO_URL_ALLOWLIST`.** Not "may break" — every URL is refused, including
the exact URL that worked before the upgrade. That is the accepted cost of
fail-closed, and it was chosen over preserving current behaviour with the break
accepted explicitly. There is no escape hatch, no "unset means permissive"
fallback, and no grace period: each of those is a default that fetches anything,
which is the defect. What an operator gets instead is a refusal that names the
variable to set and shows an example value.

Concretely:

- `scripts/live_test_runner.py` uploads from `http://localhost:18765/…` —
  machinery ADR 0049 introduced. It stops working under this change, so
  `_allow_iso_host()` now appends `localhost:18765` to `HMC_ISO_URL_ALLOWLIST`
  for its own run, next to the code that starts that server. The runner
  configures the allowlist exactly as an operator would; the default is not
  weakened to accommodate it, and the entry names the port, so it permits that
  one server and no other loopback service.
- `virtual-media-live-test-plan.md` said the HTTP path "handles a localhost
  server without any special configuration". After this change that is false, so
  it now says what to set.
- `hmc-mcp storage upload-iso` is affected identically. This lands hardest on the
  CLI operator, for whom "the MCP server host" and "my own machine" are the same
  machine — they must now declare the ISO server they were already using. The
  shared operation is where the check lives, deliberately: ADR 0049 rejected
  making the safety of the tool a property of its caller, and that reasoning did
  not stop being true here.

**This does not make the tool safe to grant broadly.** It bounds *where* the
fetch can go to a set an operator wrote down. Within that set, the caller still
chooses the URL, still gets a digest-and-size oracle over what comes back, and
still has the DNS residual above.

`HMCConfig` gaining a field moves the frozen public-signature digest in
`tests/unit/test_public_api.py` — a pydantic model's `__init__` signature is
derived from its fields — so that digest moves with this ADR, per ADR 0029.

## Considered & rejected

Grounds are separated by what was checked and what was judged, because an
unverified rejection can stand unchallenged for years.

**Refusing loopback, link-local, and private ranges instead of an allowlist.**

Rejected. *Verified:* `scripts/live_test_runner.py` uploads from
`http://localhost:18765/…`, so a blanket loopback denial breaks the documented
live-test path this project ships. *Design judgement:* in HMC estates the
legitimate ISO server normally lives on RFC1918, so a private-range denial
refuses the ordinary case while an operator's real threat — a metadata endpoint —
is one address in one of those ranges. A denylist of ranges also has to stay
correct as address families and cloud metadata addresses change; an allowlist
fails closed when it is wrong.

**Requiring the ISO by content rather than by reference.**

Rejected. *Verified:* `upload_iso` already reads the whole staged file into
memory (`content = f.read()`, `operations/storage.py:485`) before the broker
upload, which is its own defect (#308); passing multi-GB bytes inline over MCP
is worse than the fetch, not better.

**An allowlist of URL prefixes rather than hosts.**

Rejected. *Verified:* the maintainer's framing allowed either. *Design
judgement:* prefix matching invites the errors the check exists to prevent —
`https://iso.example.internal/isos` also prefixes `…/isos-attacker/`, and a
prefix written against the URL string has to be re-derived against percent
encoding, dot segments, and `user@host` forms before it means anything. A host
comparison is done on `urlparse`'s already-normalised `hostname`, where
`http://iso.example.internal@evil.test/` is `evil.test` and nothing else. The
allowlist bounds *whose server*, which is the question an operator can actually
answer; it does not bound which file, and does not pretend to.

**Bounding redirects (a lower `max_redirects`, or re-checking each hop against
the allowlist) rather than refusing them.**

Rejected. *Verified:* nothing in the fetch path inspected redirect targets, and
`raise_for_status` does not fire on 3xx, so the redirect branch had no check on
it at all. *Design judgement:* re-checking each hop keeps a branch alive whose
safety depends on a check staying correct, when the branch itself buys nothing —
an ISO server that cannot serve the ISO at the URL an operator published is not
a constraint worth carrying. Removing the branch is the same move ADR 0049 made
on the filesystem branch, and for the same reason.

**Pinning the resolution between check and fetch.**

Rejected as inapplicable rather than as too costly. *Verified:* the check
consumes no resolution at all (it is `urlparse` and a string comparison), so
there is no first answer to pin and no window between two answers. Pinning would
only mean something alongside an address-level policy, which is the second
configuration field this change declined to add. See the DNS section above for
what stays open as a result.

**Preserving today's behaviour when the allowlist is unset.**

Rejected by the maintainer directly, since it commits a configuration surface and
that is their call. The upgrade break is accepted explicitly. A permissive
default would mean every installation that does not know about this ADR keeps
the defect, which inverts who bears the cost of the decision: the operator who
never reads the release note would keep the vulnerability, and the operator who
does would opt into safety.

## References

- #303 — `hmc_upload_iso` fetches a caller-supplied URL from the server (SSRF)
- #308 — the same path reads the whole ISO into memory (separate change)
- #322 — the DNS residual this ADR leaves open
- `docs/adr/0049-iso-upload-accepts-only-http-urls.md` — made the URL branch the
  tool's only input path, and deferred this to its own change
- `docs/adr/0039-dispatch-time-target-scope.md` — why no `targets` table bounds a
  source outside the HMC
- `docs/adr/0029-supported-reusable-python-api-contract.md` — the frozen
  public-signature contract this change moves
- `docs/environment-variables.md` — `HMC_ISO_URL_ALLOWLIST`
