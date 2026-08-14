# ADR 0020: Rolling CPython support policy

## Status

Accepted (2026-08-14)

## Context

The package metadata, developer interpreter, documentation, lockfile, and hosted CI all name
Python 3.12 independently. That does not express the intended Python 3.11 floor, test later stable
releases, or reveal when CPython publishes a new stable line or ends support for an old one.

The Python Developer's Guide derives its lifecycle table from the machine-readable
`https://peps.python.org/api/release-cycle.json` dataset. CI inputs must remain explicit and
reviewed rather than changing silently when that remote dataset changes.

## Decision

Declare Python 3.11 as the package floor and support every CPython release whose lifecycle status
in the Python PEP release-cycle dataset is `bugfix` or `security`. Keep the executable CI version
list explicit in the workflow. A scheduled job compares that reviewed list with the authoritative
dataset and fails when the sets differ, requiring a normal reviewed repository change.

The lifecycle checker accepts the expected version list as arguments, fetches only the fixed
HTTPS authority, rejects redirects, bounds response size and socket inactivity, validates the
JSON shape and version syntax, and fails closed with an actionable message when the source is
unavailable or malformed.
Pull requests test the checker deterministically with local fixtures; only the scheduled job uses
the network.

## Consequences

Package installation and the lockfile resolve from Python 3.11 onward. Hosted CI runs the complete
canonical verification recipe once per explicitly listed supported version. Adding a stable line
or removing an EOL line is visible in review rather than being injected remotely into a workflow.
The scheduled check depends on the availability and schema stability of the Python PEP service;
an outage produces a visible failed check instead of silently accepting stale policy.
The checker uses a ten-second socket-inactivity timeout; the scheduled job's five-minute timeout
is the end-to-end execution bound.

## Considered & rejected

**Generate the matrix dynamically from the network.** Remote data would become executable CI
input without review and could change the number or identity of jobs between runs.

**Use calendar arithmetic locally.** Release dates can be adjusted, and calculated status would
not be the Python project's authoritative published lifecycle.

**Parse the rendered Python Developer's Guide HTML.** The presentation markup is a less stable
interface than the JSON source used to generate that guide.

**Update versions manually without a scheduled check.** The repository would again have no
enforcement when a stable line appears or an existing line reaches end of life.
