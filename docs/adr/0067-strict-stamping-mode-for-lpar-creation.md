# ADR 0067: Strict Stamping Mode for LPAR Creation

## Status

Accepted

## Context

ADR 0011 made create-time ownership stamping best-effort by design: a failed
stamp degrades to `ownership_stamped=False` plus a warning while
`create_and_stamp_lpar` still reports success. That is right for the
interactive MCP/CLI caller ADR 0011 was written for, and wrong for an
orchestrator. An orchestrator that receives success for an untagged LPAR holds
a silent orphan: it occupies real system resources, and every other ADR 0011
consumer — including the human operator reading the HMC GUI Partitions tab —
reads an unowned partition as free to delete. A failed create would have been
the better outcome: clean, retryable, nothing allocated.

Issue #377. The gap this leaves open depends on the retry path from issue
#376 (ADR 0066): raising without a supported re-stamp call would strand the
caller with an orphan and no way to fix it.

## Decision

`LparCreation` gains one field:

```python
stamp_policy: Literal["best-effort", "required"] = "best-effort"
```

The default stays `"best-effort"`; ADR 0011's choice and every existing
caller are unchanged, byte for byte, including `ownership_stamped` and the
warning texts.

Under `"required"`, `create_and_stamp_lpar` raises `HMCError` *after* the
create completes whenever the stamp did not land — a swallowed
`HMCCLIError`/`OSError`/`ValueError`, an unresolved system name, or a missing
partition name in the create body. The exception carries the new LPAR's name
and UUID so the caller can find the partition that now exists; an error that
only says "stamping failed" is not actionable. The LPAR still exists when the
error is raised — the create is not rolled back — and the operation's
docstring says so plainly, pointing at `set_lpar_ownership_description`
(issue #376, ADR 0066) as the retry path and deletion as the cleanup path.

An unknown `stamp_policy` value raises `ValueError` before any HMC traffic,
as the operation's first statement, mirroring the ADR 0064 caller-token
pre-flight.

The best-effort swallow inside `stamp_lpar_ownership` (`ssh_commands.py`)
stays exactly where it is. Strictness is enforced above that boundary, on the
returned outcome, so the two policies share one stamping code path and cannot
drift.

Adding the field changes `LparCreation`'s constructor signature, which moves
the frozen signature digest of `tests/unit/test_public_api.py`; per ADR 0029
this requires a minor release.

## Consequences

- Orchestrators can demand a tagged partition or a hard failure; they never
  again receive silent success for an unowned LPAR.
- Interactive callers keep today's behavior with no migration.
- A strict-mode failure surfaces after allocation, so the error message must
  carry the identity needed to clean up — guaranteed by the name+UUID
  contract above.
- `operations_templates.deploy_partition_template` keeps calling
  `stamp_created_lpar_ownership` directly and remains best-effort; template
  deployment gains no strict mode in this change.
