# ADR 0080: List management-console updates through its documented job

## Status

Accepted

## Context

The available-PTF tool requests a `SoftwareUpdate` extended group that IBM does not
document and HMC rejects with REST0026. Power10 and Power11 documentation instead define
`ListManagementConsoleUpdates` as a parameterless `ManagementConsole` job. Its completed
result contains the available PTF objects.

## Decision

The tool submits `ListManagementConsoleUpdates` with an empty `JobParameters` element.
It follows the repository's stable job contract: `wait=False` returns the submitted job;
`wait=True` polls the returned job reference until a terminal state, bounded by
`timeout_seconds` and `poll_interval`. Submission and terminal job errors pass through
without replacing the HMC diagnosis.

## Consequences

The tool now performs a PUT and may contact IBM through the HMC. Existing callers still
receive immediately by default, but receive a job resource rather than console attributes.
Callers that need the PTF array set `wait=True` and inspect the completed job result.
