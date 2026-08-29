# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project
pre-1.0, so versions follow ADR 0029 (`docs/adr/0029-supported-reusable-python-api-contract.md`):
any change to the facade manifest — adding, removing, or renaming an export of
`hmc_mcp.api`, or changing an exported enum member or literal alternative — requires a minor
release during `0.x`.

## Convention: the Facade manifest section is mandatory

Every release entry below **must** contain a `### Facade manifest` section, even when nothing
moved. An entry whose manifest section says "no change to `hmc_mcp.api.__all__`" converts silence
into a positive statement for consumers deciding whether an upgrade can break them. Where the
manifest changed, the section names every added, removed, and renamed export, and every changed
exported enum member or literal alternative.

A metadata test (`tests/unit/test_changelog.py`) enforces this contract: the version declared in
`pyproject.toml` must have a matching entry, every release entry must carry a non-empty
`### Facade manifest` section, and that section's *content* is checked — every export in
`hmc_mcp.api.__all__` that the oldest entry's enumerated manifest does not already name must be
named in the `[Unreleased]` manifest. The repository carries no git tags, so that enumeration,
not a tag, is the boundary the delta is derived against. Removals and renames stay outside the
mechanism: a removed export is absent from `__all__`, and with no per-release snapshot to diff
against there is nothing to corroborate a `Removed:` or `Renamed:` line.

## [Unreleased]

### Changed

- `modify_system` now accepts a cohesive `ManagedSystemPatch`, and the LPM
  validation and migration operations accept `LpmMigrationRequest` for their
  destination-specific inputs. Shared wait and authorization controls remain
  explicit keyword-only arguments.
- Mutation results now identify the affected resource consistently: `delete_adapter`
  returns the deleted adapter UUID, and `create_virtual_network` returns a
  `VirtualNetworkResult` containing the resolved system UUID and HMC resource.
- `hmc_create_volume_group` now declares its actual HMC resource payload return
  (`dict | None`) instead of the unrelated storage-mapping workflow result.
- Capacity and summary contracts now name mebibyte values explicitly:
  `desired_memory_mib`, `current_memory_mib`, `total_memory_mib`,
  `assigned_memory_mib`, and `free_memory_mib` replace their misleading
  `*_mb` names across the facade, MCP tools, CLI adapters, and live-test state.
- `ConsoleCapture` now preserves a bounded, single-line transport diagnostic in
  `error` when `stop_reason` is `"error"`. Partial console bytes and mandatory
  vterm release behavior are unchanged; non-error captures report `error=None`.
- Exported vSCSI and vFC adapter creation operations now match network adapter
  creation by accepting the optional client slot as keyword-only
  `slot_number=None`.
- Provisioning workflow results now report virtual-disk capacity as
  `capacity_mib`, matching the value's mebibyte unit.
- Exported LPAR operations now place `system_name_or_uuid` before
  `lpar_name_or_uuid`, including LPM, disk attachment, summaries, ownership
  authorization, and PCIe assignment workflows. Presentation adapters pass
  `None` when they intentionally request fleet-wide LPAR-name resolution.

### Added

- Seven bounded SSH-backed VIOS label tools list, set, and remove individual FC-port
  labels and list, create, update, and remove individual vFC group labels using the
  POWER10 and POWER11 `lslabelvios`/`labelvios` grammar. They do not expose MSP,
  vNIC, vSCSI, override-default, bulk-removal, or adapter-mutation behavior. Issue
  #559 tracks live-system field and mutation evidence not established by the manuals.
  The matching `hmc-mcp vios` commands are `list-fc-port-labels`,
  `set-fc-port-label`, `remove-fc-port-label`, `list-vfc-group-labels`,
  `create-vfc-group-label`, `update-vfc-group-label`, and `remove-vfc-group-label`.
- `ManagedSystemPatch` and `LpmMigrationRequest` provide reusable typed request
  values for managed-system configuration and LPM destination inputs.
- `VirtualNetworkResult` exposes the resolved managed-system UUID beside the
  resource returned by `create_virtual_network`.
- `DecommissionBlastRadius` and `DecommissionAdapterRecord` expose the fixed
  `DecommissionResult.blast_radius` inventory schema to reusable Python callers.
- `StorageMapResult` records the authorized LPAR UUID beside the resource returned by
  `map_storage`, so library and CLI callers no longer resolve the partition independently
  before the guarded storage operation (ADR 0104).
- Opt-in ADR 0011 ownership guard on LPAR power operations (#371, ADR 0092 §4): the new
  `authorize_power_operations` setting (`HMC_AUTHORIZE_POWER_OPERATIONS`, TOML profile key
  `authorize_power_operations`) defaults to `false`, leaving the `power_lpar` call path
  unchanged and opening no SSH connection. When `true`, `power_lpar` reads the ownership
  token before submitting the job and refuses a partition another agent owns; it accepts a
  new `ownership_override` parameter with the same audited semantics as its guarded
  siblings. `system_name_or_uuid` stays optional (ADR 0063): the guard routes through
  ADR 0094's shared resolve chain, which derives the owning managed system by bounded
  parent discovery when the caller omits it and confirms the partition lives on the
  named system when they supply it. `hmc_power_on_lpar`, `hmc_power_off_lpar`, and
  `hmc-mcp lpars power-on` / `power-off` gained the matching `ownership_override`
  argument, and the two CLI commands gained `--system`, which replaces the fleet walk
  with one read. `provision_lpar` passes the override on its own activation leg, which
  targets the partition that workflow just created and stamped. Both new parameters move
  the frozen public signature digest.
- `HMCConfig.from_mapping(values)`: environment-isolated construction for library
  consumers (#368, ADR 0096). `HMCConfig` is a pydantic-settings model with
  `env_prefix="HMC_"`, so the ordinary constructor resolves every field a caller leaves
  unset from the ambient process environment — right for the CLI and the MCP server,
  wrong for a process building one config per HMC from database rows, where a stray
  `HMC_HOST` silently retargets a backend, a stray `HMC_SSH_KEY_FILE` offers the wrong
  key, and a stray `HMC_AGENT_ID` corrupts ADR 0011 ownership attribution.
  `from_mapping` reads no environment variable and no dotenv file; every field the
  mapping omits takes its declared default. `load_profile` is unchanged —
  environment-over-TOML precedence on the operator path is deliberate. **Note:**
  `_env_file=None` is *not* an isolation mechanism — it suppresses a dotenv source, never
  the environment, and `HMCConfig` configures no dotenv source at all, so it does nothing.
  `docs/environment-variables.md` gains a "Library Consumers" section covering precedence
  and the isolation pattern.
- Cross-process job polling: the `get_job` and `wait_for_job` operations in the new
  `hmc_mcp.operations_jobs` module, plus the `JobOutcome` facade export (#364, ADR 0093). The
  supported handle for a job is two persistable strings — `job_id` and an optional `job_href` —
  so a consumer can store them, restart, construct a fresh `HMCClient`, and poll from a different
  process than the one that submitted the work. A job the HMC no longer knows about (reaped,
  deleted, or never present) now returns `found=False` instead of an opaque HTTP 404 `HMCError`,
  which is what lets a restarted worker tell "still running" from "gone". `JobOutcome` gained the
  `found: bool` and `job_href: str | None` fields. Neither has a default — that keeps both in the
  `required` set of the MCP output schema the wait tools share — so any direct construction of
  `JobOutcome` from the previous five fields must now supply them. Its field set is a package-owned
  model contract under ADR 0029, except `job`, which stays an opaque HMC resource mapping. The
  handle and `found` semantics above describe outcomes returned by `get_job` and `wait_for_job`;
  the submitting operations share the type but report a submission, so their `job_id` may be a
  synthetic label and their `found=False` means "this submission returned no job entry"
  (ADR 0093 clause 3).
- `set_lpar_ownership_description` operation and facade export (#376, ADR 0066).
- Strict LPAR ownership stamping (#377, ADR 0067): `provision_lpar` accepts a new
  `stamp_policy` field on `LparCreation` with literal alternatives `"best-effort"` (default) and
  `"required"`.
- Audit sink emits a `tls-verification-disabled` event when TLS verification is off, as
  controlled by `HMC_VERIFY_SSL` (#379).
- `list_lpar_ownership` operation, facade export, and `hmc_list_lpar_ownership` MCP tool
  (#375, ADR 0071): parsed ADR 0011 ownership per LPAR from the REST bulk list feed — one
  call covers every partition on a managed system. Partitions with no description
  (REST `<Description>` element absent), a non-token description (`unparsed`), and a valid
  ownership stamp (`owned`/`owner`) are reported as distinct facts; none are dropped.
- Bounded, non-interactive LPAR console capture: `capture_lpar_console`
  operation, `ConsoleCapture` / `ConsoleHeldError` facade exports, and the
  `hmc_capture_lpar_console` MCP tool (#385, ADR 0072). Runs `mkvterm` over a
  dedicated asyncssh process session with stdin sealed by construction (no
  code path can write to the partition console), enforces client-side
  duration/max-bytes/idle bounds, returns raw bytes (truncation never splits a
  multi-byte UTF-8 or incomplete ANSI escape sequence), detects vterm
  contention by the recorded stdout sentinel (exit code is always 0), and
  runs `rmvterm` on every exit path — with `released` true only after an
  independent-session mkvterm probe proves the slot free. Designed from the
  P1–P8 live-hardware prototype recorded on #385 (HMC V10R3 M1060); only idle
  partition streams were observed live, so the ANSI truncation rule is
  protocol-derived.
- Facade exports for four operations ADR 0029's selection rule already covered but the manifest
  omitted: `list_optical_mappings`, `mount_optical_media`, `unmount_optical_media` (shipped
  unexported by #205) and `assess_post_activation_affinity` (shipped unexported by #318) (#363).
  A contract test now applies the selection rule to every `operations_*` module by introspection
  and replaces the hand-written module-to-names map that could not detect the omission, since the
  same edit maintained both the map and `__all__`. **Consumer note:** ADR 0092 §3.2 records
  `mount_optical_media` and `unmount_optical_media` as ownership-unguarded — they mutate a named
  client partition without an ADR 0011 ownership check. Exporting them does not change that; #440
  adds the guard, and doing so will add an `ownership_override` keyword to both signatures.
- `set_lpar_processors` and `set_lpar_memory` operations and facade exports (#365, ADR
  0013/0029/0092/0094): the DLPAR processor and memory workflows move out of the
  `hmc_dlpar_proc` / `hmc_dlpar_mem` tool bodies into `operations_lpar`, so a consumer already
  running an event loop can call them — the tool path reached them only through `asyncio.run`,
  which raises inside a running loop. Tool names and existing parameters are unchanged.
  **Both are now guarded.** ADR 0092 §3.2 classifies DLPAR resource changes as Reconfiguring,
  which must be guarded unconditionally, so each calls `authorize_lpar_mutation` before the write
  and each tool gains an appended `ownership_override: bool = False`. Two consumer-visible
  consequences: a guarded call now needs SSH reachability to the HMC as well as REST, because the
  ADR 0011 token is read over SSH (#459 makes that failure actionable); and a call that omits
  `system_name_or_uuid` now discovers the owning managed system by a bounded fleet walk, because
  the guard is keyed by CLI system name — supply the selector to skip it. ADR 0094 records the
  derivation and its alternatives.
- `install_lpar_os` and `install_vios` operations and facade exports (#366, ADR 0013/0029/0070):
  the `installios` orchestration moves out of the `hmc_install_lpar_os` / `hmc_install_vios` tool
  bodies into a new `operations_install` module, so a consumer already running an event loop can
  call it — the tool path reached it only through `asyncio.run`. Both return the CLI bridge's
  detach handle (resolved system and partition names, the remote PID, the install log path, and a
  message restating them), not an HMC job identifier: there is no HMC job on this path (ADR 0069)
  and nothing to poll. Tool names, parameter lists, and returned payloads are unchanged. Both are
  classified in ADR 0092 §3.4a — outside §1's ownership rule by resource type, since `installios`
  requires a Virtual I/O Server partition and ADR 0011 stamps no token on one.
  **Consumer note:** submission is not idempotent and neither operation checks the target's
  partition type. A second concurrent call submits a second detached `installios` against the same
  partition, and the install log path is keyed on the partition *name* alone — the managed system
  is not part of it, and the redirect truncates — so two same-named partitions on different
  managed systems behind one HMC share one log and destroy each other's only diagnostic record.
  The returned `log_path` is not unique per system; serializing per partition name across every
  managed system on the HMC is the caller's responsibility. A returned handle means the process
  was backgrounded, not that `installios`
  accepted the target — a refused non-VIOS target surfaces only in the HMC-side log (#460). Adding
  a target-type check raises a new `ValueError` but adds no parameter, so it will not move the
  frozen signature digest.
- `ownership-denied` audit record for a refused ADR 0011 ownership check (#467, ADR 0100).
  The guard recorded approved overrides and nothing else, so an operator reading the stream
  could not tell "nobody tried to mutate a partition they do not own" from "many attempts were
  refused" — and on the CLI and Python API paths, which no access policy reaches, a refusal
  left no trace at all. Both denial branches now emit one `WARNING` record naming the guard
  entry point (`operation`: `lpar-mutation` or `lpar-decommission-snapshot`), the rule that
  refused (`denial`: `foreign-owner` or `malformed-token`), the system, the partition, the
  owner the LPAR's token claims (`null` on `malformed-token`), the HMC, and the acting agent.
  `ownership-override` is untouched — same name, same fields, same level — so an
  `event == "ownership-override"` filter keeps counting approved bypasses and nothing else.
  `docs/authorization-audit.md` documents the record and the caveats on alerting from it.
  No exported signature changes.
- `hmc_effective_permissions` reports `power_ownership_guards` (#470): the effective,
  post-precedence `authorize_power_operations` (ADR 0092 §4) for every connection the access
  policy's grants name, resolved inside the running server rather than in the shell that
  asks. The guard fails **open** and `HMCConfig` sets `extra="ignore"`, so a mistyped profile
  key or environment variable is dropped silently and is otherwise indistinguishable from a
  correct `false`; each entry carries the `source` that supplied the value — `environment`,
  `profile`, or `default`, where `default` is the answer that means nothing the operator
  wrote arrived. A fourth value, `ambiguous`, reports that a **case variant** of
  `HMC_AUTHORIZE_POWER_OPERATIONS` is exported: pydantic-settings matches a variant
  case-insensitively while the profile loader drops only the exact upper-case spelling, so a
  variant loses to a profile on one resolution path and wins on the other, and nothing in the
  server can tell which happened. **The #531 fix in this same release removes that
  divergence**, so `ambiguous` ships over-reporting: a variant now drops the profile's key on
  both paths and `environment` is the truthful label. Read `ambiguous` as `environment`;
  #547 tracks removing the value. `hmc-mcp config show` could not answer
  either deployment the documentation
  recommends: it exits 1 with no `config.toml`, and it reads the invoking shell's environment
  rather than the served process's. The entry keeps the setting's own name and polarity —
  `authorize_power_operations: true` means the ownership guard is enforced — because
  `authorized: false` would read as "not authorized" for the permissive state, which is the
  misreading this change exists to end. With `HMC_HOST` set the report carries at most the
  `<default>` row, because every connection token collapses there at dispatch. A connection
  whose config cannot be built reports
  `authorize_power_operations: null` with `source: unresolved` and a closed `detail` — the
  exception class,
  plus the rejected field names for a `ValidationError`. The underlying message is withheld
  from the caller and logged instead: `ConfigError` names every `profiles` and `nicknames`
  key in `config.toml`, which is the connection inventory ADR 0038 refuses to disclose, and
  a `ValidationError` quotes the value it rejected. Beyond the connection names the policy
  already declares, the entries carry no host, user, or credential; ADR 0037's disclosure
  bullet is amended to record the narrowed claim. `describe()` takes the resolved guards as
  a fourth argument and stays a pure function of its arguments; `EffectivePermissions` is not
  a `hmc_mcp.api` export, so the facade manifest is unaffected.
- `install-attempted` audit record for a detached `installios` submission (#469, ADR 0102).
  `install_lpar_os` and `install_vios` submit an irreversible install against a partition's
  disks and detach; the path has no HMC job, no ADR 0011 ownership guard, and — for an
  `hmc_mcp.api` consumer — no dispatch-boundary `authorization` record. The two `INFO` lines
  it left instead went to the unconfigured `hmc_mcp.operations_install` logger, whose
  effective level is the root's `WARNING`, so they were dropped before formatting. That left a
  bare `hmc_mcp.api` consumer with no local trace at all, and a served deployment with only
  the `authorization` permit for the tool call — which names the tool but never the resolved
  system, partition, or log path, and which `--audit-level WARNING` drops. One `WARNING`
  record now goes to the
  reserved `hmc_mcp.audit` logger immediately **before** the submit — the ambiguous case,
  since the raised exception cannot say whether anything was submitted — carrying the resolved
  system and partition, the HMC-side `log_path`, the HMC, and the acting agent. The
  post-submit "Detached" line stays on the module logger; the PID it adds is already in the
  returned `InstallHandle`. No exported signature changes.

### Changed

- `create_user` and `modify_user` now expose the documented user-profile fields as
  explicit typed keyword parameters instead of accepting an untyped `**fields` bag.
- User deletion and remote-access MCP tools now call the client boundary directly;
  remote-access validation and document merging have one owner in the client layer.
- `metric_links` and `metric_data` require metric kind, time range, sample count, and
  managed-system scope as named arguments after the resource selector.
- Affinity assessment models, pure evaluation, and live orchestration now live together
  in `operations.affinity`; snapshot modules consume that boundary without a reverse
  dependency from operations into the snapshot package.

- `provision_lpar` now requires `partition_type` and all subsequent workflow
  controls as keyword arguments. The HMC client, system/name selectors, network,
  storage, and resource payload remain positional; boolean and policy controls can
  no longer be silently misbound by position.

- `update_console_software` and `hmc_update_console_software` no longer expose a
  `kind` selector whose `"upgrade"` branch always failed. The operation now models
  only the supported `UpdateManagementConsole` job; any future multi-job console
  upgrade will require a separately named workflow.

- `upload_iso` now documents and returns its sole successful status, `"uploaded"`;
  removed the always-`None` `existing_name` result field and the CLI's unreachable
  duplicate-content output branch. Name collisions continue to raise
  `FileExistsError` before transfer.

- Public VIOS and storage operations now use `*_name_or_uuid` selector names, place
  `system_name_or_uuid` before the resource selector, and require non-selector controls by
  keyword.
- LPAR ownership operations now live with the ownership policy in `lpar_ownership`, and
  post-activation affinity orchestration now lives with the shared affinity assessment models.
  The facade names and signatures are unchanged.
- `create_media_repository` no longer removes and recreates an existing virtual media
  repository. A repeated request for the same size returns the existing repository without a
  write; a different requested size raises an actionable conflict and leaves the repository
  untouched. Resizing or replacement requires a separately explicit destructive operation.
- `hmc_wait_for_job` and `hmc_get_job` now read through `operations_jobs` instead of calling
  `HMCClient` directly, so an MCP caller can tell a reaped job from a running one (#474, ADR 0093
  amendment). **Tool behaviour changes:** a job the HMC no longer has returns `found: false`
  (`hmc_wait_for_job`) or null (`hmc_get_job`) instead of raising `HMCError`, and polling stops on
  it rather than running to the deadline — read `found` first, and note that a caller which only
  caught `HMCError` for a vanished job now gets a successful result. `found: false` still carries
  `timed_out: true`, now returned immediately rather than after the deadline, so `timed_out` must
  not be read as "still running" without checking `found`. `hmc_wait_for_job`'s `job_href` also
  changes: a `job_href` you passed that resolved is echoed back verbatim, where the value
  previously came from the HMC's own SELF link. A 404 on the *first* read is
  reported straight through, so a momentary one now reads as `found: false` where it previously
  raised; only a disappearance after the job has been seen alive gets the confirming re-read. The
  output schema is
  unchanged: `found` was already a required property of the shared wait shape. Two smaller
  changes come with the shared operation. `timeout_seconds` is now a soft bound: a job that
  disappears after being seen alive is re-read once before being reported gone, and that read is
  owed past the deadline, so `hmc_wait_for_job` can return a whole `poll_interval` late —
  more than the deadline itself if `poll_interval` exceeds `timeout_seconds`. And a `job_uuid`
  that is empty, is a bare dot, or carries a path, query, fragment, percent, or interior
  whitespace character now raises `ValueError` before any request for the job is made — though
  after the session is opened, so a malformed handle still costs a logon and logoff;
  surrounding whitespace is still trimmed. That check applies **even when `job_href` is
  supplied**, where the client previously ignored `job_uuid` altogether — so an issue #95 caller
  that persisted only the submission link must now pass the identifier too. Every non-404 HMC
  failure still raises. Two consequences specific to `hmc_get_job`: null no longer separates a
  reaped job from an HMC that produced no entry, where the `HMCError` used to, and because that
  tool returns the HMC entry rather than the outcome, the `link` it carries can be one a read just
  proved dead — ADR 0093 clause 2's never-store-a-dead-link guarantee does not reach it. The
  `hmc jobs` CLI commands keep the previous behaviour; #526 owns that pass.
- `HMC_AGENT_ID` values containing double quotes or backslashes are rejected at config load
  instead of being passed through into SSH command construction (#386).
- `hmc_install_lpar_os` and `hmc_install_vios` now drive the HMC CLI
  `installios` command over SSH (submit-and-detach: they return the remote PID
  and log path instead of a job, and `hmc_get_job`/`hmc_wait_for_job` do not
  apply). The targeted `InstallLPAR`/`InstallVIOS` REST jobs do not exist on
  any surveyed HMC (ADR 0069). Parameter changes: `nim_ip` is removed (under
  CLI semantics the HMC itself serves the install image); a required
  `install_source` (`-d`) and `system_name_or_uuid` (`-s`) replace it, and a
  required `profile_name` (`-r`, default `"default"`) plus optional
  `mac_address` (`-m`) join; `wait`/`wait_timeout_seconds`/`poll_interval`/
  `hmc_timeout_minutes` are removed because there is no job to poll (#410,
  ADR 0070).
- A lower- or mixed-case `HMC_*` export now overrides a TOML profile's matching key, for
  every `HMCConfig` field (#531). `HMCConfig` leaves pydantic-settings' `case_sensitive`
  at its `False` default, so `hmc_host=…` always reached the `host` field; the profile
  loader's exact-case membership test did not agree, and handed the profile's value to the
  constructor as an init kwarg, which outranks every environment source. Three further
  hand-rolled `HMC_*` reads that mirror or report on that resolution move to the same
  case-insensitive rule: `build_config`'s `HMC_HOST` branch gate, the ADR 0038
  `connection_scope` mirror of that gate, and the #379 TLS audit record's `source` field,
  which named `explicit-argument` for a value only the environment had supplied. The
  `connection_scope` mirror is what makes leaving it exact-case a fail-open — the token
  would resolve to a profile key while the call reached the exported host — and pre-fix
  that divergence was already reachable, though only for a profile that omits `host`,
  since a profile carrying one handed it over as an init kwarg that outranked the variant.
  One reader inside `src/hmc_mcp` was left behind by that change — `audit.py` imports
  nothing from the package by design, so its `HMC_AGENT_ID` attribution read needed a
  case-fold of its own — and #543 below carries it, along with the `scripts/live_test_runner.py`
  sweep. Several casings of one
  variable fold to a single field, and the last one in the process environment wins —
  `env_var_value` resolves the tie the way pydantic-settings does, pinned by a test
  against `HMCConfig` rather than against a reading of the library. `HMC_PROFILE` is
  unchanged and still matched exactly on POSIX; it names no `HMCConfig` field and no
  settings loader reads it. **Upgrade note:** a deployment whose environment already
  carries a case-variant `HMC_*` name gets a different resolution after this change —
  the export now beats the profile's TOML key where it previously lost. Audit for them
  before upgrading (`env | grep -iE '^hmc_'`). Four things flip in the fail-open
  direction. A stale `hmc_verify_ssl=false` over a profile's `verify_ssl = true` is at
  least visible, as `client.py` emits `tls-verification-disabled` naming the environment.
  A stale `hmc_authorize_power_operations=false` over a profile's `true` leaves no runtime
  record at all — a guard that is off simply does not run — so `hmc-mcp config show` is
  the check for that one. The largest is on the access-policy surface: a stale
  `hmc_host` now collapses every connection token to `<default>`, so an ADR 0038 grant of
  `connections = ["<default>"]` permits calls it previously denied, issued against the
  exported host, while a grant naming a profile key now denies calls it previously
  allowed. The authorization records show it — `connection.resolved` reads `<default>`
  for a call that named a profile. Finally, a stale `hmc_agent_id` now beats a profile's
  `agent_id`, and that is the identity the ADR 0011 ownership guard compares against, so
  it changes which LPARs this process may mutate — `hmc_agent_id=team-b` over a profile's
  `agent_id = "team-a"` turns every denial of a `team-b`-stamped partition into a
  permit. The authorization records show this one as well: `audit.py` folds the casings the
  loader folds (#543), so a record's `attribution` names the identity the guard compared —
  the same one the LPAR ownership stamps and the `X-Audit-Memento` header carry.
- The authorization audit record's `attribution` reads `HMC_AGENT_ID` without regard to
  case (#543, ADR 0040). `HMC_AGENT_ID` is an `HMCConfig` field and `HMCConfig` matches its
  variables case-blind, so a `hmc_agent_id=alice` export stamped every LPAR the process
  created with the ADR 0011 ownership token for `alice` and sent `X-Audit-Memento:
  hmc-mcp:alice`, while every authorization record from that same process carried no
  claimant at all — the audit stream said nobody acted while the partitions said `alice`
  did. Several casings at once resolve to the last in the process environment's order, as
  they do for every other `HMC_*` variable. `audit.py` imports nothing from the package, so
  it carries its own copy of the fold rather than calling `config.env_var_value`; a test
  pins the two against each other.
- The live integration runner (`scripts/live_test_runner.py`) reads each `HMC_*` variable
  the way that variable's own reader reads it (#543). Five places predicted or overrode a
  config resolution exact-case. The credential and `HMC_SCHEMA_VERSION` pre-checks each refused to
  start on a case variant that would have connected, telling the operator to set a variable
  that was already set. The ISO allowlist merge dropped a case variant's entries, and it
  now also removes every other casing before writing the canonical name — assigning to an
  existing key updates it in place rather than moving it, so a variant would otherwise stay
  last in `os.environ` order and stay the one that reaches the field, leaving ADR 0050 to
  refuse every upload in the run while the printed banner said the host was permitted. The
  worst was the `.env` loader: `_bootstrap_config` documents an already-set `HMC_*` variable
  as priority 1 and `.env` as priority 3, but the exact-case membership test did not
  recognise an exported `hmc_host` as an already-set `HMC_HOST`, injected the canonical
  spelling, and — a newly created key landing last in `os.environ` order — let the
  committed `.env` outrank the export, so an operator who exported a lab host ran the
  destructive suite against the HMC `.env` named. The `config.toml` injection beside it
  took the same guard. Only a name `HMCConfig` resolves as one of its own fields is folded:
  `HMC_PROFILE` and a profile's `password_env` target carry the prefix but are read
  exact-case, and folding them would let a variant nothing reads suppress the `.env` line
  spelling them canonically.

### Removed

- `hmc_detach_optical_mapping` MCP tool and its `media.detach_mapping` operation name, plus the
  `detach_optical_mapping` operation behind them (#362). Both duplicated
  `hmc_unmount_optical_media` / `media.unmount` byte for byte. As this client implements optical
  mount, the media is referenced from inside the `VirtualSCSIMapping` and no unload-without-detach
  path has been identified on the surveyed firmware, so detaching the mapping and unmounting the
  image are one operation; #200 requirement 6 was amended to describe the one behavior that is
  currently buildable. **Upgrade note:** the server is
  fail-closed on unknown tool grants (ADR 0041), so an `access-policy.toml` that grants
  `hmc_detach_optical_mapping` now refuses to start — drop that entry or rename it to
  `hmc_unmount_optical_media`. The exposed tool count drops from 148 to 147.

### Documentation

- ADR 0096 records the decision behind `HMCConfig.from_mapping` and why documentation alone was
  not enough (#368). `AGENTS.md` no longer teaches `HMCConfig(_env_file=None)` as the
  credential-free idiom: that private pydantic-settings parameter suppresses a dotenv source
  rather than the environment, `HMCConfig` configures no dotenv source at all so it is entirely
  inert, and the guidance additionally told maintainers to delete the `monkeypatch.delenv` calls
  that were the only thing isolating those tests.
- ADR 0069 records the live-HMC survey finding that the HMC REST API does not advertise the
  `InstallLPAR`/`InstallVIOS` jobs at any surveyed firmware level (#381); the disposition of the
  affected tools is tracked in #410. No code change.
- ADR 0070 records the operator decision to bridge the install tools to the
  HMC CLI `installios` command, the grammar mapping with sources, the
  submit-and-detach semantics, and the injection-validation approach (#410).
- ADR 0071 records the decision to feed the bulk LPAR ownership read from the REST list
  endpoint per the #374 live-REST survey (descriptions inlined since schema V1_2_0,
  absent-element empty semantics), superseding the issue's N×SSH sketch, and the
  parse-failure honesty policy (#375). Also corrects the disproven "not exposed via REST"
  claim in `get_lpar_description`'s docstring.
- A served process now routes its own `hmc_mcp.*` log records through ADR 0043's bounded stderr
  sink (#534, ADR 0043 amendment). Only the reserved `hmc_mcp.audit` logger and the third-party
  set were on it, so a warning from any other module — `hmc_mcp.config`'s audit-memento override,
  `hmc_mcp.server_permissions`' unresolved-profile line — reached fd 2 through
  `logging.lastResort`: synchronous, unbounded, and unescaped. Those *log records* now carry
  the `hmc_mcp:` producer prefix and are drop-counted like every other line on the queue.
  `warnings.warn` is a separate mechanism and is not covered — the audit-memento override
  emits one of each, and its warning still goes straight to `sys.stderr` unmarked (#546, which
  also owns throttling that site's log record).
  **What an operator sees change:** the prefix, and — if you route `hmc_mcp.*` into your own
  logging — a second rendering, because `propagate` is deliberately left alone here, unlike on
  `hmc_mcp.audit`. Your handlers keep receiving these records exactly as before; the sink is an
  added destination, not a replacement. A handler you attach to `hmc_mcp` itself is left in
  place and takes the records instead of the sink, with the two constraints
  `docs/authorization-audit.md` states for a handler on `hmc_mcp.audit`: it must not write to
  `sys.stdout` under stdio, and it is called on the dispatch path, so one that blocks there
  blocks the call. Nothing changes for a library or CLI process, which installs no sink. The
  `WARNING` floor is unchanged at the shipped default; what grows is the sink's share of the
  1024-slot queue it shares with the audit trail, since these records did not enter it at all
  before.

### Facade manifest

- Added: `TLSVerificationDisabledWarning` lets reusable Python consumers filter
  the TLS-disabled logon warning without suppressing unrelated `UserWarning`s.
- Added: `ManagedSystemPatch` and `LpmMigrationRequest` replace recurring scalar
  option groups in `modify_system`, `validate_lpar_migration`, `migrate_lpar`,
  and `migrate_lpar_with_affinity_preflight`.
- Added: `VirtualNetworkResult` records the resolved managed-system UUID beside
  the HMC resource returned by `create_virtual_network`.
- Changed: `delete_adapter` returns the deleted adapter UUID instead of its
  parent LPAR UUID.
- Changed: SSH-only PCIe, vNIC inventory, and affinity read operations now
  accept `HMCConfig` directly instead of constructing an unused REST client.
- Changed: `ProvisionNetwork` is replaced by `ProvisionAdapters`, reflecting that
  the model configures both virtual Ethernet and the VIOS-side vSCSI adapter.
- Changed: `create_virtual_disk` now names its mebibyte value `capacity_mib`,
  matching the CLI and MCP tool boundary.
- Changed: `list_systems` accepts any exact HMC state string; removed the
  misleading finite `ManagedSystemState` facade type and tool-schema enum.
- Changed: `get_vios` now accepts the required VIOS selector first and makes its
  optional managed-system scope keyword-only.
- Added: `DecommissionBlastRadius` and `DecommissionAdapterRecord` type the
  stable inventory returned through `DecommissionResult.blast_radius`.
- Added: `list_clusters`, `list_shared_storage_pools`, and
  `get_shared_storage_pool` provide shared presentation-neutral cluster inventory
  operations for CLI, MCP, and reusable Python callers.
- Changed: PCIe and SR-IOV inventory operations now consistently name their
  managed-system selector `system_name_or_uuid`.
- Changed: `read_lpar_boot_order`, `set_lpar_boot_order`, and
  `clear_lpar_boot_order` now accept a system-scoped LPAR name or UUID.
- Changed: SSH affinity result types and workflows now live in
  `operations.ssh_affinity`; network inventory and vNIC mutation now live in
  `operations.vnic`.
- Changed: `get_vios`, `delete_vios`, `update_vios`, and `upgrade_vios` now place the
  optional managed-system selector before the VIOS selector, matching sibling VIOS
  operations and allowing update and upgrade names to be disambiguated.

- Added: `get_vios`, `list_vios`, and the latter's `PartitionState` selector
  type as the shared VIOS inventory boundary used by both presentation layers.
- Changed: `power_system` and `power_vios` now name their action flag
  `power_on`, matching `power_lpar`.
- Added: `get_system`, `list_systems`, and their `ManagedSystemState` selector type as
  the shared managed-system read boundary used by both presentation layers.
- Added: `CreateUserRequest` and `ModifyUserPatch`; `create_user` and `modify_user`
  now accept these typed profile payloads instead of duplicated wide parameter lists.
- Changed: SSH-backed network and affinity operations now consistently name their
  selectors `system_name_or_uuid` and `lpar_name_or_uuid`.
- Added: `resolve_and_authorize_lpar_mutation` and `resolve_and_authorize_lpar_names` after
  ownership authorization moved to the cross-cutting `operations.ownership` module.
- Added: `StorageMapResult`; `map_storage` now returns this concrete result instead of the
  mapped resource alone.
- Added: `upgrade_vios`, splitting VIOS upgrades from `update_vios`; `update_vios` now accepts
  only `VIOSUpdateSource` and has no `kind` mode selector.
- Removed: `add_vios_adapter`; use the explicit `add_vscsi_adapter` or `add_vfc_adapter`
  operation instead.
- Added: `add_vscsi_adapter` and `add_vfc_adapter`, replacing the boolean mode selector on
  `add_vios_adapter` with operation-specific names.
- Changed: `add_network_adapter`, `add_vios_adapter`, `delete_adapter`,
  `map_storage`, `attach_disk_to_lpar`, `mount_optical_media`,
  `unmount_optical_media`, `migrate_lpar`, `migrate_lpar_with_affinity_preflight`,
  `abort_lpar_migration`, `recover_lpar_migration`, and `remote_restart_lpar` now
  accept `ownership_override` and enforce the shared LPAR ownership guard before
  their first mutating submission. These changes move the frozen public signature
  digest.
- Added: `configure_lpar_msp`, `configure_lpar_processor_compatibility`, and
  `synchronize_lpar_profile`, together with their `ProcessorCompatibilityMode` input
  type; the guarded SSH-backed configuration operations now have supported reusable
  import paths.
- Changed: `detach_storage_mapping` now accepts a managed-system selector and
  `ownership_override`, resolves the mapping's client LPAR, and authorizes it before
  detachment. This moves the frozen public signature digest.
- Added: `create_lpar`, the complete reusable creation workflow that validates PCIe requests,
  creates and ownership-stamps the partition, applies assignments, and returns the existing
  `LparPcieWorkflowResult`. `modify_lpar` now also accepts an optional ordered `new_name` step;
  both changes move the frozen public signature digest.
- Changed: `modify_lpar` now orders its selectors as managed system then LPAR, matching the
  reusable LPAR operation contract. This moves the frozen public signature digest.
- Changed: `unassign_sriov_logical_port` now shares the assignment operation's positional
  identity prefix and accepts `profile_name` as a keyword-only control. This moves the frozen
  public signature digest.
- Added: `modify_lpar`, `modify_system`, `create_vios`,
  and `delete_vios`. These established cross-module operation seams are now named public
  package entry points instead of being imported as private helpers. Their transitive input
  types `MemoryMirroringMode`, `PowerOffPolicy`, and `PowerOnLparStartPolicy` are exported too;
  all eight names join the frozen public signature digest.
- Removed: `apply_validated_lpar_pcie_assignments`; reusable callers use
  `apply_lpar_pcie_assignments`, which enforces prevalidation before mutation.
- Added: `backup_vios`, `list_vios_backups`, and `restore_vios`, together with their
  `BackupType` and `RestoreBackupType` input types. The reusable operations now own VIOS backup
  validation, catalog parsing, selector resolution, and concrete HMC CLI command construction.
  All three operations now accept the facade's shared `HMCClient` and read its configuration at
  the SSH boundary; this moves the frozen public signature digest.
- Added: the presentation-neutral update operations `list_available_hmc_ptfs`,
  `update_console_software`, `update_firmware`, and `update_vios`, together with
  `ConsoleUpdateMediaType`, `ConsoleUpdateSource`, `IOAdapterUpdateModel`,
  `PlatformUpdateParameter`, `SriovAdapterUpdate`, `SystemFirmwareUpdateModel`,
  `VIOSPlatformUpdate`, `VIOSUpdateHMCSource`, `VIOSUpdateIBMWebsiteSource`,
  `VIOSUpdateNFSSource`, `VIOSUpdateSFTPSource`, `VIOSUpdateUSBSource`,
  `VIOSUpgradeHMCSource`, `VIOSUpgradeNFSSource`, `VIOSUpgradeSFTPSource`, and
  `VIOSUpgradeUSBSource`. MCP handlers now only manage the configured client boundary and
  delegate the complete workflow.
- Added: `configure_remote_access`, `create_user`, `delete_user`, and `modify_user`, plus their
  `AuthenticationType` input type. These presentation-neutral operations already back the user
  MCP tools and now satisfy ADR 0029's selection rule.
- Changed: `install_vios` now places `system_name_or_uuid` before `vios_name_or_uuid`,
  matching `install_lpar_os` and the other system-scoped partition operations. This moves the
  frozen public signature digest.
- Changed: `capture_lpar_snapshot` now reads the SSH configuration from its `HMCClient`
  instead of requiring callers to pass the same client's configuration separately. This removes
  the redundant `config` parameter and moves the frozen public signature digest.
- Changed: LPAR-targeting facade operations now consistently place the managed-system selector
  before the partition selector. This reorders `install_lpar_os`, `power_lpar`,
  `set_lpar_processors`, `set_lpar_memory`, and all four virtual-adapter operations; callers
  that want fleet discovery pass `None` explicitly for the system selector. Operation-specific
  controls remain after the two selectors and the frozen signature digest moves.
- Changed: public job operations now expose `wait`, `timeout_seconds`, and `poll_interval` as
  keyword-only controls with shared defaults of `false`, 300 seconds, and 5 seconds.
  `create_logical_unit` also defaults its keyword-only `cloned_from` selector to `None`.
  This aligns `create_logical_unit`, `delete_logical_unit`, and `deploy_partition_template`
  with the existing LPAR, LPM, system, VIOS, and persisted-job operations and moves the frozen
  signature digest.
- Changed: the first parameter of all SSH-backed public operations now consistently accepts
  `HMCClient` instead of `HMCConfig`: `capture_lpar_console`, the normalized PCIe inventory and
  SR-IOV mode operations, the FC/SEA/vNIC inventory operations, and the LPAR, system, and
  resource-group affinity read and planning operations. Implementations read `hmc.config` at
  the SSH boundary, matching every other supported operation and moving the frozen signature
  digest.
- Added: `validate_lpar_migration`, the standalone LPM validation operation already used by the
  MCP and CLI adapters. ADR 0029's selection rule requires it in the reusable facade.
- Added: `HMCIdentity`, replacing the inconsistently capitalized `HmcIdentity` export. This is a
  breaking public rename and moves the frozen signature digest; no compatibility alias remains.
- Added: `InstallHandle` (#468), the `TypedDict` `install_lpar_os` and `install_vios` now return
  in place of `dict[str, Any]`. Runtime behaviour and both MCP tool responses are unchanged — a
  `TypedDict` is a plain `dict` — but the five keys `system`, `partition`, `pid`, `log_path`, and
  `message` are now part of the frozen public signature digest, so renaming one is a manifest
  change rather than a silent break, and a downstream type checker resolves each value instead of
  `Any`. **Statically this narrows the return type, so a consumer that type-checks may go red on
  an upgrade**: holding the handle in a `dict[str, Any]`, passing it where `dict[str, Any]` is
  expected, or adding or deleting a key are all errors against a `TypedDict`. Annotate with
  `InstallHandle`, or with `Mapping[str, object]` where the consumer only reads.
- Added: `InstallRequest`, the shared source, network, and profile value object accepted by
  `install_lpar_os` and `install_vios`.
- Added: the exports below landed between the `[0.1.0]` entry's enumerated manifest and this
  cycle with no manifest bullet of their own (#479). Each is an entry in `hmc_mcp.api.__all__`,
  so each contributes to the frozen public signature digest. This records the manifest catching
  up, not new capability. Grouped by the change that added them:
  - `get_lpar_memopt_score`, `list_lpar_memopt_scores` (#252): memory-optimization scores read
    per LPAR over the SSH command surface.
  - `get_system_memopt_score`, `plan_lpar_memopt_scores`, `plan_system_memopt_score`, and the
    `MemoptLparSelector` model (#311): the same scores at managed-system scope, plus the
    planning variants that project a score without applying anything.
  - `MemoptResourceGroupSelector`, `ResourceGroupAffinityResult`,
    `list_resource_group_memopt_scores`, `plan_resource_group_memopt_scores` (#312): the
    resource-group scope of the same surface.
  - `LparSnapshot`, `SnapshotInspection`, `SnapshotValidationError`, `capture_lpar_snapshot`,
    `inspect_lpar_snapshot`, `validate_lpar_snapshot` (#314): portable LPAR snapshot capture,
    plus the two operations that parse a captured snapshot document locally without reaching
    the HMC. `LparSnapshot` is the model the #482 bullet below describes the fields of.
  - `MinimumAffinityPolicy`, `MinimumAffinityPolicyResult`, `get_minimum_affinity_policy`,
    `set_minimum_affinity_policy` (#315): read and write of the minimum affinity policy.
  - `AffinityAssessmentInput`, `AffinityAssessmentResult`, `AffinityEvidence`,
    `CapturedPolicyState`, `PolicyState`, `assess_snapshot_affinity` (#317): affinity
    assessment over a captured snapshot and the evidence and policy-state models it reports.
  - `ProvisionAffinityAssessment` (#318): the assessment `provision_lpar` reports from the
    activation leg of the workflow.
  - `PostActivationAffinityAssessment`: the typed result returned by
    `assess_post_activation_affinity`.
  - `CapacitySummary`, `LparSummary`, `SystemSummary`: typed results for the public
    capacity and inventory-summary operations.
  - `LpmAffinityMigrationResult`, `LpmAffinityPreflightOutcome`, `LpmAffinityPreflightRequest`,
    `migrate_lpar_with_affinity_preflight`, `run_lpm_affinity_preflight` (#320): the
    affinity-aware LPM preflight and the migration that runs it first.
- Added: `get_job`, `wait_for_job`, `JobOutcome` (#364, ADR 0093); this moves the frozen public
  signature digest.
- Added: `set_lpar_ownership_description`.
- Added: `capture_lpar_console`, `ConsoleCapture`, `ConsoleHeldError` (#385,
  ADR 0072); this moves the frozen public signature digest.
- Added: `list_lpar_ownership`.
- Added: `list_optical_mappings`, `mount_optical_media`, `unmount_optical_media`,
  `assess_post_activation_affinity` (#363); this moves the frozen public signature digest. All four
  were already selected by ADR 0029's rule and were absent from the manifest by omission, not by
  decision, so this records the manifest catching up rather than a new capability.
- Added: `set_lpar_processors`, `set_lpar_memory` (#365, ADR 0094); this moves the frozen public
  signature digest. Both take `system_name_or_uuid` and `ownership_override` as keyword-only
  parameters; the managed-system selector stays optional per ADR 0063, so a `hmc_mcp.api` caller
  may omit it and have the owning system derived.
- Added: `install_lpar_os`, `install_vios` (#366); this moves the frozen public signature digest.
  Their `dict[str, Any]` return is **not** one of ADR 0029's opaque HMC resource payloads — the
  package composes all five keys itself, and no firmware level can add or remove one. The keys
  `system`, `partition`, `pid`, `log_path` and `message` are pinned by a contract test
  (`tests/unit/test_install_operations.py`). Changing one is a consumer-visible break even though
  it moves neither the manifest nor the signature digest, so it needs the same minor release.
  Typing the shape so the digest can see it is tracked by #468.
- Added: `PcmResource` and `RemoteRestartOperation` (#446); this moves the frozen public
  signature digest. `PcmResource` is the frozen dataclass `resolve_pcm_resource` returns;
  `RemoteRestartOperation` is the literal alias `remote_restart_lpar` takes, whose alternatives
  are `validate`, `recover`, `restart`, `cleanup`, and `cancel`. Both were already selected by
  ADR 0029's type clause and absent from the manifest by omission, so a consumer met them
  through a supported call with no supported import path to name them — visible to a downstream
  type-checker since the PEP 561 marker shipped (#367). This records the manifest catching up,
  not a new capability.
- Added: nineteen types ADR 0029's type clause now reaches through the fields of an exported
  model (#482); this moves the frozen public signature digest. Twelve `hmc_mcp.snapshot` models
  behind `LparSnapshot` — `HMCIdentity`, `LparIdentity`,
  `MemoryProjection`, `NativeProfile`, `NormalizedConfiguration`, `ObservationEnvelope`,
  `ProcessorProjection`, `SnapshotCapability`, `SnapshotConfiguration`, `SnapshotObservations`,
  `SnapshotSource`, `SystemIdentity` — and seven literal aliases: `AffinityClassification`
  (`regression`, `optimization-opportunity`, `policy-violation`, `unsupported-data`, `none`),
  `CapabilityState` (`available`, `capability-unavailable`), `Keylock` (`normal`, `manual`,
  `auto`), `OsType` (`aix`, `linux`, `ibmi`), `ResourceKind` (`dedicated_slot`, `sriov_adapter`,
  `sriov_physical_port`, `sriov_logical_port`), `SharingMode` (`capped`, `uncapped`,
  `keep_idle_procs`, `share_idle_procs`, `share_idle_procs_active`, `share_idle_procs_always`),
  and `StopReason` (`duration`, `max_bytes`, `idle`, `remote-close`, `error`). ADR 0029's
  Decision already called the fields of an exported model supported, while its walk stopped at
  the types an operation *names*; a consumer therefore met `LparSnapshot.configuration` as
  `hmc_mcp.snapshot.SnapshotConfiguration` with no supported import path to name it. This
  records the manifest catching up, not a new capability. No type moved modules and no value set
  changed.
- Fixed: `set_sriov_adapter_mode` appeared twice in `hmc_mcp.api.__all__` (#446). The name is
  imported once, so the duplicate was inert at runtime, but ADR 0029 calls `__all__` an
  exhaustive manifest and a repeated entry makes it malformed. The export set is unchanged.
- Removed: none.
- Renamed: none.
- Exported signature changes: `power_lpar` gained `ownership_override: bool = False`, and
  `HMCConfig` gained the `authorize_power_operations: bool = False` field (#371, ADR 0092 §4).
  A pydantic model's `__init__` signature is derived from its fields, so the setting moves the
  frozen public signature digest even though no operation's parameters changed by it; the
  `power_lpar` parameter moves it again. No export was added, removed, or renamed.
- Exported model/literal changes: `HMCConfig` gained the `from_mapping(values)` classmethod
  (#368, ADR 0096). ADR 0029 declares "the fields and constructor of an exported package-owned
  model" supported, so a classmethod extends that model's supported surface and this is a minor
  release under the `0.x` rule. On its own it does **not** move the frozen public signature
  digest: that digest hashes each export's `inspect.signature`, which for a pydantic model is
  the field-derived `__init__`, and a method changes no field. This change adds no
  `api.__all__` entry — the digest move recorded above is `get_job` / `wait_for_job` /
  `JobOutcome`'s (#364), not this one's.
- Exported model/literal changes: `LparCreation` gained the
  `stamp_policy: Literal["best-effort", "required"]` field (defaults to `"best-effort"`), which
  moves the frozen public signature digest. The audit event vocabulary gained the
  `"tls-verification-disabled"` literal on `hmc_mcp.audit.Event`; that module is not part of the
  `hmc_mcp.api` facade, so it does not expand the manifest itself but is recorded here because it
  widens a public literal vocabulary.
- Exported model/literal changes: the audit event vocabulary gained the `"install-attempted"`
  literal on `hmc_mcp.audit.Event` (#469, ADR 0102). Recorded here on the same terms as
  `"tls-verification-disabled"` above — `hmc_mcp.audit` is not part of the `hmc_mcp.api` facade,
  so the manifest and the frozen public signature digest are unmoved, but the literal vocabulary
  a consumer reading the audit stream matches against is wider.
- Unchanged otherwise: #410 rebuilt `hmc_install_lpar_os` / `hmc_install_vios`
  on the HMC CLI `installios` bridge (ADR 0070). These are MCP tools, not
  `hmc_mcp.api` exports; their parameter changes do not move the frozen
  manifest or its signature digest — the operations behind them that #366 later
  exported are separate names with their own signatures. #362 likewise removed the
  `hmc_detach_optical_mapping` MCP tool and the `detach_optical_mapping`
  operation; neither was exported from `hmc_mcp.api`, so the manifest and the
  frozen signature digest are unmoved and no minor release is gated on it.

## [0.1.0] - 2026-08-22

Initial supported Python API surface per ADR 0029: the reusable facade at `hmc_mcp.api`, its
frozen export set (`tests/unit/test_public_api.py::test_public_api_manifest_is_frozen`) and its
frozen signature digest
(`tests/unit/test_public_api.py::test_public_operations_are_async_and_signatures_are_frozen`).

### Added

- MCP server and CLI for the IBM HMC REST API, the `hmc_mcp.api` supported facade, and the
  ownership-stamp workflow operations.

### Facade manifest

Initial manifest of `hmc_mcp.api.__all__` (127 exports). This enumeration is the boundary the
`[Unreleased]` manifest's delta is derived against, so it names the 0.1.0 export set and nothing
added afterwards; every later addition is recorded above.

`AdapterResult`, `AdapterType`, `AssignmentResult`, `WorkflowStep`, `AttachDiskResult`,
`BootDeviceSelector`, `ConfigError`, `DecommissionResult`, `DedicatedPcieAssignment`,
`DedicatedSlot`, `DeviceType`, `FleetHealthResult`, `HMCCLIError`, `HMCClient`, `HMCConfig`,
`HMCError`, `HMCTransportError`, `InventoryResult`, `InventorySelector`, `LparCreation`,
`LparCreationResult`, `LparPcieAssignments`, `LparPcieWorkflowResult`, `LparPowerResult`,
`LparResources`, `LpmResult`, `LuType`, `MetricKind`, `PartitionType`,
`PcieAssignmentUnavailableError`, `PcmCategory`, `ProvisionNetwork`, `ProvisionResult`,
`ProvisionStorage`, `SriovAdapter`, `SriovLogicalPort`, `SriovLogicalPortAssignment`,
`SriovLogicalPortCapabilityError`, `SriovLogicalPortChangeResult`,
`SriovLogicalPortPartialError`, `SriovLogicalPortSnapshot`, `SriovMode`, `SriovPhysicalPort`,
`StorageKind`, `VnicAssignment`, `VnicBackingSelector`, `VnicBackingSnapshot`,
`VnicCapabilityError`, `VnicChangeResult`, `VnicPartialError`, `VnicSnapshot`,
`abort_lpar_migration`, `add_network_adapter`, `add_vios_adapter`, `add_vnic`,
`apply_lpar_pcie_assignments`, `assign_dedicated_pcie_slot`, `assign_sriov_logical_port`,
`attach_disk_to_lpar`, `authorize_decommission_lpar_ownership_snapshot`,
`authorize_lpar_mutation`, `capacity_report`, `clear_lpar_boot_order`, `create_and_stamp_lpar`,
`create_logical_unit`, `create_media_repository`, `create_optical_media`,
`create_virtual_disk`, `create_virtual_network`, `create_volume_group`, `decommission_lpar`,
`delete_adapter`, `delete_logical_unit`, `delete_lpar`, `delete_media_repository`,
`delete_optical_media`, `delete_virtual_disk`, `delete_virtual_network`,
`deploy_partition_template`, `detach_storage_mapping`, `find_placement`, `fleet_health`,
`get_media_repository`, `get_partition_template`, `get_pcm_preferences`, `list_adapters`,
`list_dedicated_slots`, `list_fc_ports`, `list_network_bridges`, `list_optical_media`,
`list_partition_templates`, `list_sea_adapters`, `list_sriov_adapters`,
`list_sriov_logical_ports`, `list_sriov_physical_ports`, `list_storage_mappings`,
`list_virtual_networks`, `list_virtual_switches`, `list_vnics`, `list_volume_groups`,
`load_profile`, `lpar_summary`, `map_storage`, `metric_data`, `metric_links`, `migrate_lpar`,
`power_lpar`, `power_system`, `power_vios`, `prevalidate_lpar_pcie_assignments`,
`provision_lpar`, `read_lpar_boot_order`, `recover_lpar_migration`, `remote_restart_lpar`,
`remove_vnic`, `rename_lpar`, `resolve_lpar_ownership_names`, `resolve_pcm_resource`,
`set_lpar_boot_order`, `set_pcm_preferences`, `set_sriov_adapter_mode`,
`stamp_created_lpar_ownership`, `system_summary`, `unassign_dedicated_pcie_slot`,
`unassign_sriov_logical_port`, `upload_iso`

[unreleased]: https://github.com/randomparity/hmc-mcp/compare/0.1.0...HEAD
[0.1.0]: https://github.com/randomparity/hmc-mcp/releases/tag/v0.1.0
