# Portable LPAR snapshot contract design

## Scope

Issue #313 defines a design contract only. [ADR 0082](../../adr/0082-portable-lpar-snapshot-contract.md)
governs the portable envelope, strict version boundary, and separation between replayable
configuration and non-replayable observations. Production models, serializers, commands, replay,
live-HMC research, dependencies, and migration code are excluded.

## Version 1 document

The stored artifact is one UTF-8 JSON object. JSON member names are case-sensitive. The root is:

```json
{
  "format": "hmc-mcp.lpar-snapshot",
  "version": 1,
  "captured_at": "2026-08-23T18:42:00Z",
  "source": {
    "hmc": {"uuid": "...", "name": "...", "version": "V11R1M0"},
    "system": {"uuid": "...", "name": "...", "machine_type_model": "...", "serial": "..."},
    "lpar": {"uuid": "...", "name": "example", "partition_id": 7}
  },
  "capabilities": [
    {"name": "affinity-scores", "version": 1, "supported": true, "collection": "derived"},
    {"name": "lpar-profile-record", "version": 1, "supported": true, "collection": "hmc-cli"},
    {"name": "runtime-placement", "version": 1, "supported": true, "collection": "hmc-rest"}
  ],
  "configuration": {
    "profile_name": "default",
    "native": {
      "media_type": "text/vnd.ibm.hmc.lssyscfg-profile;version=1;charset=utf-8",
      "data": "name=default,lpar_name=example,min_mem=4096,desired_mem=8192,max_mem=16384,proc_mode=shared,min_proc_units=0.5,desired_proc_units=1.0,max_proc_units=2.0,min_procs=1,desired_procs=2,max_procs=4,sharing_mode=uncap"
    },
    "normalized": {
      "memory_mib": {"minimum": 4096, "desired": 8192, "maximum": 16384},
      "processors": {
        "dedicated": false,
        "minimum": 0.5,
        "desired": 1.0,
        "maximum": 2.0,
        "virtual_minimum": 1,
        "virtual_desired": 2,
        "virtual_maximum": 4,
        "sharing_mode": "uncapped",
        "uncapped": true
      }
    }
  },
  "observations": {
    "observed_at": "2026-08-23T18:41:58Z",
    "runtime_placement": {
      "media_type": "application/vnd.hmc-mcp.runtime-placement+json;version=1",
      "data": {}
    },
    "scores": {
      "media_type": "application/vnd.hmc-mcp.affinity-scores+json;version=1",
      "data": {"current": null, "predicted": null}
    }
  }
}
```

All displayed root and identity members are required. `source.hmc.name`, `source.hmc.version`,
`source.system.name`, and `source.lpar.name` may be null when the HMC does not report them; UUIDs,
system machine type/model and serial, and the numeric partition ID may not be null. Stable UUIDs
are the primary identities. Machine type/model plus serial guards against a UUID copied from the
wrong system. Names are descriptive and never identity substitutes.

`captured_at` is the completion time of the capture. `observed_at` is the time represented by the
observation payload and must not be later than `captured_at`. Both are RFC 3339 timestamps with an
explicit offset; writers normalize them to UTC with `Z` and second precision.

## Capability context

`capabilities` is a list sorted by `name`. Each unique, non-blank name has integer `version`,
boolean `supported`, and `collection`, one of `hmc-rest`, `hmc-cli`, or `derived`. Version 1 defines
exactly three names: `lpar-profile-record` version 1 collected by `hmc-cli`,
`runtime-placement` version 1 collected by `hmc-rest` or `hmc-cli`, and `affinity-scores` version 1
collected by `hmc-rest`, `hmc-cli`, or `derived`. `lpar-profile-record` must be present and
supported. The other two may be absent (not evaluated), present and unsupported, or present and
supported. A corresponding observation is permitted only for a present, supported capability.

Unknown names, versions, or collection routes are invalid in a version-1 envelope. A new
capability name or version therefore requires envelope version 2. Absence remains distinct from
an evaluated but unsupported capability, and neither state is represented by null.

## Replayable configuration

`configuration.profile_name` names the captured HMC profile. Version 1 accepts only native media
type `text/vnd.ibm.hmc.lssyscfg-profile;version=1;charset=utf-8`. `native.data` is the exact single
UTF-8 attribute record returned by `lssyscfg -r prof -m <system> --filter
lpar_names=<lpar>,profile_names=<profile>`. Capture requires exactly one record. The record parser
must preserve every key/value pair, reject duplicate keys and record delimiters that do not parse,
and require its `name` and `lpar_name` to equal the envelope profile and LPAR names. Replay sends
the validated record as the data argument of `chsyscfg -r prof -m <target-system> -i <record>`;
normal command construction and authorization escape it as one argument. Artifact text is never a
shell command, and the system-wide `bkprofdata`/`rstprofdata` file is not part of this contract.

Version 1 deliberately accepts only the attribute-record subset already governed by ADR 0045.
The record is a non-empty comma-separated sequence of `key=value` pairs. Keys are non-empty ASCII
letters, digits, and underscores. Values are printable ASCII and may contain spaces and
semicolons, but not comma, equals sign, double quote, or a control character. Empty values are
valid. Duplicate keys are invalid. Quoted or otherwise escaped values are outside version 1 and
make capture fail with the offending attribute named; the implementation does not attempt to
decode an unverified HMC quoting grammar. After parsing, replay reconstructs the ordered pairs
through the repository's `build_attribute_record` boundary, which applies the same delimiter and
duplicate checks, then passes that result as one shell-escaped argument. Capture therefore refuses
an HMC profile that cannot be represented by this verified subset rather than producing a
snapshot whose safe replay is uncertain.

`normalized` contains exactly `memory_mib` and `processors`. `memory_mib` contains exactly positive
integer `minimum`, `desired`, and `maximum`, ordered minimum ≤ desired ≤ maximum. `processors`
contains exactly boolean `dedicated`; positive numeric `minimum`, `desired`, and `maximum`, ordered
minimum ≤ desired ≤ maximum; positive integer `virtual_minimum`, `virtual_desired`, and
`virtual_maximum`, similarly ordered; `sharing_mode`, one of `keep_idle_procs`, `share_idle_procs`,
`share_idle_procs_active`, `share_idle_procs_always`, or `uncapped`; and boolean `uncapped`.
Dedicated processor values must be integers and require `uncapped: false`; shared values may be
fractional. Memory is in MiB. No additional normalized property is valid in version 1.

Native data is replay authority. Before replay, the implementation derives every normalized field
required by version 1 from `native.data` and requires equality with the stored projection. The
mapping is exact: `min_mem`, `desired_mem`, and `max_mem` map to the three memory values;
`proc_mode=ded` maps to `dedicated: true` while `proc_mode=shared` maps to false;
`min_proc_units`, `desired_proc_units`, and `max_proc_units` map to the three processor values;
`min_procs`, `desired_procs`, and `max_procs` map to the three virtual values; and HMC
`sharing_mode` values `keep_idle_procs`, `share_idle_procs`, `share_idle_procs_active`,
`share_idle_procs_always`, and `uncap` map respectively to the normalized values of the same name
except `uncap`, which maps to `uncapped`. Normalized `uncapped` is true only for `uncap`. A missing
required native attribute, unknown mapped value, or inability to derive any required normalized
field is an unsupported version-1 profile and fails validation; no comparison is skipped. A
mismatch is malformed input. Replay never substitutes normalized values into an otherwise valid
native payload, and never executes strings from the artifact as shell commands.

## Non-replayable observations

`observations` is required even when no optional observations are available; then it contains only
`observed_at`. `runtime_placement` and `scores` are optional typed observation envelopes. Each
contains exactly `media_type` and `data`. Version 1 requires respectively
`application/vnd.hmc-mcp.runtime-placement+json;version=1` and
`application/vnd.hmc-mcp.affinity-scores+json;version=1`; `data` is a JSON object whose schema is
owned by that media type rather than the snapshot envelope. The payload is opaque to snapshot
validation except for JSON-object type. This is deterministic: the
envelope validator accepts any JSON object at that explicit opaque boundary and rejects other
types or members. Producers validate media-type data against its separately versioned schema.
Scores distinguish `current` and `predicted` within the affinity media type; neither has desired-
state meaning. Observations are never copied into an HMC profile, compared as replay
preconditions, or used to mutate a target. Unknown observation envelope members or media types are
rejected.

## Reserved command namespace

Future CLI commands are reserved under `hmc-mcp snapshot capture`, `validate`, `inspect`, and
`replay`. Future MCP operation identifiers are `snapshot.capture`, `snapshot.validate`,
`snapshot.inspect`, and `snapshot.replay`. This reservation prevents unrelated commands from
claiming the names; it is not documentation that those commands are currently implemented.

The reserved operations' runtime I/O, target selection, authorization, and mutation behavior are
owned by their future implementation design, not this persistence contract.

## Validation and failures

Parsing rejects invalid UTF-8, invalid JSON, a non-object root, and duplicate JSON member names
before constructing a snapshot. Implementations must bound parser resources, but operational
bounds do not change value-semantic conformance to this persisted format. Structural validation
then rejects unknown members, missing members, wrong types, blank identifiers, invalid
timestamps, duplicate or unsorted capabilities, inconsistent timestamps, invalid numeric ranges,
and configuration/observation boundary violations.

Each diagnostic contains the operation, an RFC 6901 JSON Pointer (root is `/`), the violated rule,
and a suggested correction. Diagnostics may quote bounded scalar identity values but never echo
`configuration.native.data`. All failures are fail-fast and side-effect-free. Validation performs
no network calls. Replay validation completes before target lookup or mutation.

A future replay design must reject unsupported format/version pairs, unrecognized native media
types, unsupported required capabilities, and native/normalized disagreement before mutation. It
must also validate the resolved target against the snapshot's HMC, system, and LPAR identity under
an explicitly authorized mapping policy. This contract defines the source identity that policy
consumes; it does not authorize target mapping, name rewriting, or physical-system compatibility
rules.

## Evolution and compatibility

`format` is immutable. `version` is the envelope and replayable-configuration schema major version.
Version-1 readers accept only 1; unknown versions may be identified by `inspect` but are invalid
for validate/replay. Any envelope or replayable field addition, removal, rename, type change,
closed-vocabulary expansion, default change, or semantic change requires a new integer version.
Observation data evolves by a new media-type version and matching capability version, which in
turn requires a new envelope version to admit that pair. Writers emit only the newest envelope
version they implement.

There is no best-effort parsing, silent field dropping, dual-format writer, or implicit upgrade
during replay. Any future conversion requires its own authorized contract for the exact source and
destination versions; version 1 defines no converter, metadata, or write behavior.

## Acceptance checks for implementation issue(s)

- Round-trip a complete version-1 fixture value-semantically through parse and serialize; JSON
  member ordering and insignificant whitespace are not contractual.
- Reject every missing required member, unknown member, wrong type, duplicate member, blank
  identity, malformed timestamp, later observation timestamp, duplicate/unsorted capability, and
  native/normalized mismatch with the precise JSON Pointer.
- Prove absent capability differs from unsupported capability and absent optional observation
  differs from a present null score.
- Prove both structural and replay validation reject unknown capability context and schema versions.
- Prove replay ignores every observation field and performs no I/O after any validation failure.
- Prove a future replay implementation validates target identity under its separately approved
  mapping policy before performing HMC I/O.
- Prove no diagnostic contains native profile payload content.

## Durable execution context

- Branch: `feat/portable-lpar-snapshot-contract-313`
- Base branch: `main`
- Guardrails: `just test`, `just smoke`, `just verify`
- Architecture: host `x86_64`; targets `amd64`, `arm64`, and `ppc64le`; relationship `included`
- ADR-index coupling: no index
