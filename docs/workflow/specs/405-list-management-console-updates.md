# List available HMC updates

Issue #405 replaces the invalid `SoftwareUpdate` group request with IBM's documented
`ListManagementConsoleUpdates` job.

The public tool preserves `console_uuid, profile=None` positional binding and adds
keyword-only `wait=False`, `timeout_seconds=300`, and `poll_interval=5`. Timing arguments
are validated before client I/O.
The request is a parameterless XML JobRequest naming operation
`ListManagementConsoleUpdates` and group `ManagementConsole`, submitted to
`/rest/api/uom/ManagementConsole/{UUID}/do/ListManagementConsoleUpdates`.

Without waiting, the tool returns the submission response. With waiting, it follows the
job's returned identity/self link and returns the first terminal response; timeout and HMC
errors use the existing stable job behavior. The obsolete REST0026 rewrite is removed.
The tool is marked mutating because it enqueues work and may cause HMC egress to IBM;
authorization remains scoped to the selected console UUID and connection profile.

Acceptance is covered by focused tests for request path/body, immediate and waited
responses, submission failure propagation, and invalid timing inputs before network I/O.
