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

**`HMC_SCHEMA_VERSION` — leave this unset for normal operation.** It is opt-in
and unset by default, because HMC V8/V9 targets do not need it and uom
documents already declare `schemaVersion=V1_0`. Set it only to pin schema
negotiation explicitly — for example while debugging a read path.

Where the `X-HMC-Schema-Version` header goes when it *is* set. The rule is per
call site, not per HTTP method — there is no method whose requests all carry it
and none whose requests all omit it:

- **UOM requests carry it unless their own call site opts out.** A call site
  opts out by passing `include_schema_version=False`, so
  `rg -n 'include_schema_version=False' src/hmc_mcp/client/` is the current set
  and the only answer that cannot go stale. Opting out was a response to HTTP
  406 on specific endpoints, not a property of any HTTP method or resource
  type: `PUT`/`POST LogicalPartition`, `PUT VirtualNetwork` and child-resource
  adapter `PUT` opt out, and so do many — **not all** — of the VolumeGroup and
  VirtualIOServer storage paths, reads included. Do not read a sibling
  endpoint's omission as covering yours; check the flag at the call site you
  are debugging.
- **Every `/rest/api/web/` request carries it**, reads and writes alike,
  because some HMC releases require it there.
- **Requests that build their own headers never carry it**, whatever this
  variable is set to. That is the `Accept: */*` discovery reads — quick
  properties, and the operation, schema and search-parameter listings, where
  ADR 0139 records the omission deliberately — the storage-broker ISO
  create/upload/cleanup calls, and every `do/{Operation}` job, which is the
  path LPAR power on/off and activation take.

Unset — or set to the empty string, which disables the header the same way —
is therefore the setting under which no request carries it at all.

A live run prints the resolved value in its run header, on stdout. The
`test-results-*.json` and observations documents do not record it, so a matrix
cited as evidence does not by itself say which of those two request
environments produced it — read that from the run's output.
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
