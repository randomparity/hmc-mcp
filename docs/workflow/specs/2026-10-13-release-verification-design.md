# Release-frozen operation verification

## Problem and authority

Issue #1508 implements #1506 Requirement 4 and accepted ADR 0212. The current
projection calls `derive_states` on development closures; a controlled fixture
edit changes `current` to `stale`. The runtime reader independently ages evidence.
Both paths contradict the approved last-release presentation contract.

## Scope and success

The catalog continues to determine projected operation membership, implementation,
and runtime eligibility exactly. A committed release snapshot determines verification,
reason and observed_at. Changing a closure or catalog observation during development
cannot change these three projected fields. Explicit release validation must detect
the resulting divergence. Missing snapshot rows for recorded operations are unevidenced;
operations without catalog records remain absent from the sparse projection and resolve
unrecorded. Seed the snapshot from the resource at tag v0.1.0, preserving its 146 rows
(17 current, 90 stale, 39 unevidenced), rather than deriving today's source.

No renderer, CI, claimed-set, gate/report, promotion, window or runbook work is included;
those belong to #1509, #1510, #1511, #1478, #1512, #1513 and #1514 respectively.
No hardware execution or tag. The existing versioned v0.1.0 claims file is untouched.

## Design and ownership

Extend the inventory owner; no new module or dependency. Store
`docs/capabilities/release-verification.json` as format_version integer 1 and a sorted
operations array. Each row has exactly operation, verification, reason and observed_at.
The states and reason/timestamp consistency follow the existing package resource contract.
The snapshot contains historical rows, so removed operations may remain until promotion;
projection ignores rows outside today's recorded registry intersection. Release equality
compares the entire freshly derived verification snapshot, detecting additions/removals too.

`load_release_verification(path)` validates the closed shape, unique IDs/keys, order,
state vocabulary and UTC-second timestamps, with a 1 MiB input ceiling. Invalid or missing
input fails the inventory gate before a projection write. Reuse existing JSON and timestamp
primitives where appropriate. No implicit snapshot writer is added to ordinary regeneration.
`render_release_verification(records, registry, repo_root, now)` computes canonical snapshot
JSON using derive_states with age_staleness=False. `render_runtime_projection(records,
registry, snapshot)` combines catalog structure with validated frozen rows and sparse defaults.
`--release` additionally compares derived verification to the loaded snapshot and reports drift;
it checks before writing a requested projection. `just capability-metadata` keeps its command.

Artifact `validate(artifact_dir, project_root, *, release=False)` retains its existing byte
checks. Optional trailing `--release` in the CLI calls the inventory script using sys.executable,
its absolute path under project_root, cwd=project_root, and --release. Nonzero returns an
artifact validation failure. This explicitly opts into closure equality without making
ordinary builds require release evidence. It executes the operator-selected checkout as the
existing build workflow does; it is not an archive-controlled command.

Remove runtime age resolution and its obsolete clock arguments from the pre-release reader,
CLI row builder and MCP maturity middleware. The parser still validates timestamp consistency.
These direct callers migrate together; no inert compatibility clock remains. Regenerate the
package projection and docs/tools through their recipes. #1509 owns the generator-specific
call-site and two-date rendering test. CHANGELOG records user-visible frozen discovery.

## Global constraints

Python 3.11–3.14 on amd64; no dependency or toolchain change. Preserve the six-name stable
hmcpctl.api facade. Authorization and runtime eligibility remain existing-runtime-guards.
No raw HMC evidence, identifiers or local paths enter tracked/public artifacts.

## Failure model

- Actors and deployments: local maintainers running inventory/build checks; repository CI;
  installed CLI and MCP discovery consuming the packaged resource.
- Invariants/assets: exact catalog structure, historical verification without invented evidence,
  deterministic regeneration, malformed snapshot rejection, release closure mismatch detection.
- Accepted failures: historical snapshot entries can outlive removed catalog entries because
  Git snapshot history represents a release; no claim of current environment fitness. Local
  concurrent malicious checkout replacement is outside this trusted maintainer workflow.
- Covered elsewhere: release claimed-set age ceiling #1478; operator authorization and live
  recovery #1513; promotion writer #1512; documentation rendering regression #1509.

## Threat model

- New boundary: committed snapshot bytes, possibly malformed in a proposed change; local
  maintainers/CI validate closed keys, bound input, duplicates, vocabulary and timestamps.
- Existing boundary: packaged projection parser retains its closed-shape and size checks.
- Artifact release invocation uses fixed argument names and the explicitly selected checkout;
  no shell interpolation, network, credentials or archive-derived executable paths.
- Out of scope: malicious checkout code execution, already trusted by setup/build/tests;
  authenticity of operator-provided observations remains the existing evidence-review contract.

## Validation

Fixture closure edits must preserve ordinary generated bytes and gate success while release
mode fails; matching release data passes independent of observation age. Mutation cases cover
malformed snapshot shape, duplicate IDs/keys, order, timestamp/state consistency and size.
Catalog membership/implementation drift still fails exact projection checking. Sparse missing
rows retain unevidenced/unrecorded distinction. Tag seed is compared to v0.1.0 resource fields.
Runtime current and failed evidence stays frozen after 90 days in real reader-backed CLI/MCP
paths. Artifact tests prove opt-in invocation, error propagation and ordinary byte validation.
Run focused tests, static checks, then final just verify and pinned prek; CI covers four Pythons.
