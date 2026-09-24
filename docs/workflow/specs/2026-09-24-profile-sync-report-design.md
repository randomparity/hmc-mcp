# Report where adapter changes live and check the profile at power-on (#981)

## Problem

REST adapter and mapping writes change only a partition's current configuration unless its
`CurrentProfileSync` is `On`. `lpars power-on --partition-profile` activates the profile and
silently drops adapters the profile lacks, so the firmware finds no boot device.

## Scope

New `operations/lpar/profile_sync.py` owns the policy; client mixins stay unchanged.

- `ChangeLocation(current_profile_sync, lives_in, profile_name)` with `summary()`.
  `read_change_location(hmc, lpar_uuid)` reads the partition entry once: `On` gives
  `current-configuration-and-profile` plus `LastActivatedProfile`; `Disabled` or `Suspended`
  gives `current-configuration`; anything else gives `unknown` and keeps the raw value.
- Operations `add_network_adapter`, `add_vscsi_adapter`, `add_vfc_adapter`, `delete_adapter`,
  `map_storage`, `mount_optical_media`, `detach_storage_mapping`, `unmount_optical_media` call
  it after authorization, before the write. `AdapterResult` and `StorageMapResult` gain
  `change_location`; mount returns `StorageMapResult`; delete, detach, unmount return the
  `ChangeLocation`. MCP add/map/mount tools return the whole result; delete/detach/unmount tools
  append `summary()` to their message. The CLI prints `summary()`.
- `power_lpar` with a partition profile compares the current configuration's
  `VirtualSCSIClientAdapter`, `VirtualFibreChannelClientAdapter` and `ClientNetworkAdapter`
  slots with every `VirtualSlotNumber` under the profile's `ProfileVirtualIOAdapters`, whatever
  the subclass name (the V10R3 capture shows only the vSCSI one). Each missing adapter is one
  `warnings` entry on `LparPowerResult` and `LparPowerOnOutcome`; the CLI prints them.
- **Warn, not refuse.** Activating a profile is the HMC's way to discard current changes;
  refusing needs a new override flag; a wrong activation is recovered by powering off.
- Recipe step 5, `docs/cli.md`, regenerated `docs/tools/` and `CHANGELOG.md` describe both.

No ownership transition: a clean extension of the operations layer.

### Failure model

1. Actors and deployments: a local CLI operator; an MCP agent such as kdive.
2. Invariants: a successful write is never reported as failed (the read precedes it); the
   power-on job document is unchanged.
3. Accepted: a failed partition, adapter-feed or profile read fails the command before any write
   or job. A same-slot adapter of another type counts as present (slots are unique per
   partition). `LastActivatedProfile` is taken as the synced profile's name.
4. Covered elsewhere: adapter PUT 406 on V10R3 (#935); sync default at create (#939);
   `lpars provision` and other profile-affecting operations (operator).

## Success

- The eight named operations report `CurrentProfileSync` and the location via CLI and MCP.
- Power-on with a profile warns once per current client adapter of the three named types whose
  slot the profile lacks, and warns nothing when the profile has them all.

## Validation

- `lives_in` for `On`, `Disabled`, `Suspended`, missing — focused-test,
  `tests/unit/test_profile_sync.py`; red: module absent; green: `pytest` on that file.
- Profile diff over single, list and empty subclass shapes — focused-test, same file.
- Adapter and mapping operations return `change_location`, read before the write —
  focused-test in their existing operation tests; red: attribute missing.
- `power_lpar` warnings and the no-warning case — focused-test, `tests/lpar/test_power.py`.
- MCP and CLI rendering — focused-test in the existing app tool and CLI tests.
- Recipe and cli prose — task-test-not-applicable: no executable consumer reads these pages.
