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

### Added

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
  case-insensitively while the profile loader drops only the exact upper-case spelling
  (#531), so a variant loses to a profile on one resolution path and wins on the other, and
  nothing in the server can tell which happened. `hmc-mcp config show` could not answer
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

### Changed


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

### Facade manifest

- Added: `InstallHandle` (#468), the `TypedDict` `install_lpar_os` and `install_vios` now return
  in place of `dict[str, Any]`. Runtime behaviour and both MCP tool responses are unchanged — a
  `TypedDict` is a plain `dict` — but the five keys `system`, `partition`, `pid`, `log_path`, and
  `message` are now part of the frozen public signature digest, so renaming one is a manifest
  change rather than a silent break, and a downstream type checker resolves each value instead of
  `Any`. **Statically this narrows the return type, so a consumer that type-checks may go red on
  an upgrade**: holding the handle in a `dict[str, Any]`, passing it where `dict[str, Any]` is
  expected, or adding or deleting a key are all errors against a `TypedDict`. Annotate with
  `InstallHandle`, or with `Mapping[str, object]` where the consumer only reads.
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
  behind `LparSnapshot` — `HmcIdentity`, `LparIdentity`,
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

`AdapterResult`, `AdapterType`, `AssignmentResult`, `AssignmentStep`, `AttachDiskResult`,
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
