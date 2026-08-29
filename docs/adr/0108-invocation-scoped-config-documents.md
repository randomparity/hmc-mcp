# ADR 0108: Use Invocation-Scoped Configuration Documents

## Status

Accepted

## Context

`hmc_effective_permissions` resolves the effective power guard for every granted
connection. Calling `build_config` independently for each connection rereads and reparses
`config.toml`, amplifying work by the connection count and allowing one report to mix file
versions. The report must still follow `build_config`'s environment-only branch and precedence,
must observe a fresh file on the next invocation, and must classify failures per connection.

## Decision

Add an internal configuration-document snapshot value containing the resolved path and parsed
TOML mapping. Let `build_config` optionally consume that value while retaining its existing
branching and merge rules. `resolve_power_guards` creates at most one snapshot per call and shares
it across connection resolutions; a snapshot-creation exception is classified for every affected
connection using the existing unresolved-report path. An ambient `HMC_HOST` remains environment
only and does not require a snapshot.

The snapshot is invocation-local. It is neither cached nor retained by the server.

## Consequences

One report reads and parses the configuration file at most once and all profile-backed rows use
the same document version. `HMCConfig` construction and validation still occur per connection,
preserving profile selection, environment precedence, and validation behavior. A document-level
failure is reported for every connection with the same closed classification. Existing
connection-scoped warning behavior is retained; sharing the exception does not broaden logging
deduplication across connection names.

## Considered & rejected

- **Cache the parsed document across calls.** judgment: freshness is part of the report contract;
  cache invalidation adds state and can make the next report describe an earlier file.
- **Call `_load_profile_from_document` directly from the permission report.** verified:
  `_load_profile_from_document` in `src/hmc_mcp/config.py` already owns profile selection,
  secret-environment resolution, environment-over-profile precedence, and validation. The caller
  would still have to duplicate `build_config`'s explicit/ambient-host gate, profile-selection
  trigger, `NoProfileSelectedError` fallback, and override merge contract. Supplying the document
  to `build_config` keeps that orchestration in its existing owner while adding only an internal
  input for data the caller has already read.
- **Keep one independent `build_config` call per connection.** verified: issue #536 records the
  repeated-read amplification and the possibility of rows from different file versions.
