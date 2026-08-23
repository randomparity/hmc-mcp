# ADR 0075: Use the JSON PlatformUpdate job

## Status

Accepted

## Context

`hmc_update_firmware` currently submits an undocumented `UpdateFirmware` operation with
generic repository parameters. IBM's Power11 REST reference instead defines `PlatformUpdate`
for HMC 11.1.1111 and later. Its sole `PlatformUpdateParameter` value is a nested JSON object,
which the existing XML `build_job_request` can represent only by stringifying the nested object.

## Decision

Keep `hmc_update_firmware` as the public tool name, but replace its repository argument with an
explicit typed PlatformUpdate parameter object. PUT it to
`/rest/api/uom/ManagedSystem/{UUID}/do/PlatformUpdate` with
`Content-Type: application/vnd.ibm.powervm.web+json; type=JobRequest` and
`Accept: application/json`. The native JSON `JobRequest` contains `RequestedOperation` naming
`PlatformUpdate` and `ManagedSystem`, plus one `JobParameter` named
`PlatformUpdateParameter` whose `ParameterValue` is the supplied object, not a string. IBM's
Power11 reference captured 2026-08-22 verifies this wire shape.

Build that envelope in `jobs.py` and submit it through a dedicated JSON client method. Before
resolving a managed system or submitting work, read the management-console version and require
`V11R1M1111` or later; missing, malformed, or older versions fail closed with an actionable
requirement.

The existing XML job builder and submission method remain unchanged for every other operation.

## Consequences

- Callers must supply documented platform-update fields rather than the removed generic
  repository shape.
- HMC versions older than 11.1.1111 cannot receive the operation.
- The client gains one narrowly scoped JSON job-submission boundary alongside its XML boundary.
- The payload shape is testable as native JSON without stringifying its nested parameter value.

## Considered & rejected

- **Encode the nested value into an XML JobParameter.** judgment: IBM's Power11 PlatformUpdate
  reference, captured 2026-08-22, documents both native JSON and XML with stringified JSON;
  choosing native JSON preserves the explicit object model and avoids operation-specific
  serialization inside the generic XML builder.
- **Generalize `submit_job` with content-type switches.** judgment: one method accepting multiple
  representations makes the established XML contract conditional; a dedicated JSON boundary is
  smaller and keeps existing callers unchanged.
- **Let unsupported HMCs reject the request.** verified: the Power10 managed-system job index has
  no PlatformUpdate operation, while the Power11 reference states the 11.1.1111 minimum; local
  validation can prevent the known-invalid write.
- **Retain the old repository argument as a compatibility path.** judgment: it cannot map
  truthfully onto the documented PlatformUpdate object and would preserve two contracts for one
  tool.
- **Leave the existing UpdateFirmware behavior unchanged.** verified: `rg -n UpdateFirmware`
  finds the operation in this repository, while the supplied Power10 and Power11 IBM reference
  snapshots captured 2026-08-22 contain no occurrence; retaining it preserves a write contract
  with no support in the available vendor evidence.
