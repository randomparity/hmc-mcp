# Provision refuses a dedicated PCIe slot (#1390)

## Problem

`hmc_provision_lpar` accepts `assignments.dedicated`, writes the slot into the new
partition's profile, and then powers the partition on with no partition profile. The
PowerOn activates the current configuration, so the running partition never owns the
slot. Live on V10R3 the lpar-power arm's `pcie-slot-owned` assertion failed
(docs/live-testing.md, "The lpar-power arm").

## Decision

The operator chose, on 2026-10-13, to refuse the argument now and to fold the activation
design into #1225. The issue's two activation alternatives are superseded:

- Activating with the profile would discard the virtual Ethernet and vSCSI adapters
  provision adds to the current configuration (#1345).
- Adding the slot by DLPAR after power-on needs RMC and a running operating system, which
  a partition at firmware does not have.

#1225 (epic #1215) writes every provision change into the profile and activates from it,
which removes the conflict. Until it lands, provision refuses a dedicated slot.

## Behaviour

- `validate_provision_assignments` in `operations/lpar/provision.py` raises `ValueError`
  when `assignments.dedicated` is non-empty. It runs first in the operation's preflight,
  and at the MCP tool and CLI entry points before any HMC session is opened. A dry run
  is refused the same way.
- The message names the verified path, which the bare-CEC recipe
  (docs/recipes/bare-cec-lpar.md) follows: create the partition (`hmc_create_lpar`,
  optionally with `assignments.dedicated`), or assign the slot to its profile with
  `hmc_assign_dedicated_pcie_slot` while it is not activated; then power on with that
  profile (`hmc_power_on_lpar` with `partition_profile_uuid`).
- Direct SR-IOV and vNIC assignments are unchanged.

## Live arm

The lpar-power arm no longer sends a slot to the real provision of partition P, and no
longer asserts `pcie-slot-owned`. Before that provision it sends one dry-run provision
with a dedicated slot and records `dedicated-slot-refused` when the call fails with the
refusal. The probe needs no dedicated-PCIe configuration: the refusal precedes every
HMC read, and a dry run writes nothing even if the refusal regresses.

## Out of scope

Profile-first provisioning (#1225); create-time `assignments.dedicated` and the
assign/unassign tools; any live HMC run.

## Failure model

A regression that drops the refusal would let a dedicated slot reach the profile with a
current-configuration PowerOn again. Unit tests pin the refusal at the operation, tool
and CLI entry points with no HMC request recorded, and the live arm's probe records a
FAIL row if a dry run is accepted.
