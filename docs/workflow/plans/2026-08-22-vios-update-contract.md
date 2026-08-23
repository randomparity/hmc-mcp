# VIOS update and upgrade implementation plan

## Goal and architecture

Correct `hmc_vios_update` to build and submit IBM's documented VIOS operations,
validate the selected request contract before I/O, and expose waited `stdOut`
without discarding the raw HMC job. `jobs.py` owns typed input contracts, XML
builders, and result parsing; `server_updates.py` owns operation selection,
path construction, submission, and the public tool result.

Tech stack: Python 3.11–3.14, `TypedDict`, Pydantic/FastMCP schema generation,
pytest/respx, ruff, ty, and uv.

## Global constraints

- Preserve the consolidated `kind="update" | "upgrade"` entry point.
- Update uses `UpdateVIOS`, `/do/UpdateVIOS`, and ResourceType values `HMC`,
  `NFS`, `SFTP`, `USB`, `IBMWebsite`.
- Upgrade uses `UpgradeVIOS`, `/do/UpgradeVIOS`, and ResourceType values `HMC`,
  `NFS`, `SFTP`, `USB`.
- Common optional parameters are `Name`, `ServerHostOrIP`, `UserName`,
  `Password`, `SSHKey`, `PassPhrase`, `RemoteDirectory`, `FileNames`,
  `MountLocation`, `MountOptions`, `USBDevice`, and `SaveFile`.
- Only update accepts `RestartVIOS`; only upgrade accepts `Disks`.
- `ResourceType`, unknown keys, cross-operation keys, and upgrade
  `IBMWebsite` fail before client creation. Do not invent media-specific
  required fields absent from IBM's references.
- Encode the resolved VIOS UUID as one URL path segment.
- Only a waited terminal result projects the first non-empty string `stdOut`,
  trimmed, at the top level; preserve the raw job and submission-only results.
- Keep `RepositorySource` and firmware behavior unchanged. Do not restore the
  removed generic console update behavior or add compatibility shims.
- Use no new dependency. Keep Python functions within repository complexity,
  line-length, positional-argument, and warning limits.
- Guardrails are `just test`, `just smoke`, and `just verify`; CI hard-gates
  `just verify`. ADR-index coupling is `no index`.

## File map

- `src/hmc_mcp/jobs.py`: VIOS request types, validation/builders, `stdOut`
  extraction.
- `src/hmc_mcp/server_updates.py`: public annotation/docs, fail-fast selection,
  encoded path, wait-only result projection.
- `tests/system/test_update_upgrade.py`: XML builders and HTTP paths.
- `tests/app/test_server_tools.py`: tool validation, path encoding, async/waited
  result behavior.
- `tests/app/test_capabilities.py`: rendered VIOS schema.
- `tests/unit/test_job_lifecycle.py`: typed-source schema and result parser edge
  cases.
- `README.md`: corrected operator-facing request and result contract.

## Task 1: Pin the documented builder and schema contract

**Interfaces**

- Produces `VIOSUpdateSource`, `VIOSUpgradeSource`, `VIOSSource`,
  `update_vios_job(VIOSUpdateSource) -> str`, and
  `upgrade_vios_job(VIOSUpgradeSource) -> str` in `hmc_mcp.jobs`.
- Later tasks consume `VIOSSource` in `hmc_vios_update` and the two builders.
- Existing `RepositorySource` remains the input to `update_firmware_job`.

1. Replace old VIOS fixtures in `tests/system/test_update_upgrade.py` with
   documented dictionaries and assertions for exact operation/group and
   parameter names. Add failing tests for missing `ResourceType`, unknown keys,
   `Disks` on update, `RestartVIOS` on upgrade, and `IBMWebsite` on upgrade.
2. Update `tests/unit/test_job_lifecycle.py` to construct Pydantic adapters for
   both VIOS source types and assert their exact property sets and required
   discriminator.
3. Update `tests/app/test_capabilities.py` to assert the public tool schema is
   the union of the update and upgrade source contracts and that each branch
   carries its exact `ResourceType` enum.
4. Run the focused tests and confirm they fail against the old generic
   `RepositorySource` and bare operation names:

   ```sh
   uv run --no-sync pytest -q tests/system/test_update_upgrade.py \
     tests/unit/test_job_lifecycle.py tests/app/test_capabilities.py
   ```

5. In `src/hmc_mcp/jobs.py`, define the exact interfaces:

   ```python
   VIOSUpdateResourceType = Literal["HMC", "NFS", "SFTP", "USB", "IBMWebsite"]
   VIOSUpgradeResourceType = Literal["HMC", "NFS", "SFTP", "USB"]

   class _VIOSSourceBase(TypedDict, total=False):
       Name: Annotated[str, Field(description="Name of the VIOS image.")]
       ServerHostOrIP: Annotated[str, Field(description="Remote server host or IP.")]
       UserName: Annotated[str, Field(description="Remote SFTP user name.")]
       Password: Annotated[str, Field(description="Remote SFTP password.")]
       SSHKey: Annotated[str, Field(description="SSH private key for SFTP.")]
       PassPhrase: Annotated[str, Field(description="SSH-key passphrase.")]
       RemoteDirectory: Annotated[str, Field(description="Remote image directory.")]
       FileNames: Annotated[str, Field(description="Comma-separated image files.")]
       MountLocation: Annotated[str, Field(description="NFS mount location.")]
       MountOptions: Annotated[str, Field(description="Additional NFS mount options.")]
       USBDevice: Annotated[str, Field(description="USB device name.")]
       SaveFile: Annotated[str, Field(description="Save the remote image on the HMC.")]

   class VIOSUpdateSource(_VIOSSourceBase):
       ResourceType: Required[VIOSUpdateResourceType]
       RestartVIOS: Annotated[str, Field(description="Restart VIOS after update.")]

   class VIOSUpgradeSource(_VIOSSourceBase):
       ResourceType: Required[VIOSUpgradeResourceType]
       Disks: Annotated[str, Field(description="Comma-separated free physical volumes.")]

   VIOSSource = VIOSUpdateSource | VIOSUpgradeSource
   ```

   Add operation-specific key/type constants and a validator that rejects
   unknown or missing parameters with operation-named actionable messages,
   stringifies non-`None` values, and is called by the two builders. Render
   `UpdateVIOS` and `UpgradeVIOS` respectively.
6. Run the focused tests and expect all selected tests to pass. Run
   `uv run --no-sync ruff check src/hmc_mcp/jobs.py` and
   `uv run --no-sync ty check src/hmc_mcp/jobs.py`; expect exit 0.
7. Commit the explicit source and test paths with subject
   `fix: use documented VIOS update job requests`.

Acceptance: generated XML contains only the selected documented operation and
keys; every invalid selected-operation contract fails; rendered schema exposes
both precise request shapes; firmware tests remain unchanged and passing.

## Task 2: Submit exact paths and project waited stdOut

**Interfaces**

- Consumes Task 1's `VIOSSource`, builders, and operation validation.
- Produces `vios_stdout(job: dict[str, Any] | None) -> str | None` in
  `hmc_mcp.jobs` and corrected `hmc_vios_update` behavior.
- Preserves `_update_op` and shared wait lifecycle semantics.

1. Add failing unit fixtures in `tests/unit/test_job_lifecycle.py` for
   `Resource.Results.JobParameter` as one mapping and a list. Cover a malformed
   entry before a valid value; exact case-sensitive name; whitespace-only and
   non-string values; and a first valid value followed by empty, malformed, and
   valid duplicates. Assert the first non-empty string is trimmed and wins.
2. Change VIOS application tests to expect exact `/do/UpdateVIOS` and
   `/do/UpgradeVIOS` paths and bodies. Add a hostile selector test matching the
   console path-segment test. Add wait tests proving the raw terminal mapping is
   retained with top-level `stdOut`, and non-wait submission metadata is
   unchanged without a projection.
3. Run:

   ```sh
   uv run --no-sync pytest -q tests/unit/test_job_lifecycle.py \
     tests/app/test_server_tools.py -k 'vios or stdout'
   ```

   Expect failures on the old paths, absent parser, and unencoded selector.
4. Add this structural parser in `src/hmc_mcp/jobs.py`:

   ```python
   def vios_stdout(job: dict[str, Any] | None) -> str | None:
       resource = (job or {}).get("Resource")
       if not isinstance(resource, dict):
           return None
       results = resource.get("Results")
       if not isinstance(results, dict):
           return None
       parameters = results.get("JobParameter", [])
       if isinstance(parameters, dict):
           parameters = [parameters]
       if not isinstance(parameters, list):
           return None
       for parameter in parameters:
           if not isinstance(parameter, dict):
               continue
           value = parameter.get("ParameterValue")
           if parameter.get("ParameterName") == "stdOut" and isinstance(value, str):
               value = value.strip()
               if value:
                   return value
       return None
   ```

5. In `server_updates.py`, annotate `repository: VIOSSource`, select
   `operation = "UpdateVIOS" | "UpgradeVIOS"`, validate/build before
   `client_from_env`, encode `quote(vios_uuid, safe="")`, submit to the fixed
   suffix, and after `_update_op` add a top-level `stdOut` only when
   `wait is True`, the result is a mapping, and `vios_stdout(result)` returns a
   value. Copy the mapping before augmentation.
6. Run the focused tests and static checks for the two changed modules; expect
   exit 0. Run `just smoke`; expect the tool count summary and exit 0.
7. Commit with subject `fix: submit documented VIOS update operations`.

Acceptance: path selectors cannot redirect the operation; invalid input reaches
no client; waited result projection follows the exhaustive ordering matrix;
the complete job remains available; asynchronous output is byte-for-byte equal
as a mapping to the submission parser result.

## Task 3: Document and verify the complete contract

**Interfaces**

- Consumes the final public signature and behavior from Tasks 1 and 2.
- Produces operator documentation only; no new runtime interface.

1. Update the `README.md` tool row and nearby update documentation with one
   update and one upgrade request example, exact operation-specific differences,
   and the wait-only `stdOut` behavior. Remove statements that VIOS shares the
   console repository format.
2. Compare the implemented paths, enums, and parameter sets against all four
   named Power10/Power11 captures from the spec. Use `rg` and line-numbered
   reads; any disagreement is a source/test correction, not a doc caveat.
3. Run `just test`, `just smoke`, then `just verify` bare. Each must exit 0.
   Run `git status --porcelain` after verification and require no untracked or
   unstaged generated artifact.
4. Commit explicit documentation paths with subject
   `docs: describe documented VIOS update inputs`.

Acceptance: README instructions match the installable schema and tests; both
reference generations agree; all guardrails pass with zero warnings. Live HMC
testing runs only if a suitable non-production update target and credentials
already exist; otherwise record `not run: no safe live VIOS update target` in
the handoff.

## Rollback and cleanup

Each task is a separate conventional commit and can be reverted in reverse
order. Reverting the runtime commits restores the broken operation and is not a
compatibility strategy. No migration, generated registry, dependency, external
state, or temporary fixture persists. Leave the external worktree and branch
for the campaign orchestrator after PR handoff.
