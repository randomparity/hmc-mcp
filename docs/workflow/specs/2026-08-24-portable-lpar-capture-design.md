# Portable LPAR capture implementation design

## Scope and authority

Issue #314 implements the version-1 contract accepted by [ADR 0082](../../adr/0082-portable-lpar-snapshot-contract.md)
and its companion specification. ADRs 0083 and 0084 govern the affinity observation payloads.
This change installs capture, validate, and inspect surfaces in the supported Python API, MCP,
and CLI. Replay, conversion, target mapping, profile mutation, DPO control, and new dependencies
remain excluded.

## Architecture

`snapshot.py` owns the strict value model, duplicate-aware JSON parsing, bounded local document
reading, serialization, and inspection. It is presentation-neutral and performs no HMC I/O.
`operations_snapshot.py` composes existing REST inventory and SSH profile/affinity operations into
one capture. Server and CLI adapters delegate to those two modules; they do not duplicate schema
or collection logic.

Capture accepts a managed-system selector, LPAR selector, and profile name. It resolves exactly
one system and LPAR, reads exactly one native `lssyscfg -r prof` record, derives the normalized
memory and processor projection, and gathers placement and affinity observations when supported.
The completion timestamp is recorded after collection; the observation timestamp is recorded
immediately before observation collection. Capability entries state which optional reads were
attempted, their version, support status, and collection route. Resource-group affinity context is
carried inside the affinity observation data, never configuration.

Version 1 always attempts the profile, runtime-placement, and affinity collectors; there is no
caller switch that can silently omit one. A capability probe that authoritatively reports
unavailable produces a present `supported: false` capability and no corresponding observation.
A successful collector produces `supported: true` and its typed observation. Permission,
transport, timeout, malformed-output, or any other operational failure aborts capture unchanged;
it is never downgraded to unsupported. Resource-group collection preserves ADR 0084's narrow
`HSCLCA00` translation and propagates every other failure.

Runtime placement data contains exactly `state`, `rmc_state`, `processor_mode`,
`current_memory_mib`, `current_processor_units`, and `dedicated_processors`, projected from the
resolved REST LPAR without falling back to desired profile values. `state` is a nonblank string;
`rmc_state` is a nonblank string or null; `processor_mode` is `shared` or `dedicated`;
`current_memory_mib` is a positive integer or null; `current_processor_units` is a positive finite
JSON number or null; and `dedicated_processors` is a positive integer or null. Shared mode requires
`dedicated_processors: null`; dedicated mode requires `current_processor_units: null`. Current
resource values may be null when an inactive or partially reported LPAR has no runtime allocation;
the producer rejects wrong types, nonpositive values, and contradictory mode/value combinations.
Affinity data contains exactly `current`, `predicted`, and `resource_groups`. `current` contains
the existing ADR 0083 LPAR and system current-score results; `predicted` contains the existing
default-scenario LPAR and system prediction results; and `resource_groups` contains the ADR 0084
all-groups current and calculated result envelopes, including their selector and capability state.
The producer validates these exact media-type payloads before constructing the snapshot. An
ADR 0084 capability-unavailable resource-group result remains data within the supported overall
affinity observation; it does not erase the available partition/system scores.

The exact supported contracts are:

| Surface | Contract |
|---|---|
| Python | `capture_lpar_snapshot(hmc, config, system_name_or_uuid, lpar_name_or_uuid, profile_name) -> LparSnapshot`; `parse_snapshot(text) -> LparSnapshot`; `serialize_snapshot(snapshot) -> str`; `read_snapshot(path) -> LparSnapshot`; `inspect_snapshot(text) -> SnapshotInspection`. |
| MCP | `hmc_snapshot_capture(system_name_or_uuid, lpar_name_or_uuid, profile_name, profile=None) -> dict` with operation `snapshot.capture`; `hmc_snapshot_validate(document) -> dict` with operation `snapshot.validate`; `hmc_snapshot_inspect(document) -> dict` with operation `snapshot.inspect`. Local operations accept JSON text, not paths. |
| CLI | `hmc-mcp snapshot capture SYSTEM LPAR PROFILE_NAME --output PATH`; `snapshot validate PATH`; `snapshot inspect PATH`. |

Capture returns the complete JSON-compatible envelope. Successful validation returns
`{"valid": true, "format": "hmc-mcp.lpar-snapshot", "version": 1}`. Inspection performs only
bounded duplicate-aware root decoding and returns `format`, `version`, and `supported` (true only
for the exact version-1 pair), so callers can identify a newer document without treating it as
valid. Validation errors raise `SnapshotValidationError` through Python and MCP. CLI validation
prints the success object as JSON and exits 0; inspection prints its decoded result and exits 0
when root inspection succeeds. Parse, I/O, or validation failures go to stderr and exit 1; Typer
argument misuse exits 2. No command named `replay` is installed.

CLI capture publishes UTF-8 JSON failure-atomically: create a private temporary file in the
destination directory, write and close it, then install it with an atomic no-replace operation.
An existing destination is never replaced. Serialization, short-write, disk-full, close, install,
or handled failure attempts to remove the temporary file and leaves no destination artifact.
Process death or host failure may leave a recognizably prefixed private temporary file, but never
publishes that file at the requested destination.

## Validation and diagnostics

Version 1 is closed at every envelope-owned object. Every raw-text and file entry point rejects
input whose UTF-8 encoding exceeds 1 MiB before JSON construction. Parsing rejects invalid JSON, duplicate JSON
members, wrong discriminator/version, and all structural or semantic
violations defined by the companion specification. `SnapshotValidationError` reports the
operation, RFC 6901 pointer, violated rule, and suggested correction. It may include bounded
identity scalars but never native profile data.

Native profile records use the existing repository attribute-record grammar. Capture and local
validation require `name` and `lpar_name` identity agreement and exact agreement between every
derived normalized field and the stored projection. Observation envelopes remain opaque JSON
objects after their media type and object shape are checked. Serialization is deterministic but
JSON member ordering is not contractual.

## Threat model

Added trust boundaries are local snapshot JSON supplied by an operator, a local output path, and
HMC profile/observation output. Existing widened boundaries are SSH command construction and MCP
tool dispatch. The untrusted actors are an authenticated MCP caller, a local CLI operator, and a
compromised or malformed HMC response.

- Snapshot text is size-bounded, duplicate-aware, strictly typed, and closed-schema validated
  before values are constructed. Errors omit native data.
- Local file reads use an explicit path, reject non-regular files and oversized input, decode only
  UTF-8, and do not follow document-provided paths. Capture uses same-directory failure-atomic
  no-replace publication and best-effort cleanup of its recognizably prefixed private temporary
  file after handled write/install failures.
- HMC selectors continue through existing resolution and shell-quoting boundaries. Native profile
  output is parsed as data and is never executed.
- MCP tools retain existing connection/target authorization metadata and are read-only. Local-only
  validation and inspection do not gain HMC reach.

Symlink policy beyond regular-file validation, filesystem ACLs, HMC credential protection, replay
authorization, and hostile administrators of the local machine are out of scope because this
change neither creates those trust domains nor mutates remote state.

## Tests and acceptance

Tests round-trip a complete fixture; cover every required/unknown/wrong/duplicate member class,
timestamps, capability states, native/normalized mismatch, and contradictory processor fields;
prove diagnostics do not leak native data; and prove validation/inspection perform no HMC I/O.
Capture tests mock only REST and SSH boundaries and verify observations are never present in the
configuration payload. They assert the exact placement and affinity producer schemas, including
the all-groups resource context. They also prove unavailable probes omit observations while permission,
transport, timeout, and malformed-output failures abort capture. Application tests pin every
signature and result above, MCP registration/security, Python exports, CLI exit streams/statuses,
existing-destination refusal, injected write/install cleanup, malformed-file diagnostics, and the
absence of replay.

## Durable execution context

- Branch: `feat/portable-lpar-capture-314`
- Base branch: `main`
- Guardrails: `just test`, `just smoke`, `just verify`
- Architecture: host `x86_64`; targets `amd64`, `arm64`, `ppc64le`; relationship `included`
- ADR-index coupling: no index
