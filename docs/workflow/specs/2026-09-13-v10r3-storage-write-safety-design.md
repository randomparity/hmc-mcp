# V10R3 storage write safety design

## Problem

Issue #779 records an HMC V10R3 VolumeGroup write that returned HTTP 500 after
changing physical-volume metadata. The current client neither proves nor reports
post-dispatch state. It also sends a MiB CLI value directly to HMC `DiskCapacity`,
whose observed unit is GiB; presents VolumeGroup GiB values as MiB; and lacks a
bounded typed-`Accept` compatibility path for audited writes.

## Scope

The change covers the `StorageClient` VolumeGroup mutation set, its direct storage
operation/CLI/MCP callers, and VolumeGroup inventory results. `capacity_mib` remains
the supported public input. It must be positive and divisible by 1024, then converts
to the integral GiB XML value at the document boundary. `VolumeGroup` replaces
`capacity_mib`/`free_space_mib` with `capacity_gib`/`free_space_gib`; no aliases are
kept on this pre-release result surface. A parsed free-space value greater than a
present group capacity becomes `None` and carries a diagnostic rather than a false
capacity claim.

The audited typed-UOM POST/PUT set is the VolumeGroup and VirtualIOServer storage
write calls in `client_storage.py`. Those calls may make one compatibility retry only
for HTTP 406 by changing `Accept` to generic UOM while retaining the endpoint's
required `Content-Type`; all other client writes and web/job APIs retain their
existing headers after the audit documents why. A 5xx has no retry path.

For each audited VolumeGroup mutation, the client snapshots the relevant group or
VIOS group list before dispatch. A 5xx triggers one readback, then raises an HMC error
that says the request may have changed state, records a safe before/after comparison
or failed readback, and tells the operator not to retry. It never rolls back.

`docs/recipes/lpar-iso-install.md` is excluded: open PR #777 owns its correction,
with operator approval recorded in #779's `WORK:SCOPE` annotation. Live validation is
also excluded and remains an operator task in an approved disposable environment.

## Failure model

- **Actors and deployments**
  - Authenticated terminal and MCP operators against HMC V10R3 storage APIs.
  - CI runs mocked request and operation tests; it does not mutate an HMC.
- **Invariants and assets at stake**
  - A failed VolumeGroup response can conceal irreversible storage state changes.
  - `capacity_mib` callers must not create a disk larger than requested.
  - Published storage result keys must state their actual GiB units.
- **Accepted failure classes**
  - Readback can fail after a 5xx; the error reports that failure and still forbids retry.
  - Non-audited endpoints keep their established header behavior because #779 does not
    establish their V10R3 compatibility.
- **Covered elsewhere**
  - The ISO-install recipe correction is owned by PR #777.
  - Disposable-environment live validation is owned by the operator.

## Design

`client_storage.py` owns HMC-specific snapshot, readback, typed-header fallback, and
error composition. Its helper accepts an explicit snapshot/readback pair, so the exact
audited mutation set is visible at each call site. It catches only `HMCError` values
with status 500–599, performs one readback, and raises a new error chained from the
original. The comparison is compact: absent/present state and the normalized relevant
VolumeGroup fields, never raw response bodies.

`documents/storage.py` owns protocol-unit conversion and rejects non-integral-GiB MiB
inputs before creating XML. `operations/storage/resources.py` owns result naming and
impossible-inventory handling. Existing CLI and MCP layers serialize those operation
results; they only rename display headings and field references. LPAR attachment keeps
its public MiB inputs and result field because it reports the caller's requested unit.

ADR 0136 records why the client readback is diagnostic rather than an automatic retry or
rollback. ADR 0025 remains the authority for replacing misleading pre-release public
names without aliases.

## Success

- For the named VolumeGroup mutation set, an HTTP 5xx produces exactly one readback
  attempt and an error that says possible side effect and do-not-retry; no second write
  occurs.
- `capacity_mib` values divisible by 1024 produce the corresponding integral GiB
  `DiskCapacity`; zero, negative, and non-divisible inputs fail before HTTP.
- The named typed-UOM write set retries once only on 406 with generic UOM `Accept`;
  a successful first request, 5xx, or another status does not retry.
- `list-vgs` publishes GiB-named fields and uses null plus a diagnostic for free space
  greater than its present capacity.
- Generated tool documentation, operator documentation, changelog, and focused tests
  describe the delivered contract; the #777 recipe remains excluded.

## Validation

- Focused transport tests assert exact first/fallback headers and request counts for 406,
  5xx, and non-406 failures.
- Focused storage client tests assert before/after readback and error text for every
  audited mutation shape.
- Focused document, operation, CLI, MCP, and LPAR tests assert conversion, validation,
  result-key replacement, null diagnostics, and preserved attachment semantics.
- `just tool-docs`, `just static`, `just test`, `just smoke`, and `just verify` prove
  generated artifacts, repository checks, and executable behavior. Live validation is
  intentionally not run without the approved disposable environment.
