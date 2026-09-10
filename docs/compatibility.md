# HMC compatibility

[Documentation index](index.md)

## HMC version compatibility

The checked reference corpus covers retained POWER10 and POWER11 command and REST
documentation snapshots. It is an evidence inventory, not a blanket compatibility
promise for every operation, HMC release, or managed system. Consult the
[HMC reference capability ledger](capabilities/README.md) for the corpus scope and
per-operation evidence, and the [generated tool reference](tools/index.md) for the
maturity currently published by the installed code.

Three VIOS backup catalog tools have a narrower floor:
`hmc_list_vios_backups`, `hmc_backup_vios`, and `hmc_restore_vios` require
**HMC V10 or newer**. Their supported HMC commands do not exist in the V9.1.940
command inventory, so these tools have no runtime version probe or V8/V9
fallback.

**`HMC_SCHEMA_VERSION` — leave this unset for normal operation.**
`hmc-mcp` omits the `X-HMC-Schema-Version` request header from all write
paths (`PUT`/`POST`) regardless of this setting — some HMC firmware versions
return HTTP 406 on every UOM write when that header is present. The variable
only affects `GET` requests. Set it only if you are debugging schema
negotiation on a specific HMC read path; it has no effect on LPAR creation,
adapter configuration, storage operations, or any other mutating call.
See [`docs/environment-variables.md`](environment-variables.md) for all
supported variables.

### Firmware write-path compatibility

Some HMC V10 firmware builds return HTTP 406 for all UOM write paths — even
without the schema-version header — for child-resource endpoints such as
`ClientNetworkAdapter` and `VirtualSCSIClientAdapter` PUT. On those builds:

- **LPAR creation** (`hmc_create_lpar`, `hmc_provision_lpar`): automatically
  falls back to `mksyscfg` over SSH. `HMC_PASSWORD` (or `HMC_SSH_KEY_FILE`)
  must be set for SSH auth; the fallback is transparent to the caller.
- **Virtual adapter attachment** (`hmc_add_network_adapter`,
  `hmc_add_vscsi_adapter`): no automatic fallback. Configure adapter profiles
  via the HMC GUI, the HMC CLI (`chhwres`), or the opt-in `hmc_run_command`
  escape hatch if this affects your firmware.
- **Virtual disk creation** (`hmc_create_virtual_disk`): no automatic fallback.
  The disk can be created directly on the VIOS with `mkbdsp` and then mapped
  with `hmc_map_storage_to_lpar`.
