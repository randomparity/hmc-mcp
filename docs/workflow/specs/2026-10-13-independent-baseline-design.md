# Independent live-window baseline

## Problem and scope

Issue #1479 and #1506 requirement 8 require a runnable independent read-only
capture and before/after comparison. The campaign operator approved this scope
on 2026-10-13. Release documentation belongs to #1514, orchestration to #1513,
and mutations/recovery to the existing arms/operator. This script does not run
arms. Existing per-arm baselines remain owned by their arms; no migration is needed.
[ADR 0213](../../adr/0213-independent-baseline-snapshots.md) records the interface.

## Interface and data flow

`scripts/live_test_baseline.py capture --system NAME --output PATH
[--profile NAME] [--vios-id ID ...] [--dry-run]` reads one managed system.
Each repeated positive VIOS partition ID explicitly authorizes its mapping and
volume-group reads. No VIOS-side reads run when none are selected. Profile
selection uses the existing `build_config` environment/profile precedence;
record the effective host in the private snapshot, not credentials.
Dry-run prints the read categories and selectors without loading credentials,
opening a connection, or creating an output. `compare BEFORE AFTER` is offline.
Exit 0 means capture succeeded or validated snapshots match; 1 means drift;
2 means invalid input, unreadable state or an incomplete capture/comparison.

Use existing SSH transport and strict header parsers for slot ownership,
partitions (`lpar_id,name,lpar_env,state`), profiles (`io_slots`), and SR-IOV
adapters, physical ports and configured logical ports. Profile reads preserve
the ADR 0165 command and validate the ADR 0166 value grammar. Physical ports
use the existing three-level read. Dedicated adapters have no active port reads.
Read system name/model/serial through a header-bearing system query to bind identity.
VIOS IDs must resolve to a listed `vioserver` partition. Use the existing authenticated REST GET and Atom parser below permissive list
helpers: reject blank bodies, non-Atom envelopes, absent resources or missing
SCSI mapping containers before projection. An explicit empty Atom volume-group
feed or mapping container is valid. Preserve server/client/target/backing identity,
including a valid server-only unmapped adapter or empty optical drive; absence
of a server identity or malformed present target/backing data is unreadable.
Read each listed group's
volumes through the existing quoted `viosvrcmd`/`lsvg -lv` command builder.
Capture volume identity, type, allocation counts, state and mount point; retain
group identity even when no volumes exist. Resolve selected VIOS UUIDs within
the selected system; validate group UUID/name before issuing volume reads.

The version-1 JSON document contains an effective-host/system/VIOS scope and
named row tables: system, partitions, slots, profiles, SR-IOV adapters/physical/
logical ports, VIOS mappings/groups/volumes. Explicit tables and row schemas
permit validation on load and capture; unexpected/missing keys, duplicate JSON
keys, wrong types, duplicate row identities, or incomplete selected VIOS/table
coverage fail closed. A valid HMC no-results sentinel represents an empty table;
blank output is unreadable. System identity requires exactly one matching row.
A completed read is not a transaction: the operator quiesces other changes.

Compare scope and validated tables structurally. Ignore only table-row ordering
and profile slot entry ordering; preserve duplicates as errors and preserve
field values, volume allocation/state and mapping identities. Output only changed
category names and counts, not resource values. Snapshots remain private local
artifacts: create mode 0600, require an existing parent, refuse existing output
(including symlinks), install only after successful capture/validation, and
remove owned temporary files on failure. Raw identifiers never enter public output.

## Success and validation

The script and its dedicated test module implement capture, explicit VIOS selection,
offline comparison, dry-run, strict shape handling and private no-overwrite output.
Tests mock SSH/REST boundaries and prove no mutation command reaches them; exercise
empty versus unreadable replies, unknown/non-VIOS selectors, drift, order-only
changes, incomplete/malformed snapshots, injection-shaped selectors and failures.
Run focused pytest with `--no-cov`, fault-inject a comparison defect to observe
red, then restore. Run repository `just verify` and pinned prek hooks before push.
Only offline proof is authorized for this campaign; no hardware claim is made.
Add minimum instructions to `docs/live-testing.md` and `[Unreleased]` changelog.

## Failure model

- Actors and deployments: a trusted local operator or the #1513 command; HMC
  V10/V11 reachable through existing authenticated SSH/REST transports; offline CI.
- Invariants/assets: read-only remote state, exact scoped comparison, no incomplete
  success, private snapshots, existing output preservation, credential secrecy.
- Accepted failures: concurrent remote changes can cause conservative drift or
  non-atomic observations; operator must keep the window quiescent. Deliberate
  snapshot forgery by its owning operator is outside evidence integrity. Unsupported
  remote field renderings fail closed rather than guessing. Runtime port state
  changes may cause drift requiring operator inspection.
- Covered elsewhere: transport TLS/SSH verification and timeouts by existing
  transports; window authorization/order by #1513; recovery by existing arms/operator;
  release publication/docs by #1514. Live hardware proof needs separate authorization.

## Threat model

Trust boundaries are local argv and snapshot JSON, HMC/VIOS replies, and private
output paths. Use fixed read operations, quote system names, validate numeric IDs
and plain VIOS group names before the nested shell, validate document schemas,
and do not print response bodies or credentials on failure. Refuse output overwrite
and symlinks using exclusive creation; temporary content stays private.
