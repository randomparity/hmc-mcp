# Verify lpar.modify delegated assignments

Issue #1386; scope token q1386-59127db3, WORK:SCOPE6064405578.
M250, full-spec, iterating; one PR selected by the operator on 2026-10-08.

## Problem

`lpar.modify` binds its resource write and assignment prevalidation reads, but
omits its delegates' chsyscfg/chhwres writes and supporting lssyscfg/lshmc reads.
ST39 exercises the resource path only. Existing dedicated-slot settings and
profile parsers can support the requested proof without another setting or arm.

## Scope and approach

Extend the coherent ST39 owner; no product ownership transition or API change.
Bind exactly chhwres, chsyscfg, lshmc, lshwres, lssyscfg and LogicalPartition,
verified from assignment prevalidation, its environment admission and the three
current delegate implementations.
Do not copy unrelated bindings from lpar.create or provision.lpar.

Run one dedicated assignment on the existing ST39 scratch partition, before
rename and before SMS activation. Reuse the dedicated configuration predicate,
exact runtime admission predicate, unowned predicate, and strict profile table/triple
parser. ST39 locally records returned read failures; it does not use the dedicated
arm admission helper that converts failed or malformed reads into SKIP.
The configured dedicated system must equal ST39's system. With an explicit DRC,
only that slot is eligible; without one select the first unowned slot listed by
no profile, following the existing dedicated configuration contract. The
profile name comes from that configuration (default default_profile).

Read the admitted all-profile table and dedicated inventory before scratch
creation. Missing configuration, a mismatched configured system, or no eligible
unowned slot listed by no profile causes the assignment case to SKIP with its
named prerequisite. An observed failed read or malformed response records FAIL
and retains the failed run exit, including before scratch creation. The existing
resource proof may continue; there is no assignment observation unless its call
was attempted. Continuing other cases never rewrites an observed failure. Release
and model responses must be text with three unique numeric Version/Release/Service
Pack fields and one type-model token in NNNN-XXX form. A well-formed pair outside
the existing exact admission envelope is SKIP; missing/repeated/non-numeric fields,
non-text responses or malformed model tokens are FAIL. Record original failed
call data through state.record (including CallFailure redaction); state.call does
not record. These checks stay local to ST39, without changing other live arms.

After scratch creation, re-read the profile table, require a unique configured
profile on the scratch name, and require its parsed io_slots baseline empty.
Re-read slot ownership and profile holders immediately before the assignment;
require the scratch ownership token and Not Activated state as well. Failure
at this point records FAIL and makes no assignment write.

Call hmc_modify_lpar with the scratch UUID, system and assignments.dedicated
containing only the configured profile name and selected DRC; omit resources.
Judge typed or mapping workflow_completed, dedicated[0] step status and independent
profile readback, not transport PASS. A warning that the final LPAR document read
failed stays in the recorded response; it does not negate the dedicated proof
when the delegated step and independent profile postconditions succeed. Read the profile back through the admitted table;
the exact parsed target triple must be DRC/none/0, no other triples added, and
other profiles' io_slots must be unchanged.

Restore before SMS activation. Even after a refused or ambiguous assignment,
read actual state once. Already-baseline requires no write; exactly the one
expected added triple permits one hmc_unassign_dedicated_pcie_slot using the
scratch UUID, only with matching token and Not Activated state. Unexpected or
unreadable state permits no cleanup mutation. A refused unassign is not retried.
After its one call, exact baseline readback and unowned inventory are required;
a failed call remains failure even if readback shows restoration.

If restoration is uncertain, do not run subsequent ST39 cases, power on, or
normal partition delete: record MANUAL RECOVERY REQUIRED and leave the owned
scratch partition for operator inspection. Existing recovery finds the reserved
ST39 prefix; its remedy discloses inspecting the scratch profile's dedicated
slot before any removal. Do not automatically repair from recovery.

When restored, continue existing ST39 cases and teardown. After deletion require
original all-profile io_slots map and selected unowned slot unchanged, in
addition to existing partition-name and processor/memory pool comparison.
Emit a distinct observation id st39-hmc-modify-lpar-dedicated for lpar.modify
under existing scenario st39-lpar-config, after final cleanup. Assertions are
assignment-workflow-completed, dedicated-profile-read-back,
other-profile-slots-unchanged, dedicated-profile-restored,
dedicated-slot-unowned and dedicated-baseline-restored. An attempted call with
failed workflow/readback/restore stays failed; no retry or observational rewrite.

SR-IOV and vNIC mutations remain SKIP rows and named durable gaps: they require
separate operator authorization and admitted writable SR-IOV backing hardware,
including an eligible logical port or healthy VIOS backing/RMC prerequisites.
They do not become missing implementation scope: the variants already exist.
Copy the emitted dedicated observation unchanged into maturity.json after its
live run. Existing resource evidence remains until separately re-recorded.
Regenerate capability metadata and tool documentation; update runbook, existing
ST39 specification's obsolete gap statement and changelog.

## Global Constraints

Python >=3.11; CI amd64/arm64 with Python3.11–3.14. No new dependencies.
Use sibling worktrees, just setup, uv run --no-sync, no force push or pushed rebase.
Mutate only run-owned scratch state inside the exact orchestrator live grant.
Redact PII from public writes; never publish test-results JSON.

## Success

- lpar.modify binds the six verified reference rows without changing sibling joins.
- A live exact-pushed-head ST39 run attempts the dedicated delegate once and
  records actual workflow/profile/baseline/cleanup outcomes, with failure retained.
- Eligible successful runs restore the scratch profile before activation, delete
  the scratch partition and recover clean with baseline names/pools/profiles/slot.
- Unauthorized SR-IOV/vNIC paths remain named prerequisite gaps without dispatch.
- Focused tests discriminate refusals, partial workflows, wrong readbacks,
  unsafe cleanup and unsafe activation; full local guardrails and CI pass.

## Failure model

1. Actors/deployments: trusted local operator and orchestrator on the authorized
   V10R3/POWER9 window; offline CI scripted HMC fixtures. No parallel live writers
   are authorized by this campaign. Tool responses can fail or be delayed.
2. Assets/invariants: other partition profiles and physical-slot ownership,
   original system resource pools, scratch-only UUID mutations, honest evidence.
3. Accepted classes: process interruption can leave one bounded scratch partition
   with the dedicated profile change; reserved-prefix recovery reports it for
   manual inspection, and no interrupted result establishes cleanup. Concurrent
   operator modification is outside the serialized window; observed differences
   fail proof/stop mutation. Arbitrarily late read propagation fails safely and
   receives manual diagnosis rather than repeated writes.
4. Covered elsewhere: ownership/auth semantics ADR0092; dedicated environment
   and profile grammar ADR0165/0166; existing ST39 and lpar-power lifecycle
   behavior remains unchanged, #1413 owns judged create/delete row documentation,
   and #1390 owns provision activation; SR-IOV/vNIC mutation authorization operator;
   stale catalogs refreshed by campaign consolidated round; unrelated device writes outside this PR.

## Threat model

- Boundaries added: none externally callable. Existing harness consumes configured
  selectors, HMC inventory/profile text and typed MCP workflow results.
- Actors: trusted local operator controls configuration; firmware supplies external
  response text; concurrent administrative mutation outside the granted window.
- Controls: existing dedicated config validation and admission; shlex command
  encoding through existing profile command builder; strict admitted parsers;
  exact UUID/token/state guards; exact before/expected/after comparisons; failed
  reads stop cleanup. Public filtered evidence generator plus complete PII scan.
- Out of scope: hostile operator with independent HMC authority (accepted serialized
  window assumption); product authorization redesign and new read grammar (existing
  controls unchanged); public raw capture ingestion (forbidden, not introduced).
