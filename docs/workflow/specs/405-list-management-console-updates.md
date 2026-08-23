# List available HMC updates

Issue #405 replaces the invalid `SoftwareUpdate` group request with IBM's documented
`ListManagementConsoleUpdates` job.

The public tool accepts `console_uuid`, `wait=False`, `timeout_seconds=300`,
`poll_interval=5`, and `profile=None`. Timing arguments are validated before client I/O.
The request is a parameterless XML JobRequest naming operation
`ListManagementConsoleUpdates` and group `ManagementConsole`, submitted to
`/rest/api/uom/ManagementConsole/{UUID}/do/ListManagementConsoleUpdates`.

Without waiting, the tool returns the submission response. With waiting, it follows the
job's returned identity/self link and returns the first terminal response; timeout and HMC
errors use the existing stable job behavior. The obsolete REST0026 rewrite is removed.

Acceptance is covered by focused tests for request path/body, immediate and waited
responses, submission failure propagation, and invalid timing inputs before network I/O.
