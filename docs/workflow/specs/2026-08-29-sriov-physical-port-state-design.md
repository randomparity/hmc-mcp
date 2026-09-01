# SR-IOV physical-port state design

Issue: [#557](https://github.com/randomparity/hmc-mcp/issues/557)  
Decision: [ADR 0112](../../adr/0112-sriov-physical-port-level-selection.md)

## Scope and outcome

Extend the existing normalized SR-IOV physical-port inventory to read both
evidence-supported HMC physical-port levels, `roce` and `ethc`, and expose a
validated up/down state. Retain ADR 0056's HMC V10R3 M1060 and 8375-42A admission
boundary, existing public operation shape, and captured POWER9 fixture.

The source authority is issue #557, its frozen `WORK:SCOPE` charter, issue
#214's version-labelled live capture, and issue #573's sanitized paired-probe
evidence. No migration or adapter mutation is in scope.

## Design

`ssh.network.list_sriov_physical_port_rows` validates that `adapter_id` is a
positive decimal integer before issuing commands. It then runs the same
projected, adapter-filtered read at both `--level roce` and `--level ethc`.
Exactly one level must return rows. Both empty is unsupported; both non-empty is
ambiguous. Every accepted row must repeat the requested adapter ID and carry a
`phys_port_type` matching the level that returned it.

`operations.pcie.list_sriov_physical_ports` retains its existing environment
admission call. It maps state `1` to `up` and `0` to `down` in the existing
`availability` field. Blank or any other state raises `ValueError`; it is not an
unknown availability value.

The captured POWER9 JSON stays byte-for-byte unchanged. Tests load its exact
command/projection/output contract and supply the companion `ethc` empty result
without modifying captured evidence.

## Failure behavior

- A command failure remains an `HMCCLIError` from the SSH boundary.
- Zero rows at both levels fails as unsupported rather than available-empty.
- Rows at both levels fail as ambiguous.
- A non-positive adapter ID, mismatched adapter ID/type, or state outside
  `0`/`1` fails as malformed.
- Existing version/model rejection still returns capability-unavailable before
  either physical-port command runs.

## Security boundaries

The change widens one existing SSH command boundary by adding a second allowed
literal level. The local operator controls `system_name` and `adapter_id`; the
authenticated HMC controls stdout. Existing `shlex.quote` encodes the system
name and `build_filter` plus `shlex.quote` encodes the adapter filter. A
positive-decimal check bounds the adapter selector before either command. CSV
shape checks, exact adapter/type matching, one-nonempty-level cardinality, and
the two-value state map control HMC output. Failures expose only actionable
operation context, not credentials. No entry point, authorization decision,
secret, mutation, network destination, or permission is added. Compromised HMC
output and authorization policy are out of scope because the existing SSH and
operation layers own those controls.

## Verification

Focused tests prove: `roce` and `ethc` selection, both empty, both non-empty,
invalid adapter IDs, mismatched adapter identity/type, state up/down mapping,
malformed state, unchanged environment rejection, and the existing captured
fixture contract.
Repository gates then run with `just verify` and the all-files hook command.
