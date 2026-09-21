# LPAR ownership inventory

## Scope

Create an executable inventory for the LPAR-mutating operation surface governed
by ADR 0092. The user approved this authorization guardrail on 2026-09-05.

## Decision

The inventory is an AST-based test. It names the operation modules that expose
LPAR mutation, classifies every public operation as guarded, operational,
creation-only, or non-LPAR-mutating, and fails if a discovered public operation
has no classification. Guarded entries must contain or delegate to an existing
ownership helper; this adds no new runtime authorization path.

## Security model

The untrusted actor is an authenticated MCP, CLI, or Python caller. Existing
operations remain the authorization boundary. The test prevents a developer
from adding an LPAR mutator without recording its ownership classification;
it does not validate HMC authorization or replace runtime ownership checks.

## Acceptance criteria

- An unclassified public operation fails the inventory test.
- Every guarded operation has a statically reachable ownership helper.
- ADR 0092 identifies the inventory as the exhaustive enforcement mechanism.
- `just test` and `just verify` pass.
