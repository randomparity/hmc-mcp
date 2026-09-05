# HMC compatibility

[Documentation index](index.md)

## HMC version compatibility

`hmc-mcp` targets **HMC V8 through V11** and all the POWER generations they
manage. All uom XML documents are written with `schemaVersion="V1_0"` — the
floor every supported HMC understands — so create/modify operations succeed
regardless of firmware age.

| HMC version | POWER generations managed | uom schema floor |
|-------------|--------------------------|------------------|
| HMC V8      | POWER6, POWER7, POWER8   | V1_0             |
| HMC V9      | POWER7, POWER8, POWER9   | V1_0             |
| HMC V10     | POWER8, POWER9, POWER10  | V1_0             |
| HMC V11     | POWER9, POWER10, POWER11 | V1_0             |

Three VIOS backup catalog tools have a narrower floor:
`hmc_list_vios_backups`, `hmc_backup_vios`, and `hmc_restore_vios` require
**HMC V10 or newer**. Their supported HMC commands do not exist in the V9.1.940
command inventory, so these tools have no runtime version probe or V8/V9
fallback. Other tools retain the general HMC V8 through V11 support stated
above.

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
