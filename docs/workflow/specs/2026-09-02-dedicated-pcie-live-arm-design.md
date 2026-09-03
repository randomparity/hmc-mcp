# Dedicated PCIe live-assignment arm — design

Issue: [#217](https://github.com/randomparity/hmc-mcp/issues/217) ·
Decision record: [ADR 0115](../../adr/0115-dedicated-pcie-live-evidence-arm.md)

## Goal

Add the dedicated PCIe slot arm of the live-HMC reversible-assignment scenario to
`scripts/live_test/pcie.py`, register it with the live runner, and cover its orchestration
and cleanup guards with unit tests.

The SR-IOV arm (`exercise_sriov_assignment`, ST23–ST28) is already implemented and already
live-verified — 24/24 PASS on 2026-09-01. It is out of scope and is not modified.

Nothing in this change runs against a live HMC. The arm's PASS/SKIP/FAIL matrix is produced
by the operator after merge.

## Background the design turns on

`hmc_assign_dedicated_pcie_slot`, `hmc_unassign_dedicated_pcie_slot`, and a create-time
`LparPcieAssignments.dedicated` request all fail closed under ADR 0055. ADR 0053 states the
condition that lifts the gate: exact `io_slots` readback must be admitted. ADR 0115 records
the resulting decision — the arm records the refusal, then gathers the readback evidence
through the documented profile grammar over the runner's `hmc_run_command` escape hatch.

## Architecture

One new orchestrator, `exercise_dedicated_pcie_assignment`, registered as live-runner
subtask 24 and as the `dedicated` subtask group. It follows the SR-IOV arm's shape: a frozen
config dataclass, a mutable fixture record, a point-in-time state snapshot with a summary
formatter, one function per step, and a top-level orchestrator that sequences them and
always reaches cleanup.

Step labels are **ST29–ST34**. The SR-IOV arm is registered as subtask 23 but records rows
under subtask numbers 23–28, so 24–28 are already taken in the results document; starting at
29 keeps every row label unique across a full run.

### Data structures

```python
@dataclass(frozen=True)
class _DedicatedConfig:
    system_name: str
    lpar_prefix: str
    profile_name: str
    drc_index: str | None      # explicit override; None selects from inventory

@dataclass
class _DedicatedFixture:
    config: _DedicatedConfig
    run_marker: str            # ADR 0064 caller token, unique per run
    lpar_name: str             # f"{lpar_prefix}{run_marker}"
    lpar_uuid: str | None = None
    drc_index: str | None = None
    baseline_io_slots: str | None = None   # exact profile value at creation
    applied_io_slots: str | None = None    # exact profile value this run wrote
    slot_assigned: bool = False
    created: bool = False      # a partition exists and is this run's to clean up

@dataclass(frozen=True)
class _DedicatedState:
    slot_owner: str | None      # owner_lpar from the dedicated-slot inventory
    profile_io_slots: str | None  # exact `lssyscfg -F io_slots` value
    lpar_uuid: str | None
    caller_token: str | None    # ADR 0064 token parsed from the description
```

### Configuration

Read once, from the environment, with no fallback to `LiveTestContext.system_name` — issue
#217 requires explicit managed-system configuration and forbids falling back to an arbitrary
system.

| Variable | Required | Meaning |
|---|---|---|
| `HMC_LIVE_PCIE_SYSTEM` | yes | Managed-system name the arm may mutate |
| `HMC_LIVE_PCIE_LPAR_PREFIX` | yes | Name prefix for the run-unique fixture LPAR |
| `HMC_LIVE_PCIE_PROFILE` | no | Profile name; defaults to `default_profile` |
| `HMC_LIVE_PCIE_DRC_INDEX` | no | Exact slot to exercise; otherwise auto-selected |

A missing required variable is a SKIP for the whole arm, recorded before any tool call.
These names are not `HMCConfig` fields, so `just env-vars` — which checks `HMCConfig` fields
against `docs/environment-variables.md` — is unaffected. They are documented in the module
docstring, where the operator running the arm will look.

## Steps

**ST29 — configure and baseline.** Resolve configuration; SKIP the arm when it is absent.
Call `hmc_list_dedicated_pcie_slots` for the configured system. Select the configured
`drc_index`, or the first slot whose `owner_lpar` is empty when none is configured. SKIP the
arm — never PASS — when the inventory is empty, when no slot is unassigned, or when a
configured `drc_index` is absent from the inventory or already owned.

**ST30 — create the run-unique owner-stamped fixture.** First call `hmc_create_lpar` with a
non-empty `assignments.dedicated`, to record the create-time capability boundary; ADR 0055
refuses this before creating anything, so it is recorded as SKIP via
`RunState.record_expected_or_real`. Then call `hmc_create_lpar` with no assignments and
`caller_token=<run_marker>`. Capture the UUID — from `result["lpar"]["UUID"]`, falling back
to `hmc_get_lpar` because some firmware answers HTTP 201 with an empty body — and read the
exact baseline `io_slots` value from the new profile. A failed create ends the arm before
any mutation.

**No identity, no mutation.** If the UUID cannot be resolved after both attempts, the arm
records that and proceeds directly to cleanup without assigning anything. The fixture is
then removed on the run-unique caller token and the fixture name alone, which is the only
identity that exists — and it is never the identity under which hardware was mutated,
because no hardware was. Keeping the two apart is why this is a separate rule and not a
weakened Guard A.

**ST31 — assign on the existing LPAR.** Call `hmc_assign_dedicated_pcie_slot` and record its
capability refusal as SKIP. Then issue the documented profile grammar,
`chsyscfg -r prof -m <system> -i "name=<profile>,io_slots+=<drc>//0,lpar_name=<lpar>"`, and
record the exact `io_slots` value the profile then holds as `applied_io_slots`. Setting
`slot_assigned` happens only after that readback observes the DRC index, so cleanup never
believes it wrote something it did not.

**ST32 — verify readback.** The profile's `io_slots` must contain the DRC index. The
dedicated-slot inventory owner is recorded as an informational row, mirroring the SR-IOV
arm's treatment of the effective layer: a profile-only assignment is not expected to change
effective inventory for a partition that has never activated.

**ST33 — unassign, verify restoration, reassign.** Issue `io_slots-`, require the profile's
`io_slots` to equal the exact `baseline_io_slots` captured at ST30 — exact restoration, not
merely the absence of the DRC index — then reassign and verify again. Each phase runs only
if the previous one passed; a skipped phase is recorded as SKIP with the reason.

**ST34 — cleanup.** Always runs. It is the safety boundary and is specified in full below.

## Cleanup contract

Cleanup performs at most two mutations, in this order: remove the slot from the profile,
then delete the LPAR. Before **each** of them it re-reads live state and compares it exactly
against what this run recorded. Every refusal records a FAIL row whose note begins
`MANUAL RECOVERY REQUIRED:` and names the exact command an operator must run, and then
returns without attempting any further mutation.

**Guard A — fixture identity, checked before either mutation.** Re-read the LPAR UUID via
`hmc_get_lpar` and the ADR 0064 caller token from `hmc_get_lpar_description`, parsed with
`parse_lpar_ownership_caller_token`.

- Caller token differs from `fixture.run_marker` → refuse both mutations.
- A UUID was captured at ST30 and the re-read UUID differs from it → refuse both mutations.
- Either value unreadable → refuse both mutations. An unreadable identity is a mismatch, not
  a pass; this is the direction the guard has to fail in.

The one asymmetry is deliberate: when ST30 never resolved a UUID, ST31–ST33 did not run, so
there is no assignment to remove and the token comparison alone governs the delete.

**Guard B — slot removal.** Attempted only when `fixture.slot_assigned` is true.

- The profile's current `io_slots` must equal `fixture.applied_io_slots` exactly. Anything
  else means something outside this run changed the profile → refuse, and do not delete the
  LPAR either.
- On an exact match, issue `io_slots-`, then re-read. The value must equal
  `fixture.baseline_io_slots`. A removal that reports success but does not restore the
  baseline → refuse to delete the LPAR, and record recovery evidence.

**Guard C — LPAR deletion.** Reached only after Guard A passed and either the slot was never
assigned or Guard B completed and observed the restored baseline. Re-read the UUID and
caller token once more, immediately before the delete, and require both to still match. The
delete is `hmc_delete_lpar(system_name_or_uuid=<configured system>,
lpar_name_or_uuid=<fixture name>)` — the tool requires the system selector, and the arm has
a configured one, so it never searches the fleet for the name it is about to destroy.

Hardware before partition is the ordering the issue requires: a delete that ran first would
leave a slot recorded against a partition that no longer exists, which is exactly the
un-reusable pool state the issue's downstream consumer cannot tolerate.

## Testing

`tests/scripts/test_pcie.py`, new, following the AGENTS.md convention of one test module per
`scripts/` file and the `ScenarioState` seam already used by `tests/scripts/test_inventory.py`.

The seam is extended in two ways the inventory one does not need: a per-tool status map, so
a test can make one tool fail while others pass, and retention of the ordered call list, so
ordering and non-occurrence can be asserted.

The guards are what the tests must bite on. Each of these asserts both the recorded outcome
**and** that the forbidden mutation does not appear in the call list:

1. Missing required configuration → the arm SKIPs and issues no tool call at all.
2. Inventory with no unassigned slot → SKIP for the arm, and no LPAR is created.
3. A configured `drc_index` that inventory reports as owned → SKIP, no LPAR created.
4. The create-time dedicated request's capability refusal is recorded SKIP, not FAIL or
   PASS, and the arm still proceeds to create the unassigned fixture.
5. The existing-LPAR `hmc_assign_dedicated_pcie_slot` refusal is recorded SKIP, and the arm
   still proceeds to the profile grammar.
6. Happy path: `io_slots+` then `io_slots-` restores the exact baseline, reassign succeeds,
   cleanup removes the slot and deletes the LPAR, and the removal command precedes the
   delete call in the recorded order.
7. **Guard A, UUID drift** — the description read at cleanup reports a different UUID: no
   `chsyscfg` and no `hmc_delete_lpar` is issued, and a `MANUAL RECOVERY REQUIRED:` row is
   recorded.
8. **Guard A, foreign caller token** — the token read back is not this run's marker: same
   assertions.
9. **Guard A, unreadable identity** — the description read fails: same assertions.
10. **Guard B, profile drift** — `io_slots` at cleanup is neither the applied value nor the
    baseline: no `io_slots-` is issued, no delete is issued, recovery evidence is recorded.
11. **Guard B, removal that does not restore** — `io_slots-` reports success but the
    re-read does not equal the baseline: no `hmc_delete_lpar` is issued.
12. **Guard C, identity drift between removal and delete** — identity matched at Guard A and
    the removal succeeded, but the re-read before the delete reports a foreign token: no
    `hmc_delete_lpar` is issued.
13. Cleanup when the slot was never assigned: no `chsyscfg` is issued, and the LPAR is still
    deleted (the fixture is this run's to remove).
14. A run marker is unique per invocation, and the fixture LPAR name carries the configured
    prefix.
15. **No identity, no mutation** — create returns no LPAR body and `hmc_get_lpar` resolves
    nothing: no `chsyscfg` is issued at all, and the fixture is still deleted on the token.

Tests 7–12 are the ones that would pass against a guard that never refuses only if the
assertion on the absent call is missing, so each states it explicitly rather than asserting
the recorded row alone.

## Trust boundaries

The arm is an operator-run script, not a served surface, and it adds no entry point an
untrusted actor can reach. Two boundaries are worth stating:

- **Command construction from non-literal values.** `system_name`, `lpar_name`,
  `profile_name`, and `drc_index` reach `chsyscfg` and `lssyscfg` strings. `system_name` is
  passed through `shlex.quote`; the profile record is built with the existing
  `hmc_mcp.ssh.commands.build_attribute_record`, which is the same helper
  `ssh/profiles.py:_change_profile_io_slot` uses and which rejects characters the `-i`
  record parser treats as structure. The arm does not hand-format that record.
- **Operator-supplied configuration.** The four environment variables are supplied by the
  operator running the script, who already holds HMC credentials; they are a configuration
  boundary, not a trust boundary. `drc_index` is nonetheless compared against the inventory
  before use, so a typo SKIPs rather than reaching a command.

Out of scope: the escape hatch's own authorization (unchanged, and already opted into by the
runner), and SSH transport host-key handling (issue #605).

## Out of scope

The SR-IOV arm; any change to `src/`; lifting ADR 0055's gate; running against hardware;
`src/hmc_mcp/ssh/transport.py`.
