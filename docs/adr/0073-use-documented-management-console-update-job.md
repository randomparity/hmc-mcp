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
keys are the documented job parameter names. The existing `kind` argument remains so
callers receive an actionable error for `kind="upgrade"`; the tool rejects that value
before opening an HMC connection and names the documented multi-job workflow. This PR
does not automate that workflow.

## Consequences

Update callers must provide the documented console parameter names. Upgrade calls no
longer submit a request that cannot succeed. VIOS and firmware jobs retain their existing
repository contract.
