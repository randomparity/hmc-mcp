# Platform update implementation plan

**Goal:** Replace the nonexistent firmware operation with the documented, version-gated JSON
PlatformUpdate job.

**Architecture:** `jobs.py` owns explicit payload types and the pure JSON envelope builder.
`client.py` owns the JSON PUT transport. `server_updates.py` gates HMC compatibility, resolves the
target, submits, and optionally waits through existing lifecycle helpers.

**Tech stack:** Python 3.11+, strict Pydantic models, HTTPX, pytest/respx, FastMCP.

## Global constraints

- Require HMC 11.1.1111 or later and fail closed before system resolution or submission.
- Submit `PlatformUpdate` to `ManagedSystem` with the documented nested JSON shape.
- Use `application/vnd.ibm.powervm.web+json; type=JobRequest` and `Accept: application/json`.
- Remove the old UpdateFirmware and RepositorySource firmware path; add no shim.
- Preserve all existing XML job behavior and update-family contracts merged for #397 and #398.
- Run `just test`, `just smoke`, and `just verify`; CI hard-gates `just verify`.
- No ADR index update is required because ADR-index coupling is `no index`.

## Task 1: Define and prove the payload contract

**Files:** modify `src/hmc_mcp/jobs.py`; modify `tests/system/test_update_upgrade.py`.

**Interfaces:** define `PlatformUpdateParameter` and its nested strict Pydantic model types; define
`platform_update_job(parameters: PlatformUpdateParameter) -> dict[str, Any]`. Task 3 imports both.

1. Replace the old builder test with a full expected-dictionary test containing system firmware,
   SR-IOV, VIOS, and IO-adapter sections. Add parameterized model tests for every admitted literal
   and near-miss casing, conditional ResourceType, empty adapter lists, unknown keys at each nested
   level, and semantic no-op requests. Run
   `uv run pytest -q --no-cov tests/system/test_update_upgrade.py -k 'platform_update'`; expect
   collection failures because the new model and builder symbols do not exist.
2. Add frozen `BaseModel` classes with `extra="forbid"`, the table-and-sample literal unions,
   conditional VIOS resource validation, semantic no-op validation, nested SR-IOV under system
   firmware and IO adapters under VIOS, plus the pure builder. Keep `update_firmware_job` only until
   Task 3 replaces its live consumer so every checkpoint remains importable. Run the same
   `-k 'platform_update'` selection; expect every builder and validation test to pass.
3. Run `uv run ruff check src/hmc_mcp/jobs.py tests/system/test_update_upgrade.py` and
   `uv run ty check`; expect zero diagnostics.
4. Commit with `feat: model platform update payload`.

Acceptance: native mapping equality proves the exact envelope and no stringified parameter value;
IBMWebsite and NoUpdate/IO-only sample shapes validate; every admitted casing and near miss is
tested; empty adapter lists and all-NoUpdate/no-adapter requests fail.

## Task 2: Add narrow JSON job submission

**Files:** modify `src/hmc_mcp/client.py`; modify `tests/system/test_update_upgrade.py`.

**Interfaces:** define
`HMCClient.submit_json_job(job_path: str, job_request: Mapping[str, Any]) -> dict[str, Any] | None`.
Task 3 calls it. Existing `submit_job(str, str)` remains unchanged.

1. Add async tests for exact PUT headers/body, normalization of IBM's lowercase `id`, nested
   `content.JobResponse`, `Result`, and `selfLink`, plus empty success, non-success, malformed
   successful JSON, and valid JSON with wrong root, id, content, JobResponse, Status, selfLink,
   Result, and result-entry field types. Add a non-2xx response that echoes a sentinel submitted
   value and assert the sanitized `HMCError` omits it. Pass a documented failure result through
   `job_outcome`. Run
   `uv run pytest -q --no-cov tests/system/test_update_upgrade.py -k submit_json_job`; expect attribute-error
   failures.
2. Implement the method with HTTPX `json=`, exact headers, existing 200/201/202 acceptance,
   complete response normalization, and actionable `HMCError` failures. Run the focused tests;
   expect pass.
3. Run the existing XML submission tests beside the new tests; expect pass and unchanged headers.
4. Commit with `feat: submit JSON job requests`.

Acceptance: the JSON boundary is exact and XML callers do not change.

## Task 3: Replace the firmware tool behavior

**Files:** modify `src/hmc_mcp/server_updates.py`; modify `tests/app/test_server_tools.py`; modify
`tests/system/test_update_upgrade.py`; modify `tests/app/test_tool_security.py` where schema assertions
name the old argument; modify `tests/unit/test_job_lifecycle.py` where the obsolete repository schema
is imported and asserted.

**Interfaces:** replace the public signature with
`hmc_update_firmware(system_name_or_uuid: str, platform_update: PlatformUpdateParameter,
wait: bool = False, timeout_seconds: int = 300, poll_interval: int = 5,
profile: str | None = None) -> dict[str, Any] | None`. Add private
`_require_platform_update_version(console)` and `_platform_update_op(hmc, submit_fn, wait,
timeout_seconds, poll_interval)`. The latter returns a normalized terminal response directly,
passes only link-bearing nonterminal responses to `wait_for_submitted_job`, and raises the
accepted-but-unpollable error for a nonterminal response without a link. Use
`platform_update_job(PlatformUpdateParameter)` plus `HMCClient.submit_json_job`.

1. Replace the app test with supported-version exact-path/body tests and add older, missing,
   malformed, empty-input, `wait=True` terminal JSON-response, and nonterminal-without-selfLink
   cases that assert no guessed polling; add a link-bearing nonterminal case that proves the
   existing poller receives the link. Replace the obsolete RepositorySource lifecycle-schema test
   with PlatformUpdate model/schema coverage. Add a quoted-UUID case and regression tests proving
   the XML update helpers retain their existing wait behavior. Run
   `uv run pytest -q --no-cov tests/app/test_server_tools.py -k firmware`; expect failures against the old
   contract.
2. Implement strict version parsing, model validation, version lookup before system resolution,
   quoted system UUID, PlatformUpdate-local terminal/link/no-link wait branching, and submission.
   Remove `update_firmware_job`, firmware-only `RepositorySource`, `RepositoryType`, validation
   maps, `_repository_params`, and their obsolete imports now that their consumers are replaced.
   Run the focused app tests plus
   `uv run pytest -q --no-cov tests/unit/test_job_lifecycle.py -k platform_update`; expect pass.
3. Update system/client and tool-security schema expectations to the new argument and nested types.
   Exercise unknown keys at the top level and every nested level through the actual MCP boundary.
   Run `uv run pytest -q --no-cov tests/system/test_update_upgrade.py tests/app/test_tool_security.py`; expect
   pass.
4. Commit with `fix: use documented platform update job`.

Acceptance: unsupported/unknown HMC versions cannot submit or resolve a system; supported versions
send the exact request; terminal responses require no poll, link-bearing nonterminal responses use
the existing poller, and id-only nonterminal responses fail actionably after acceptance without
guessing an endpoint.

## Task 4: Document and verify the public replacement

**Files:** modify `README.md`; add `docs/adr/0075-use-json-platform-update-job.md`; add
`docs/workflow/specs/2026-08-22-platform-update-design.md`; add this plan; modify only generated
artifacts named by guardrail failures.

**Interfaces:** README names `platform_update`, its version floor, and a complete representative
object using the types from Task 1.

1. Replace the generic firmware row/description with PlatformUpdate version and input guidance.
2. Search with `rg -n 'UpdateFirmware|update_firmware_job|repository.*firmware' src tests README.md`;
   expect no obsolete executable or user-facing contract references.
3. Run `just test`, `just smoke`, and `just verify`; expect all commands to exit 0 with no warnings.
4. Review `git diff --check` and `git status --short --untracked-files=all`; expect no whitespace
   errors and only intended tracked changes. Commit with `docs: describe platform update contract`.

Acceptance: documented instructions match the installable schema; all repository gates pass.

## Rollback and live proof

No external state is created by tests. Do not deploy code reverted to the known-invalid
UpdateFirmware operation: the repository has no supported per-tool disable control. Correct a bad
change forward, or take the whole MCP service out of service until a corrected build is deployed;
verify the deployed revision against the intended commit before restoring service. A live P11
validation, when credentials and a safe target are explicitly available, must likewise confirm the
deployed checkout equals branch HEAD before submission; otherwise record that the documentation and
mocked HTTP path ran but live HMC validation did not.
