# Rolling Python support design

## Scope

Issue #160 changes one compatibility contract: support begins at CPython 3.11 and includes every
stable, non-EOL release. It aligns package metadata, the developer default, README, lockfile, and
hosted CI while preserving pinned actions, least privilege, and `just verify`. Native runner
provisioning, release-artifact construction, publication, and the architecture-product matrix are
owned by #161–#163 and remain outside this change.

The governing decision is [ADR 0020](../../adr/0020-rolling-cpython-support-policy.md).

## Policy and workflow

`pyproject.toml` is the installation authority and declares `requires-python = ">=3.11"`.
`.python-version` remains the single developer default and selects 3.11 so ordinary local setup
exercises the floor. The README states the rolling policy, and `uv.lock` is regenerated at the
new floor.

The existing CI job becomes an explicit Python matrix containing `3.11`, `3.12`, `3.13`, and
`3.14`, the stable supported lines on 2026-08-14. Every arm retains the current Ubuntu runner,
timeout, pinned actions, setup, `just verify`, and hook validation. This issue does not claim
native multi-architecture coverage.

A second bounded job runs only on the weekly schedule and manual dispatch. It invokes
`scripts/check_python_support.py` with the same explicit versions. The checker fetches the Python
PEP release-cycle JSON and selects versions at or above 3.11 whose status is exactly `bugfix` or
`security`. Equality means the repository includes every stable supported line and retains no EOL
or prerelease line. A mismatch reports missing and unexpected versions and exits nonzero.

## Lifecycle checker

The checker exposes pure parsing and comparison functions for fixture-driven tests. Its CLI uses
only the standard library, accepts one or more unique `major.minor` values, and requests the fixed
`https://peps.python.org/api/release-cycle.json` URL, rejects redirects, and uses a ten-second
socket-inactivity timeout. It reads at most
256 KiB plus one sentinel byte and rejects oversized, invalid JSON, non-object roots, malformed
entries, unsupported status values relevant to the selected set, duplicate expected values, and
versions below the 3.11 floor. Network and validation failures include the failed operation and a
next action.

## Failure behavior

The scheduled job fails closed when Python's lifecycle service is unavailable, redirects,
is malformed, or exceeds the response bound. Its five-minute job timeout is the end-to-end bound;
the checker's ten-second timeout bounds an individual stalled socket operation. It never modifies
the workflow or lockfile. Maintainers inspect the
authoritative lifecycle page, update the explicit matrix and lockfile in a reviewed pull request,
then rerun the check. Pull-request CI does not depend on network lifecycle data.

## Threat model

The added trust boundary is a scheduled CI job reading public JSON controlled by the Python
project. The untrusted party is the remote service or any network intermediary able to affect a
failed TLS request; Python's HTTPS verification authenticates the fixed host. Fixed destination,
redirect rejection, inactivity timeout, response-size bound, strict JSON/schema validation, and
comparison-only behavior prevent remote input from becoming commands, paths, dependencies, or
dynamic workflow jobs. Error
messages expose no credentials because the request sends none.

The existing GitHub workflow boundary is widened only by `schedule` and `workflow_dispatch`
triggers. Permissions remain `contents: read`, action references remain immutable SHA pins, and
the remote response is never interpolated into shell or `${{ }}` expressions. Compromise of
Python's publication process and denial of service at the remote endpoint are outside scope; the
former is an accepted upstream-authority risk, and the latter intentionally causes a visible
scheduled failure.

## Verification

- Parser tests cover the current 3.11–3.14 fixture, a new stable line, EOL retention, prerelease
  exclusion, malformed roots/entries, oversized input, duplicate or below-floor expectations,
  redirect rejection, timeout propagation, and actionable network failure.
- Workflow tests prove the explicit matrix, scheduled/manual trigger, identical matrix values
  passed to the drift job, fixed least privilege, immutable action pins, bounded jobs, and
  delegation to `just verify`.
- Metadata tests prove the package floor, developer default, README policy, and lockfile floor.
- `just verify` remains the full local guardrail.
