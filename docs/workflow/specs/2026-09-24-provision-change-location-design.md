# Provision reports its adapter/mapping change location

## Problem

`lpars provision` / `hmc_provision_lpar` adds a network adapter, a vSCSI adapter, and a
storage mapping directly through `HMCClient`, bypassing the standalone
`add_network_adapter`/`add_vscsi_adapter`/`map_storage_to_lpar` operations that, since
#981/#1045, read the partition's `CurrentProfileSync` and report a `change_location`.
Provision's result carries none of that, so an operator cannot tell whether a later
`power-on --partition-profile` would keep these adapters (#1056).

## Scope

Add `change_location: ChangeLocation | None` to `ProvisionResult`. After the network leg
succeeds, call `read_change_location(hmc, lpar_uuid)` (`profile_sync.py`) once — not per
adapter/mapping step, which would repeat the read the standalone operations already do once
each. Thread the value through every later return path (storage/assignment/power/affinity
failure, and the final success) so it is reported whenever the network adapter landed. A
failed read is advisory: caught and turned into a `warnings` entry, matching
`profile_adapter_warnings`'s precedent, never failing provisioning that already wrote
adapters. `lpars provision`'s CLI renders `result.change_location.summary()` exactly as
`adapters add-network` does; `hmc_provision_lpar`'s docstring documents the field. No change
to `_power_on`, `attach_disk_to_lpar`/`AttachDiskResult`, or `read_change_location` itself.

### Failure model

- Actors and deployments: an authenticated hmcpctl operator via CLI or MCP client, against
  one HMC — the same actor every other provision call already assumes.
- Invariants and assets at stake: none beyond provision's existing ones; `change_location` is
  a read-only, additive, best-effort report and authorizes no HMC write.
- Accepted failure classes: a failed `read_change_location` call (transient network or
  session error) is accepted — caught, reported as a warning, `change_location` stays `None`;
  provisioning that already succeeded is not undone by an information read failing after it.
- Covered elsewhere: `read_change_location`'s classification of `CurrentProfileSync` and its
  own error surface (`profile_sync.py`) are unchanged, out of scope here.

## Success

- `ProvisionResult.change_location` is populated whenever the network step ran and the read
  succeeded; `None` when no adapter/mapping step ran, or the read failed (named in `warnings`).
- `lpars provision`'s non-JSON output prints the same `CurrentProfileSync is ...` sentence
  `adapters add-network` prints, whenever `change_location` is not `None`.
- `hmc_provision_lpar`'s docstring names the field and what it reports.

## Validation

- Contract: `change_location` reflects `CurrentProfileSync` after network/vSCSI/storage. Mode: focused-test — `test_provision_reports_change_location_when_synced` (sync `On`) and `test_provision_lpar_full_workflow` (sync `Disabled`); green: `lives_in` matches each value.
- Contract: a failed change-location read is advisory, not a provisioning failure. Mode: focused-test — `test_provision_change_location_read_failure_is_advisory`; green: `workflow_completed is True`, `change_location is None`, a matching warning present.
- Contract: `change_location` stays `None` when no adapter step ran. Mode: focused-test — `test_policy_provision_network_failure_records_each_step_once`; green: `result.change_location is None`.
- Contract: the CLI renders the summary line. Mode: focused-test — `test_lpars_provision_renders_change_location`; green: `"CurrentProfileSync is Disabled" in result.stdout`.
- Contract: `hmc_provision_lpar`'s docstring documents the field. Mode: task-test-not-applicable — prose docstring text; no executable or structural check distinguishes a correct description from a plausible wrong one, verified by design and code review instead.
