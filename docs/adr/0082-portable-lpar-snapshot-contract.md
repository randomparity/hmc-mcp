# 0082 — Version portable LPAR snapshots as JSON envelopes

## Status

Accepted (2026-08-23)

## Context

HMC-native profile backup is deployable only through the HMC that owns the backup file. The
repository also exposes reusable processor and memory vocabulary and live partition inventory,
but it has no local persistence contract that can carry replayable configuration together with
the context needed to interpret it. Runtime placement and affinity scores are observations, not
configuration: treating them as replay input would turn a diagnostic fact into an unsafe desired
state.

The first contract must be implementable without claiming that future HMC releases, unknown
capabilities, or a newer snapshot schema can be replayed safely.

## Decision

Portable LPAR snapshots are UTF-8 JSON objects with the exact top-level discriminator
`"format": "hmc-mcp.lpar-snapshot"` and integer `"version": 1`. Version 1 is the contract in the
companion specification. A snapshot is an envelope containing capture time, source HMC identity,
managed-system identity, LPAR identity, capability context, a replayable configuration payload,
and a separate observations payload. Configuration and observations never share fields.

The configuration payload carries the selected named HMC profile as the exact UTF-8 attribute
record returned by `lssyscfg -r prof` plus the normalized configuration fields hmc-mcp
understands. Replay parses that record as data and supplies the validated record to
`chsyscfg -r prof -i`; it never executes artifact text as a command. The native record is the
replay authority; the normalized projection is portable inspection and validation context. A
future replay operation must reject disagreement between the two rather than choosing one.

Observations carry their own `observed_at` timestamp and include runtime placement, current
scores, and predicted scores only when available. They are informational and must be ignored by
replay. Capability entries identify the vocabulary and collection route used at capture time;
absence remains distinct from a supported capability whose value is null.

Reserve one command namespace for future implementation: CLI commands live under
`hmc-mcp snapshot`, and MCP operation identifiers under `snapshot.*`. Version 1 reserves
`capture`, `validate`, `inspect`, and `replay`; this ADR does not make those commands installable.

Readers validate the entire envelope before use. Unknown top-level or nested fields are rejected
in version 1, as are missing required fields, wrong JSON types, invalid RFC 3339 timestamps,
blank identifiers, duplicate capability names, and observation fields placed in configuration.
Diagnostics identify the JSON Pointer and the violated rule without echoing opaque profile data.
Validation performs no HMC I/O. A future replay design must require an exact supported version,
recognized capability vocabulary, target-identity validation, and internally consistent native
and normalized configuration before mutation; target mapping policy is not authorized here.

Compatibility is major-version based. Version 1 readers accept only version 1. A schema change to
the envelope or replayable configuration requires version 2; observation payloads evolve under
their own media types and capability versions because replay never consumes them. Writers always
emit the newest envelope version they implement. A reader may inspect the discriminator and
version of a newer document but must reject validation or replay as unsupported. No compatibility
shim, silent field dropping, or implicit upgrade during replay is permitted. Conversion behavior
requires a separately authorized contract for its exact source and destination versions.

## Consequences

Snapshots remain portable files while retaining HMC-native replay fidelity. Consumers can reason
about unavailable observations and capabilities without confusing absence with null. Strict
validation makes evolution deliberate and prevents a newer writer from being accepted under
older semantics. Future replay and conversion designs cannot silently weaken the contract.

The contract duplicates some configuration in native and normalized forms. That duplication is
intentional: the former is replay authority and the latter is stable inspection context, with
disagreement treated as malformed input.

## Considered & rejected

- **Persist only HMC-native backup files.** verified: `server_tools/profiles.py` documents that backup
  paths are on the HMC filesystem, so the result is neither a local portable artifact nor a
  carrier for observations and capability context.
- **Persist only normalized hmc-mcp fields.** judgment: normalization cannot preserve HMC fields
  unknown to the installed hmc-mcp release and therefore cannot be lossless replay authority.
- **Wrap the system-wide `bkprofdata` file.** verified: `server_tools/profiles.py` exposes backup and
  restore for every LPAR profile on a managed system, so using that artifact as one LPAR snapshot
  would create an unsafe system-wide replay boundary.
- **Mix placement and scores into configuration.** verified: issue #313 explicitly classifies
  current and predicted scores as non-replayable; mixing them makes accidental replay possible.
- **Allow unknown fields within a major version.** judgment: silently discarding an unknown field
  could discard replay semantics while still reporting the document valid.
- **Use separate schemas for capture and replay.** judgment: two overlapping formats create drift;
  one envelope with a hard configuration/observations boundary is smaller and testable.
- **Define conversion mechanics in version 1.** judgment: no destination version exists, so an
  output protocol would preempt a future migration decision outside this issue.
