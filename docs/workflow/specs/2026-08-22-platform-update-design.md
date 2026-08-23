# Platform update contract design

## Goal

Correct `hmc_update_firmware` so it submits IBM's Power11 `PlatformUpdate` job with its exact
nested JSON envelope and refuses unsupported HMC versions before any job submission.

This design implements issue #396 and [ADR 0075](../../adr/0075-use-json-platform-update-job.md).

## Contract

The public MCP tool remains `hmc_update_firmware`. Its second argument becomes
`platform_update: PlatformUpdateParameter`; the obsolete `repository: RepositorySource` input and
`update_firmware_job` builder are removed. Strict frozen Pydantic models with
`extra="forbid"` explicitly model:

- `SystemFirmwareUpdate` with `UpdateType` (`Update`, `Upgrade`, or `NoUpdate`), integer
  `UpdateOrder`, and optional nested `SRIOVAdapterUpdate` entries;
- each SR-IOV entry with `AdapterID` and a case-sensitive `SubType`. IBM's table names
  `adapterdriver` and `Adapter`, while its samples use `adapterdriver` and
  `adapterdriver,adapter`; the model accepts exactly those three values without rewriting and
  rejects standalone lowercase `adapter` and other casing;
- `VIOSUpdate` entries with update type and VIOS identity, plus optional order, image name,
  resource type, and nested IO-adapter updates. `ResourceType` is required when `UpdateType` is
  `Update`, sample-demonstrated lowercase `update`, or `Upgrade`; IBM's documented `NoUpdate`
  IO-adapter-only shape may omit it. System firmware admits only the table values `Update`,
  `Upgrade`, and `NoUpdate`; VIOS additionally admits and preserves lowercase `update` because an
  IBM request sample uses it. `UpdateOrder` and `Name` remain optional because IBM's examples omit
  them in otherwise valid requests.
  `ResourceType`, when present, is the case-sensitive literal union `HMC`, `NFS`, `SFTP`, `USB`,
  or `IBMWebsite` and is preserved without rewriting; and
- `IOAdapterUpdate` entries nested under their VIOS entry, with partition id, device, and
  repository. The case-sensitive repository set accepts the table values `MOUNTPOINT`, `SFTP`,
  `USB`, `IBMWebsite`, and `DISK`, plus sample-demonstrated `disk`; values are preserved exactly.

The system-firmware and VIOS sections are optional so callers can select the platform components
IBM permits. Cross-field validation requires at least one real action: a system-firmware or VIOS
entry uses `Update`, `update`, or `Upgrade`, or a `NoUpdate` entry carries a non-empty corresponding
SR-IOV or IO-adapter list. Empty top-level objects, empty adapter lists, and populated
all-`NoUpdate` objects without adapter work are rejected before client construction. Nested strict
models expose the documented keys and literal values to MCP schema generation and reject unknown
keys at every level. The model deliberately omits IBM's separately documented partition-migration
section because this firmware-update tool does not authorize partition relocation.

`platform_update_job` returns a native mapping with exactly this envelope:

```text
JobRequest
  RequestedOperation: OperationName=PlatformUpdate, GroupName=ManagedSystem
  JobParameters
    JobParameter[0]: ParameterName=PlatformUpdateParameter,
                     ParameterValue=<the supplied object>
```

The nested parameter object is not JSON-encoded into a string.

## Data flow and compatibility gate

The tool validates wait settings and non-empty update input, opens the selected HMC profile, and
calls `get_console_info`. It reads `Resource.VersionInfo` and accepts the established HMC form
`V<major>R<release>M<maintenance>`. A tuple comparison requires `(11, 1, 1111)` or newer. A missing
console record, missing version, malformed version, or lower version raises `ValueError` naming
`PlatformUpdate` and the `HMC 11.1.1111 or later` requirement. This occurs before system-name
resolution and before submission, ensuring unsupported HMCs receive no PlatformUpdate write.

After the gate, existing system resolution supplies the UUID. The new
`HMCClient.submit_json_job(path, request)` performs PUT with
`Content-Type: application/vnd.ibm.powervm.web+json; type=JobRequest` and
`Accept: application/json`. It accepts only the native request mapping, checks the existing job
success statuses, and parses a JSON response when present. Empty success responses remain `None`.
An empty successful response returns `None`. A non-empty successful response must be a JSON object
with a non-empty string `id`, object-valued `content.JobResponse`, and a non-empty string `Status`.
`selfLink` must be null or a non-empty string. Optional `Result` must be a list of objects; every
entry must have non-empty string `ParameterName` and string `ParameterValue` (an empty value is
preserved). Any violation raises `HMCError` naming a malformed PlatformUpdate response and the
invalid field, without echoing payload values. Other documented JobResponse fields are preserved.

Normalization maps top-level `id` to `UUID`, copies `content.JobResponse` to `Resource`, maps a
non-empty `selfLink` to `link`, and moves every validated `Resource.Result` entry to
`Resource.Results.JobParameter`. The original singular `Result` key is removed so downstream code
has one representation. Existing `ResponseException` and other JobResponse failure fields remain
inside `Resource`. Existing XML `submit_job` behavior is unchanged.

Waiting reuses `wait_for_submitted_job`. A normalized response already carrying a terminal
`Resource.Status` is returned without a redundant poll. A nonterminal response uses a non-empty
normalized `selfLink` through the existing polling boundary. Because the supplied P11 reference
does not document a portable status endpoint for an id-only response and the legacy root Job path
is known to fail on some HMCs, `wait=True` with a nonterminal response and no link raises an
actionable error explaining that the update was accepted but cannot be polled. It never guesses a
status URL.

## Error handling

- Empty or semantically no-op update object: reject before client construction.
- Missing or unparseable HMC version: fail closed and name how to determine compatibility.
- HMC older than 11.1.1111: reject before system resolution or PUT.
- Non-2xx submission: raise a sanitized `HMCError` naming the PUT operation and HTTP status without
  including the response body, because an HMC validation error may echo submitted values.
- Invalid successful JSON: raise `HMCError` naming the malformed PlatformUpdate response.
- Invalid wait timings and unresolved systems retain existing behavior.

## Security model

### Boundaries and actors

The existing authenticated MCP caller controls the nested update values and can trigger a
destructive HMC operation. The change adds JSON serialization at the existing authenticated HMC
transport boundary; it does not widen tool authorization, profile selection, or credential access.
Remote repository names, devices, image names, and adapter identifiers are untrusted strings.

### Controls

Strict nested Pydantic models reject extra keys and constrain literal enums before dispatch. Tests
exercise unknown keys at the top level and each nested level through the actual MCP schema boundary.
HTTPX's `json=` encoding serializes string values rather than concatenating JSON, preventing
structural injection. JSON submission errors suppress the HMC response body so reflected input
values do not cross back into MCP error output.
System UUIDs retain path quoting. The existing destructive-tool authorization metadata continues
to gate invocation. Errors name operation and version requirements but do not include credentials
or payload values.

### Out of scope

The HMC decides whether named firmware, VIOS images, devices, and adapters exist and whether the
authenticated account may update them. This change does not add live credentials, perform a live
update during tests, or broaden the repository's authorization model.

## Testing

- Builder tests assert full native mapping equality for documented system-firmware/SR-IOV,
  IBMWebsite VIOS, lowercase-`update` VIOS, and `NoUpdate` IO-adapter-only shapes. Parameterized
  model tests cover every admitted UpdateType, SubType, resource type, and repository literal,
  reject near-miss casing, enforce conditional VIOS resource requirements, and reject empty or
  all-NoUpdate/no-adapter requests.
- Client tests assert method, exact path, exact media headers, JSON body, response parsing, empty
  response handling, non-success errors, malformed/wrong-shape successful JSON, and preservation of
  a documented `COMPLETED_WITH_ERROR` result through `job_outcome`. A non-2xx sentinel test proves
  an HMC body echoing a submitted value is absent from the error. Rejection cases separately
  cover invalid root, id, content, JobResponse, Status, selfLink, Result container, Result entry,
  ParameterName, and ParameterValue types without reflecting their values in errors.
- Tool tests prove a supported version submits the exact PlatformUpdate request, older/missing/
  malformed versions submit nothing and return actionable errors, empty input opens no client, and
  system UUID path quoting remains intact. A `wait=True` test uses IBM's lowercase `id` and nested
  `content.JobResponse` shape to prove normalization and terminal-response handling.
- Schema/security tests assert the replacement argument, strict nested schema, and unknown-key
  rejection at every nesting level while preserving destructive metadata. They cover every VIOS
  resource literal and reject near-miss casing.
- README documents the version floor and a representative request.
- `just test`, `just smoke`, and `just verify` must pass. Live HMC validation is reported separately
  and is not fabricated when no suitable P11 HMC is available.

## Scope and rollback

Only the update builder/types, narrow JSON client method, firmware tool, directly relevant tests,
README, ADR 0075, and generated artifacts required by guardrails may change. A git revert restores
the former client and tool contract; it also restores the known-invalid UpdateFirmware behavior,
so rollback is operationally safe only by disabling the tool until this correction returns.
