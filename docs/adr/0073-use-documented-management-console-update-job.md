# ADR 0073: Use the documented management-console update job

## Status

Accepted

## Context

The console update tool currently submits undocumented `Update` and `Upgrade` jobs and
reuses the VIOS repository parameter vocabulary. IBM's Power10 and Power11 references
instead define `UpdateManagementConsole` with a distinct parameter set. They define an
HMC upgrade as a sequence of jobs, not one `Upgrade` operation.

## Decision

Console updates use `UpdateManagementConsole` and a console-specific `TypedDict` whose
keys are the documented job parameter names. The console-update operation exposes no
mode selector: it performs the one supported update job. A future console-upgrade
workflow requires a separately named operation after the documented multi-job sequence
is implemented; a permanently refused selector is not an API contract.

## Consequences

Update callers must provide the documented console parameter names. Upgrade calls no
longer have a phantom entry point. VIOS and firmware jobs retain their existing repository
contract.
