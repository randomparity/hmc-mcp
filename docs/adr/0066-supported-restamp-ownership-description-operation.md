# ADR 0066: Supported Re-Stamp / Ownership-Description Operation

## Status

Accepted

## Context

Writing an ADR 0011 ownership token was possible only through
`create_and_stamp_lpar`, which stamps once at create time. Three consumer needs
had no supported call at all:

1. **Re-stamping after a best-effort stamp failed.** `create_and_stamp_lpar`
   can return `ownership_stamped=False` with the LPAR created, leaving the
   consumer holding an unowned LPAR it created, with no way to retry.
2. **Rewriting the description at pool return or handover.** Transferring an
   LPAR from one owner to another means writing a new token — unsupported.
3. **Any guarded description write from the facade.** The validate-guard-write
   composition (`validate_lpar_description` → `authorize_lpar_mutation` →
   `set_lpar_description`) existed only as duplicated copies inside two
   presentation modules: the MCP tool body (`server_lpar_config.py`) and the
   CLI command body (`cli_lpars.py`). This is the layering problem ADR 0013
   assigns to operation modules; the copies could drift.

Issue #358 (ADR 0064) settled the create-time token format this operation must
be able to write.

## Decision

`operations_lpar` gains one presentation-neutral async operation:

```python
async def set_lpar_ownership_description(
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    description: str,
    *,
    ownership_override: bool = False,
) -> str
```

It validates the description text first (before any HMC traffic), resolves the
target, enforces the description-field ownership token via
`authorize_lpar_mutation`, then performs the single SSH write via
`set_lpar_description`. The caller composes the description itself in the
ADR 0011 ownership format plus optional ADR 0064 caller segment; the operation
is format-agnostic and writes whatever text passes validation and the guard.

The operation is exported from `hmc_mcp.api`, expanding the manifest, so per
ADR 0029's release policy this addition requires a minor release. The ADR 0029
`operations_lpar` inventory is updated accordingly, and the frozen signature
digest in `tests/unit/test_public_api.py` is recomputed.

The MCP tool `hmc_set_lpar_description` and the CLI `lpars set-description`
command become thin delegates to the operation. Both duplicated copies of the
guard-and-write composition are deleted rather than left alongside the new
operation. The tool keeps its existing contract: validation rejects bad text
before any network activity, because validation is the operation's first
statement.

## Consequences

- Facade consumers can re-stamp a failed create-time stamp, rewrite the token
  at pool return or handover, and record their own metadata — each with the
  same ownership protection the presentation layers already enforced.
- One composition instead of two drifting copies; the presentation modules no
  longer import the guard primitives directly.
- No new HMC traffic pattern: still exactly one SSH write per call, preceded by
  the same REST resolution and authorization reads the tool path performed.
