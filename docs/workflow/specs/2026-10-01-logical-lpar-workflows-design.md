# Logical LPAR workflows and installation handoff contract

**Issue:** #1216 (H1 of epic #1215)
**Decisions:** [ADR 0189](../../adr/0189-logical-lpar-catalog-and-delegated-authorization.md),
[0190](../../adr/0190-durable-logical-operation-store.md),
[0191](../../adr/0191-installation-ends-at-boot-started.md),
[0192](../../adr/0192-decommission-owned-storage-cleanup.md),
[0193](../../adr/0193-host-handoff-external-management-hold.md),
[0194](../../adr/0194-initial-installation-profiles-and-native-envelope.md)
**Branch:** `feat/logical-lpar-contract-1216` from `main`
**Guardrails:** `just verify`; `uv run --no-sync prek run --all-files`

## Purpose and boundary

This is the contract the epic's children implement: #1218–#1232 here, and iso-chain-loader#23–#25.
It changes no code. Where a value can only come from native evidence, this spec names it as
**undecided** and says who decides it. Nothing undecided may be reported as implemented.

hmcpctl's provisioning responsibility ends at an HMC-observable progress code showing that the
installer boot has started (ADR 0191). Everything after that point is the user's: install
success, disk boot, guest identity and SSH. That boundary was the operator's decision on
2026-10-01.

The existing `hmc_provision_lpar` and `hmc_decommission_lpar` gain a required `request_id`. This is
a pre-release replacement with no compatibility path, so #1225 and #1229 carry the CHANGELOG entry.

Not in this design (the approved exclusions):

- implementation of the eleven tools;
- iso-chain-loader changes;
- #637's profile primitives;
- guest SSH and readiness;
- any live run;
- kdive-side code;
- any change to the six-name `hmcpctl.api` facade;
- a generic workflow language.

## Primary tools

All eleven are MCP tools. Each mutating tool takes `request_id` and the ADR 0190 identity
fields. Every result is a typed record, and every collection in a result carries an explicit
`limit` and `truncated` flag. Effects are those registered under ADR 0189.

| Tool | Effect | Inputs (beyond `profile`) | Result |
| --- | --- | --- | --- |
| `hmc_inventory` | read | `systems?` (≤ 16 selectors), `lpar_state?`, `owner?`, `limit` 1–200 (default 50), `cursor?` | systems and partitions with scoped ids, state, capacity, owner; `sources` with per-source `ok`/`unavailable`/`denied` |
| `hmc_plan_lpar` | read | `desired` (below), `system?` *or* `placement` constraints | `plan_digest`, resolved targets, `blockers[]`, `intended_changes[]`, `unverified[]` |
| `hmc_provision_lpar` | mutate | `request_id`, `desired`, `system`, `expected_plan_digest?`, `install?`, `power_on`, `dry_run`, `wait_seconds`, `resume` | operation result (below) |
| `hmc_reconfigure_lpar` | destructive | `request_id`, `lpar`, `patch` (below), `allow_disruption`, `wait_seconds`, `resume` | operation result plus `changes[]`, each `live` / `profile` / `pending_activation` |
| `hmc_decommission_lpar` | destructive | existing ADR 0027 inputs, plus `request_id`, `storage_cleanup` (`retain` default / `delete_owned`), `resume` | ADR 0027 result plus `storage`: `deleted[]`, `retained[]` with reasons, `pending[]` |
| `hmc_power_lpar` | destructive | `request_id`, `lpar`, `action` (`start` / `stop` / `restart`), `mode` (`graceful` default / `immediate`), `wait_seconds`, `resume` | operation result plus `already_in_state`, `observed_state` |
| `hmc_inspect_lpar` | read | `lpar`, `include` ⊆ {`resources`, `rmc`, `profile_drift`, `refcodes`} | state, RMC, profile drift, ≤ 20 refcodes, `next_actions[]` (tool names only) |
| `hmc_prepare_host_handoff` | mutate | `action` (`prepare` / `release`), `lpar`, `hold`, `consumer`, `hold_id` | handoff document (below) or release confirmation |
| `hmc_operation_status` | read | `operation_id` *or* `request_id`; or a listing with `state?` and `limit` 1–50 | operation records with ≤ 200 events per page |
| `hmc_search_tools` | read | `query` (≤ 200 characters) *or* `name`, `limit` 1–20 | names, one-line summaries, effect, maturity; the full input schema only for an exact `name` |
| `hmc_invoke_tool` | destructive | `name`, `arguments` (≤ 64 KiB) | the invoked tool's own result, unchanged |

Selectors are the existing `system_name_or_uuid` / `lpar_name_or_uuid` pairs. Names are
resolved within one system; ADR 0015's UUID pass-through does not apply.

`desired` contains:

- `name`;
- `processors`: `mode`, plus `entitled` / `virtual` with min/desired/max;
- `memory_mib`: min/desired/max;
- `networks[]`: `vlan_id`;
- `root_disk`: `size_gib`, plus an explicit `vios` and `volume_group` or a placement hint.

When more than one VIOS or volume group qualifies and none was named, the result is a blocker.
The first match is never chosen.

`patch` covers `name`, `processors` and `memory_mib`. Any omitted field is preserved. #1228
extends `patch`, under the same rules, with networks, added disks, `installer_media: detach` and
`boot: root_disk` — the post-install steps ADR 0191 leaves to the caller.

A tool whose operation writes a VIOS or volume-group document also takes
`exclusive_writer_window` (see *Ownership and shared VIOS state*).

`install` contains:

- `profile`: `ubuntu-26.04.1` or `rocky-9.8`;
- `network`: `address` as CIDR, `routes` (1–16, including a default route), `dns` (≤ 3);
- `ssh_authorized_keys`: public keys, 1–16;
- `login_user`;
- `media`: either `{mode: prepared, url, sha256, size}` or `{mode: built}`.

The operation result carries:

- `operation_id`, `request_id`, `state`;
- `phase`, from {`validating`, `creating`, `configured`, `media_bound`, `installer_booting`};
- `outcome`, from {`configured`, `boot_started`, `needs_attention`, `failed`}, or `null` while
  running;
- `effects[]`, each with `kind`, `target`, `status` (`intended` / `applied` / `not_applied` /
  `uncertain`) and created identity;
- `steps` (`WorkflowStep`, kept for ADR 0005 and ADR 0027 callers);
- `warnings[]` and `next_actions[]`.

`state` is one of `running`, `interrupted`, `terminal`.

## Delegated authorization

Under ADR 0189, a logical action runs only when the policy permits every tool in its row and
the dispatch authorizer admits the call, as that tool, for each resolved target.

| Action | Delegated tools |
| --- | --- |
| plan; inventory | `hmc_list_systems`, `hmc_list_lpars`, `hmc_capacity_report`, `hmc_list_vios`, `hmc_get_vios_storage_detail`, `hmc_list_lpar_ownership` |
| provision (no install) | `hmc_create_lpar`, `hmc_add_network_adapter`, `hmc_add_vscsi_adapter`, `hmc_create_virtual_disk`, `hmc_map_storage_to_lpar`, #637's profile write |
| provision `install` | the row above, plus `hmc_upload_iso`, `hmc_mount_optical_media`, `hmc_set_lpar_boot_order`, `hmc_power_on_lpar`, `hmc_read_lpar_refcodes` |
| `power_on=true` | adds `hmc_power_on_lpar` |
| reconfigure | `hmc_modify_lpar`, `hmc_dlpar_proc`, `hmc_dlpar_mem`, #637's profile write; #1228 adds `hmc_unmount_optical_media`, `hmc_set_lpar_boot_order` and its attach tools |
| power `start` | `hmc_power_on_lpar` |
| power `stop` / `restart` | `hmc_power_off_lpar` |
| decommission `retain` | `hmc_decommission_lpar`'s existing set |
| decommission `delete_owned` | adds `hmc_detach_storage_mapping`, `hmc_unmount_optical_media`, `hmc_delete_optical_media`, `hmc_delete_virtual_disk` |
| inspect | `hmc_get_lpar`, `hmc_get_lpar_state`, `hmc_read_lpar_refcodes` |
| handoff `prepare` | `hmc_get_lpar`, `hmc_list_lpar_ownership` |
| handoff `release` | none beyond the tool itself |
| operation status | none beyond the tool itself; it lists only records with the caller's connection and agent id |

The name of #637's profile-write tool is **undecided**, and #637 sets it. #1225 and #1226
stay blocked on it.

`hmc_power_lpar` never reaches `dumprestart`; that stays `hmc_dump_restart_lpar` (ADR 0188).
`mode=immediate` must be stated explicitly. A timeout never escalates to it.

## Operations, resume and restart

ADR 0190 governs. The contract each implementer must hold is:

- **Before any HMC write**, the store records the intent. A crash between intent and outcome
  leaves the effect `uncertain`.
- **Resume** classifies each `uncertain` effect by a live read keyed on recorded identity, and
  continues only when every effect classifies:
  - created partition: UUID, then name within the system;
  - adapter: UUID;
  - disk: VIOS, volume group and name;
  - media: repository name.

  A resource found under a different owner stamp is `needs_attention`, not adopted.
- **Restart power-cycle.** A `restart` resumed after its power-off was applied does not
  power off again. It reads state and continues from the next effect.
- **Installer power-on** is applied at most once per operation. A resume after `boot_started`
  returns the recorded outcome.
- `hmc_operation_status` and resume on another connection or agent id return "not found",
  the same answer as for an operation that does not exist.

## Persistent and live changes

- **Creation** writes the profile through #637's primitive, then reads it back before
  reporting `configured`. Proof (#1225) is that the configuration survives profile activation.
- **Reconfigure** computes every change before applying any:
  - a change applies `live` only when the partition is Running, RMC is active, and the value is
    within the current min/max;
  - otherwise the change is written to the profile and reported `pending_activation`;
  - a min/max change, or a processor-mode change, is always `pending_activation`, and the
    processor mode is never changed implicitly.
- **No reboot.** hmcpctl never reboots to apply a change. With `allow_disruption=false` (the
  default), a `pending_activation` change is still written to the profile and reported. The
  preview names the activation the change needs.

## Ownership and shared VIOS state

- Provision stamps ownership (ADR 0011) as a required step. Failing to stamp is `failed`, not
  a warning. Created disks and media are recorded in the store.
- VIOS and volume-group writes keep ADR 0169 and ADR 0171's read-modify-write with `If-Match`.
  Whether the HMC enforces `If-Match` is unconfirmed (#879). Until #879 confirms it, any
  operation that writes a VIOS or volume-group document requires `exclusive_writer_window=true`.
  That flag is the caller's assertion that an operator has paused other writers on those VIOS.
  Without it, planning reports a blocker. A 412 response is `failed` and is not retried.
- Decommission storage cleanup follows ADR 0192.

## Installation media and boot

ADR 0191 governs. The order of steps for `install` is:

1. create the partition and adapters;
2. record the client network adapter's actual MAC;
3. create and map the root disk;
4. bind media:
   - *prepared*: check the producer result against the request;
   - *built*: invoke the configured build entry with the manifest, then check its result;
5. upload with a digest check;
6. mount;
7. set the boot order to installer media first;
8. power on;
9. poll refcodes until `boot_started` or `wait_seconds` runs out, then report `needs_attention`
   with the observed codes.

The media name is `hmcpctl_<first 12 hex of operation_id>.iso`, which fits upload's
`[A-Za-z0-9_.]{1,79}` rule.

**Producer result fields iso-chain-loader#23 must emit:**

- `format` (`iso-chain-media-v1`);
- `iso_sha256` and `iso_size`;
- `manifest_sha256`;
- `distribution`, `release` and `architecture`;
- `mac`;
- `network`;
- `operation_binding` (the `operation_id`);
- `url` (built mode only).

The installer must refuse to install unless exactly one non-optical disk is present. Caller SSH
keys and the login user pass through to the producer untouched.

**Undecided:** the boot-started reference-code set. #1230 fills it from native evidence. Until
then `boot_started` is never reported.

**Not verified by hmcpctl:**

- that the VIOS copy matches the uploaded image, since there is no readback;
- that the guest can reach the installer source;
- every guest fact.

Results list each of these under `unverified`.

## Host handoff

ADR 0193 governs the hold. The `prepare` document has three groups of facts:

- `observed`: system and partition identity, state, resources, adapters, MACs, disks and
  mounted media, each from a read made during this call;
- `declared`: install profile, address, routes, login user and key fingerprints, taken from
  the operation record;
- `unverified`: OS, kernel, bootloader, kdump/fadump readiness, SSH reachability and host
  identity, each with the statement that kdive's doctor/adopt checks it.

It also carries the hold, if any, and its scope statement. It carries connection and
credential *references* (profile name, agent id), never secrets. hmcpctl does not register
anything with kdive or allocate anything in it, and `release` never deletes the partition.

## Native envelope and proof

ADR 0194 governs. Proof is two separately authorized arms, `native-ubuntu-boot-started` and
`native-rocky-boot-started`. Each runs through `scripts/live_test_preflight.py` and
`scripts/live_test_recovery.py` against the deployed commit, and its evidence comes from
`scripts/live_test_evidence.py`. CI, QEMU and skipped arms do not count. This issue authorizes
no live run.

## Failure model

**Actors and deployments**

- An MCP client agent on a server deployment that has a served access policy and one store.
- An operator with a CLI on the same host.
- Other HMC writers (GUI, other deployments, scripts), whose writes hmcpctl cannot see in advance.
- External consumers: kdive.

**Invariants and assets at stake**

- Unrelated VIOS and volume-group state.
- Existing guest disks.
- Shared media and physical volumes.
- Partitions owned by others.
- The meaning of `boot_started` and of handoff `observed` facts.
- Policy-withheld authority.
- The operation record.

**Accepted failure classes**

- *Writes by other HMC writers during an operation.* Accepted because this is not a fence;
  ADR 0011 and ADR 0190 state it, and the `exclusive_writer_window` bounds VIOS writes.
- *Store loss.* Provenance is lost. Accepted because ADR 0192 turns this into retained, never
  deleted, storage.
- *Two clients sharing one agent id share operations.* Accepted because MCP carries no
  principal (ADR 0190).
- *A Running guest whose install failed.* Out of scope by operator decision (ADR 0191).

**Covered elsewhere**

- Profile primitives: #637.
- `If-Match` enforcement: #879.
- Media correctness and installer behavior: iso-chain-loader#23–#25, #6, #8.
- kdive adoption checks: kdive.
- Console leasing: ADR 0172 and kdive.

## Threat model

**Boundaries added**

1. Invoke gateway → any registered tool.
2. Build entry → host process execution.
3. Producer result file → binding decisions.
4. Store file → authorization of resume, cleanup and holds.

**Boundaries widened**

1. Decommission → storage deletion.
2. The upload URL now carries a binding digest.

**Actors**

- An authenticated MCP client holding a narrower grant than the server.
- A local user who can write the state directory or the build output.
- A network peer that serves the media URL.
- A misbehaving producer.

**Controls**

1. Per-tool `authorized()` re-entry; refusal of gateway and `arbitrary-command` tools; an
   identical denial for unknown and withheld names; arguments ≤ 64 KiB.
2. A configuration-only executable and argument template; only the manifest path varies; no
   shell.
3. A strict versioned schema (unknown or duplicate keys refused); every field compared with
   recorded effects; the result file ≤ 64 KiB.
4. A `0700` directory and `0600` file, checked at open; refusal when not private.

For the widened boundaries:

1. Ownership requires store provenance plus a live single-reference check, and incomplete
   inventory retains.
2. The allowlist, no redirects, and a digest compare before upload.

Errors name the failed check without echoing payloads.

**Out of scope**

- Fencing against independent HMC writers.
- A compromised HMC or VIOS.
- Guest-side compromise.
- Secrecy of public SSH keys.

## Success

Each #1216 completion criterion maps to one place:

- tool schemas and bounds: *Primary tools*;
- identity, resume, retention and restart: *Operations, resume and restart*, ADR 0190;
- authorization, including search and invoke: *Delegated authorization*, ADR 0189;
- ownership, cleanup, persistent versus live: *Persistent and live changes*, *Ownership and
  shared VIOS state*, ADR 0192;
- media binding, builder invocation and the installation boundary: *Installation media and
  boot*, ADR 0191;
- hold and release: *Host handoff*, ADR 0193;
- releases, native envelope and proof arms: *Native envelope and proof*, ADR 0194;
- reconciliation with accepted ADRs: the Status sections of ADR 0189 (ADR 0012) and ADR 0192
  (ADR 0027). ADR 0005 is extended, not changed.

Each value this spec marks **undecided** names the issue that decides it.

## Follow-ups this decision creates

Listed for the operator; this PR does not edit them.

| Item | Conflict |
| --- | --- |
| #1222 | Guest SSH readiness is obsolete under ADR 0191; it should be closed or rescoped. |
| #1230 | Acceptance becomes `boot_started` per distro, plus filling the boot-started code set. |
| Epic #1215 | Requirement 7 and the SSH success criteria conflict with ADR 0191. |
| iso-chain-loader#24, #25 | The "host-identity handoff" wording conflicts. They should inject caller keys only and refuse unless exactly one disk is present. |
| iso-chain-loader#23 | Must emit the producer result fields above. |
| #1228 | Gains the `installer_media: detach` and `boot: root_disk` patch fields. |
