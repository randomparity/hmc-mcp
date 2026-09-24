# SR-IOV capacity granularity preflight (#1035)

## Problem

`assign_sriov_logical_port` sends any capacity from 1 to 100 with at most two decimals. The HMC
also requires an Ethernet logical port's capacity to be a multiple of the physical port's
`min_eth_capacity_granularity`, and refuses other values with HSCL1294 after a `chhwres` attempt.
`SriovPhysicalPort.minimum_capacity_granularity_percent` is always `None`.

## Evidence

Read-only probes on the authorized lab HMC (V10R3 M1060, 2026-09-24):

- `lshwres -r sriov --rsubtype physport --level {roce|ethc} -F ...,min_eth_capacity_granularity`
  is accepted at both levels. RoCE ports reported `1.0`; ports listed at `--level ethc` on another
  system of the same HMC reported `2.0`.
- A bogus-attribute control is refused at both levels ("An invalid attribute was entered"), so
  acceptance is attribute validation, not silence. The attribute does not appear in the default
  (no `-F`) listing.
- The branch's exact nine-field commands were run read-only at both levels for one RoCE adapter
  (8375-42A, the admitted model) and one ethc adapter (9119-MHE, outside admission, parser
  evidence only). The new fixture is that output with only location codes and system names
  redacted.
- The IBM `chhwres` reference binds an `eth` logical port's capacity to
  `min_eth_capacity_granularity` and a RoCE logical port's to `min_roce_capacity_granularity`.
  The assignment always creates `logical_port_type=eth`, so only the Ethernet attribute governs.

## Design

1. `list_sriov_physical_port_rows` (`src/hmcpctl/ssh/sriov.py`) appends
   `min_eth_capacity_granularity` to its projection at both levels. The roce/ethc exclusivity and
   adapter-id checks are unchanged.
2. `pcie.py` gains `_eth_capacity_granularity(row) -> Decimal | None`: a missing key, empty value,
   or `null` is `None` (not reported); otherwise the value must parse as a `Decimal` from 0.01
   through 100 (the range a two-decimal capacity can be a multiple of), or
   `HMCCLIError("malformed physical-port capacity granularity: ...")`.
3. `list_sriov_physical_ports` fills `minimum_capacity_granularity_percent` from that helper.
4. `_require_sriov_assignment_capacity_and_state` first checks
   `capacity % granularity != 0` when the helper returns a value, and raises
   `ValueError("capacity_percent <c> is not a multiple of the physical port's capacity
   granularity <g>%")`. This runs before any further SSH read and before any `chhwres`/`chsyscfg`.
   `None` skips the check, so the HMC stays the judge.

A new sanitized live capture, `tests/fixtures/sriov/sriov-physport-granularity-v10r3.json`, holds
the exact new commands at both levels for one RoCE and one ethc adapter. Tests that replayed the
old projection's captures against the parser (`tests/unit/test_sriov_ssh_contract.py` selection
cases, `tests/system/test_pcie_contract.py` RoCE acceptance) replay this capture instead. The old
captures stay byte-identical; their survey assertions still hold.

## Failure model

1. Actors and deployments: an MCP client or CLI operator using SR-IOV assignment or inventory
   inside `require_admitted_environment` (HMC V10R3 M1060, model 8375-42A).
2. Invariants and assets: no HMC mutation for a capacity the port will refuse; a port without a
   reported granularity behaves exactly as before; the physport exclusivity check.
3. Accepted failure classes:
   - An HMC release that rejects the new attribute fails the physport read, and with it
     assignment, SR-IOV inventory, and vNIC backing preflight (`vnic.py`), with an HMC error
     rather than silently skipping; accepted because the attribute was live-probed inside the
     admitted envelope, and widening admission (#667/#668) must re-probe it.
   - RoCE logical ports are not assigned by this tool, so `min_roce_capacity_granularity` is
     not read.
4. Covered elsewhere: widening SR-IOV admission (#667/#668, #871 non-goal); post-dispatch
   HSCL1294 reporting (#966). vNIC backing capacity and the live runner's 7.5 default SR-IOV
   capacity are follow-up candidates, not this change.

## Validation

- `focused-test`: physport projection includes the attribute at both levels —
  `tests/unit/test_sriov_ssh_contract.py`, replaying the new capture.
- `focused-test`: refuse (7.5 at 1.0, no `chhwres`), accept (4 at 2.0), absent (7.5 with no
  attribute, and with `null`, reaches `chhwres`) — `tests/unit/test_sriov_logical_port_operations.py`.
- `focused-test`: malformed granularity (`abc`, `0`, `1E-27`, `101`) raises `HMCCLIError` and inventory reports
  the parsed value — `tests/unit/test_sriov_logical_port_operations.py`,
  `tests/system/test_normalized_pcie_inventory.py`.
- Live: re-run the read-only physport probe with the branch's exact commands.
