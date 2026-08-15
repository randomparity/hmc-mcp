# Registry-wide tool parameter descriptions plan

**Issue:** #146

**Base:** `main`

**Host/targets:** arm64; amd64, arm64, ppc64le; host included

**Guardrails:** `just verify`; `UV_NO_SYNC=1 uv run prek run --all-files`

## Files and responsibilities

- `tests/app/test_capabilities.py`: recursive schema-description assertion and both registry modes.
- `src/hmc_mcp/server_{network,storage,adapters,users,metrics,updates,lpar_config,system_resources,templates,profiles}.py`:
  rendered top-level parameter descriptions.
- `src/hmc_mcp/server_health.py` and `src/hmc_mcp/server_command.py`: description-only fixes
  required by the current default and conditional live registries.
- `src/hmc_mcp/documents.py`: descriptions for nested public input fields not already covered.
- `src/hmc_mcp/jobs.py`: description metadata for nested update-repository fields.
- `README.md`: current tool inventory text.

## Tasks

1. Add the recursive checker and a focused synthetic-schema negative test that does not mutate
   the registry. Run the live-registry test and confirm it fails for named missing paths.
2. Convert each remaining public tool docstring to Google-style `Args:` sections, preserving all
   behavioral prose and documenting every signature parameter accurately. Run the focused
   registry tests after each domain group.
3. Add nested field metadata where the registry checker reports missing `$defs` properties and
   verify the negative test still catches an intentionally undescribed nested field.
4. Reconcile the README tool table with the live registry.
5. Run `just verify` and `UV_NO_SYNC=1 uv run prek run --all-files` bare. Review the diff for
   accuracy, naming, and unnecessary complexity before committing.

The first executable implementation action is the failing registry test. No task changes public
signatures, defaults, validation, or response shapes.
