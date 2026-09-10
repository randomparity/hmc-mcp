# Operation maturity discovery implementation plan

Goal: expose one operation-keyed maturity projection through installed CLI and MCP
discovery and generated tool documentation without changing runtime admission.

Architecture: `docs/capabilities/maturity.json` remains canonical. The existing catalog
script generates a sparse package resource; a stdlib-only internal reader supplies every
presentation surface. ADR 0131 and the approved specification define the contract.

Tech stack: Python 3.11+, stdlib JSON/datetime/importlib resources, Typer/Rich, FastMCP
4.0.3, MCP 2.2.0, pytest 9.1.1, ruff 0.16.5, and ty 0.0.75.

Expected implementation size: 360–520 changed lines (M) — derived from the three task
file maps below, excluding this design set and generated tool pages.

## Global Constraints

- Supported Python versions are exactly 3.11, 3.12, 3.13 and 3.14.
- Declared CI targets are amd64 and arm64; the attuned x86_64 host is included.
- Add no dependency and no public export from `hmc_mcp.api`.
- Keep `docs/capabilities/maturity.json` canonical and sparse.
- Use `io.github.randomparity.hmc-mcp/operation-maturity` as the sole MCP metadata key.
- Keep `implementation`, `verification` and `runtime_eligibility` separate in every view.
- Runtime eligibility remains the literal `existing-runtime-guards` and grants nothing.
- Use operation IDs as identity; tool/CLI presentation names never own evidence.
- No HMC configuration, credential, network or hardware access occurs in discovery.
- Regenerate `docs/tools/`; never hand-edit its pages.
- Guardrails are `just verify` and `uv run --no-sync prek run --all-files`.
- BASE_BRANCH is `main`; branch is `feat/expose-operation-maturity-624`.
- Review depth is `iterating`; open findings and design deferrals are currently none.

## Task 1: Generate and read the packaged projection

Files: create `src/hmc_mcp/operation_maturity.py` and
`src/hmc_mcp/_operation_maturity.json`; modify `scripts/check_capability_inventory.py`,
`justfile`, `tests/scripts/test_check_capability_inventory.py`; create
`tests/unit/test_operation_maturity.py`.

Interfaces:

```python
@dataclass(frozen=True)
class OperationMaturity:
    implementation: str
    verification: str
    runtime_eligibility: str
    reason: str | None = None

def operation_maturity(operation: str, *, now: datetime | None = None) -> OperationMaturity: ...
def operation_maturity_catalog(
    *, now: datetime | None = None
) -> Mapping[str, OperationMaturity]: ...
def operation_maturity_meta(operation: str, *, now: datetime | None = None) -> dict[str, str]: ...
```

Later tasks rely on these exact names. The script adds `--write-runtime-projection PATH`
and pure `render_runtime_projection(...) -> str`; `just capability-metadata` writes the
fixed package path. Default validation compares the canonical rendering byte-for-byte.

Verification:

- Mode: focused-test. Contract: closed projection schema, sparse default, immutability,
  invalid-operation grammar and age transition. Tests:
  `tests/unit/test_operation_maturity.py`. Expected red: import or assertion failure before
  the module exists. Green: `uv run --no-sync pytest tests/unit/test_operation_maturity.py
  --no-cov -q` exits 0.
- Mode: focused-test. Contract: generation is atomic and the catalog gate rejects missing,
  malformed and drifted output. Tests added to
  `tests/scripts/test_check_capability_inventory.py`. Expected red: parser rejects the new
  option or stale projection is accepted. Green: `uv run --no-sync pytest
  tests/scripts/test_check_capability_inventory.py --no-cov -q` exits 0.

Steps:

1. Add both focused test groups and run their commands to observe red.
2. Introduce the frozen dataclass and strict package-resource loader. Return the explicit
   all-`unrecorded` value for a grammar-valid operation absent from the sparse projection;
   raise `OperationMaturityError` for an invalid operation ID or projection.
3. Extend the existing derivation code to serialize only recorded operations, preserve the
   latest observation time needed for age checks, and atomically write the requested path.
4. Add `capability-metadata` to `justfile`, regenerate the resource, and make default
   validation compare exact bytes with an actionable regeneration message.
5. Run both focused commands green. Remove one schema check and one age check in controlled
   faults, observe the named tests red, restore them, and rerun green.
6. Run `just capability-inventory`, `just lint`, and `just typecheck`; commit as
   `feat: package operation maturity projection`.

Rollback: revert the task commit; the canonical catalogs are never rewritten.

## Task 2: Expose the shared projection through CLI and MCP discovery

Files: create `src/hmc_mcp/cli_commands/capabilities.py` and
`tests/app/test_capability_discovery.py`; modify `src/hmc_mcp/cli.py`,
`src/hmc_mcp/cli_commands/app.py`, `src/hmc_mcp/tool_registry.py`,
`src/hmc_mcp/server_tools/command.py`, `src/hmc_mcp/server_tools/permissions.py`, and
`tests/unit/test_tool_registry.py`.

Interfaces:

```python
MATURITY_META_KEY = "io.github.randomparity.hmc-mcp/operation-maturity"
def capabilities(as_json: bool = typer.Option(False, "--json", help="Output JSON")) -> None: ...
def capability_rows(
    tool_security: Mapping[str, ToolSecurity], *, now: datetime | None = None
) -> list[dict[str, object]]: ...
```

`capability_rows` groups sorted tool names by operation and calls Task 1 once per operation.
All three MCP registration sites pass
`meta={MATURITY_META_KEY: operation_maturity_meta(security.operation)}` while preserving
their existing annotation and authorization arguments.

Verification:

- Mode: focused-test. Contract: CLI table/JSON share sorted operation rows and run with HMC
  settings absent. Tests: `tests/app/test_capability_discovery.py`. Expected red: command is
  absent. Green: `uv run --no-sync pytest tests/app/test_capability_discovery.py --no-cov
  -q` exits 0.
- Mode: focused-test. Contract: every registration path adds identical namespaced metadata
  without changing MCP annotations or ceiling filtering. Tests in the same file and
  `tests/unit/test_tool_registry.py`. Expected red: `_meta` is absent. Green: the command
  above plus `uv run --no-sync pytest tests/unit/test_tool_registry.py --no-cov -q` exit 0.

Steps:

1. Add CLI and MCP contract tests and run both focused commands to observe red.
2. Build sorted operation rows from `TOOL_SECURITY`; table columns are Operation, Tools,
   Implementation, Verification and Runtime eligibility, while JSON uses those snake-case
   fields plus `reason` only when present.
3. Register the root `capabilities` command without importing credentials or creating an
   HMC client.
4. Add the same namespaced dictionary at the module, arbitrary-command and effective-
   permissions registration sites. Do not alter `ToolAnnotations`.
5. Run focused tests green. Temporarily substitute one tool's operation during the test,
   observe the shared-evidence assertion red, restore it, and rerun green.
6. Run `just smoke`, `just lint`, and `just typecheck`; commit as
   `feat: expose operation maturity in discovery`.

Rollback: revert the task commit; Task 1 remains an unused internal projection.

## Task 3: Generate evidence-backed docs and compatibility guidance

Files: modify `scripts/gen_tool_reference.py`,
`tests/scripts/test_gen_tool_reference.py`, `docs/compatibility.md`,
`tests/test_project_metadata.py`, `tests/test_release_artifacts.py`, and `CHANGELOG.md`;
regenerate `docs/tools/*.md` with `just tool-docs`.

Interfaces: extend `ToolRecord` with `implementation`, `verification`,
`verification_reason` and `runtime_eligibility`. `build_records(...)` resolves these only
through Task 1's reader. Group tables render separate Implementation, Verification and
Runtime eligibility columns; the index defines the vocabularies and links the canonical
capability guide.

Verification:

- Mode: focused-test. Contract: generated pages join the shared projection and reject an
  unresolved operation. Tests: `tests/scripts/test_gen_tool_reference.py`. Expected red:
  maturity columns are absent. Green: `uv run --no-sync pytest
  tests/scripts/test_gen_tool_reference.py --no-cov -q` exits 0.
- Mode: focused-test. Contract: compatibility prose removes the blanket V8–V11/all-POWER
  promise while retaining sourced specific limits. Test:
  `tests/test_project_metadata.py::test_hmc_compatibility_claims_are_evidence_scoped`.
  Expected red: the old universal text remains. Green: run that node with `--no-cov -q`.
- Mode: focused-test. Contract: wheel and sdist contain the projection resource. Test added
  to `tests/test_release_artifacts.py`. Expected red: the member is not asserted or absent.
  Green: `uv run --no-sync pytest tests/test_release_artifacts.py --no-cov -q` exits 0.

Steps:

1. Add all focused assertions and run them to observe red.
2. Extend `ToolRecord`, its join validation, group rows and index legend using Task 1's
   reader; keep registered/exposed counts and enablement notes unchanged.
3. Run `just tool-docs`; inspect generated diffs rather than editing pages.
4. Rewrite only unsupported compatibility claims, preserve specific sourced limitations,
   add the release note, and assert the projection archive member.
5. Run focused tests green. Remove one generated maturity value, observe the generator test
   red, restore it, and rerun green.
6. Run `just capability-inventory`, `just tool-docs-check`, `just doc-freshness`, and
   `just verify`; then run `uv run --no-sync prek run --all-files`.
7. Review the merge-base diff for naming and complexity; commit as
   `docs: publish evidence-backed operation maturity`.

Rollback: revert the task commit and regenerate `docs/tools/` from the reverted generator.

## Resume facts

- Current phase after approval: design review, then scope audit and forge.
- Scope token: `q624-dc2d8c45`; scope annotation is the latest complete `WORK:SCOPE` on #624.
- BASE_BRANCH: `main`; branch: `feat/expose-operation-maturity-624`.
- Guardrails: `just verify`; `uv run --no-sync prek run --all-files`.
- Host architecture: x86_64; targets: amd64 and arm64; relationship: included.
- Artifact lane: full-spec; fixed denominator: 250 changed lines (M).
- Review depth: iterating; open findings, deferrals and follow-up candidates: none.
