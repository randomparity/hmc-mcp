# SR-IOV capacity granularity in LPAR and vNIC prevalidation (#1081)

## Problem

#1035 made `assign_sriov_logical_port` refuse a `capacity_percent` that is not a multiple of
the physical port's `min_eth_capacity_granularity`. Two other paths that request SR-IOV
capacity do not:

- `prevalidate_lpar_pcie_assignments` (`operations/lpar/assignments.py`), run by LPAR create,
  provision and modify before any HMC write. It checks adapter and port health and the per-port
  capacity sum, but not granularity. An off-granularity request passes, the LPAR is created,
  and the HMC then rejects the logical port (`HSCL1294`), leaving a partial LPAR.
- `add_vnic` (`operations/virtualization/vnic.py`). Its preflight checks only
  `used + requested > 100`.

## Scope

**Owner.** `operations/virtualization/pcie.py` already owns parsing
(`_eth_capacity_granularity`) and the refusal. It stays the owner and exposes both halves:

- `eth_capacity_granularity(row) -> Decimal | None` — the existing parser, renamed public.
  Unchanged behaviour: `""`/`"null"`/absent → `None`; a value that is not a finite decimal in
  `[0.01, 100]` → `HMCCLIError("malformed physical-port capacity granularity: …")`.
- `require_capacity_granularity(capacity, granularity) -> None` — raises
  `ValueError("capacity_percent <c> is not a multiple of the physical port's capacity
  granularity <g>%")` when `granularity` is not `None` and `capacity % granularity != 0`.
  The #1035 call site uses it, so its message is unchanged.

**Callers.**

1. `_analyze_assignment_requests` returns, per `(adapter, physical)`, the tuple of individual
   requested capacities (direct logical ports and vNIC backings, in request order) instead of
   their sum. `_validate_sriov_inventory` checks each item with `require_capacity_granularity`
   against `SriovPhysicalPort.minimum_capacity_granularity_percent` (already parsed by
   `list_sriov_physical_ports`) after the port health check, then sums for the exhaustion
   check. Checking items, not the sum, matters: 2.5 + 2.5 sums to a multiple of 1.0 yet each
   item is refused by the HMC.
2. `_preflight_add` parses the selected port row's granularity into
   `_VnicPreflightContext.granularity`; `add_vnic` calls `require_capacity_granularity`
   beside the existing exhaustion check. It stays after the verified-retry (unchanged) return,
   matching the exhaustion check's placement, and before `add_vnic_backing`. Parsing happens in
   `_preflight_add`, so a malformed granularity on the selected port fails closed with
   `HMCCLIError` even on a verified retry — the same as prevalidation, whose port listing
   already parses every row.

No obsolete path remains; no public contract changes other than two new module-level names
in `pcie.py` (not in the ADR 0118 facade). No ADR: the refusal contract is #1035's, reused.

### Failure model

1. **Actors and deployments:** a local operator or MCP client driving create / provision /
   modify / `add_vnic` against one HMC.
2. **Invariants and assets:** no HMC mutation (`mksyscfg`, `chsyscfg`, `chhwres`) after a
   request item whose capacity is not a multiple of a granularity the selected port reports;
   hence no partial LPAR from that cause. Error types unchanged (`ValueError` for the refusal,
   `HMCCLIError` for malformed granularity).
3. **Accepted failure classes:** a port reporting no granularity is not refused — the HMC
   judges, as in #1035. Non-Ethernet (RoCE/FC-specific) granularity is out of scope
   (operator-approved exclusion). Granularity changing between prevalidation and mutation —
   hardware-fixed per port; not reachable in practice.
4. **Covered elsewhere:** the direct `assign_sriov_logical_port` refusal (#1035); live-test
   runner default capacity (#1082).

## Success

- Prevalidation refuses an off-granularity direct logical-port or vNIC-backing item with
  `ValueError` before `create_and_stamp_lpar` is reached; on-granularity and no-granularity
  requests still pass.
- `add_vnic` refuses off-granularity capacity with `ValueError`; `add_vnic_backing` is not
  awaited.
- A malformed port granularity raises `HMCCLIError` on all three paths.
- CHANGELOG `[Unreleased]` records the fix.

## Validation

- `focused-test`: `tests/lpar/test_pcie_assignments.py` — per-item refusal (7.5 on 1.0;
  2.5 + 2.5 on one port), vNIC-backing item refusal, on-granularity and `None` pass, malformed
  → `HMCCLIError` (an existing-behaviour pin: the port listing already parses it); request
  analysis returns per-item tuples. Red before the change: no raise, and create proceeds.
- `focused-test`: `tests/network/test_vnic_operations.py` — `add_vnic` 7.5 on 1.0 refused with
  no `add_vnic_backing` call; 4 on 2.0 dispatches; malformed → `HMCCLIError`. Red: mutation
  dispatched.
- `focused-test`: `tests/unit/test_sriov_logical_port_operations.py` existing #1035 cases stay
  green (shared helper preserves message).
- Live: lab HMC via `docs/live-testing.md` — granularity readback; off-granularity create
  refused with no partition created (`lssyscfg` before/after). The live runner's 7.5% default
  (#1082) is already refused by the direct-assign path since #1035; this change adds no new
  runner failure.
