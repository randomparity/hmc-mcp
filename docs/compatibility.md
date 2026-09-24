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
  `rg -n 'include_schema_version=False' src/hmcpctl/client/` is the current set
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
  ADR 0139 records the omission deliberately — and every `do/{Operation}`
  job, which is the path LPAR power on/off and activation take.

Unset — or set to the empty string, which disables the header the same way —
is therefore the setting under which no request carries it at all.

A live run prints the resolved value in its run header, on stdout. The
`test-results-*.json` and observations documents do not record it, so a matrix
cited as evidence does not by itself say which of those two request
environments produced it — read that from the run's output.
See [`docs/environment-variables.md`](environment-variables.md) for all
supported variables.

### Firmware write-path compatibility

HMC V10R3 answers a UOM write with HTTP 406 when its `Accept` header names a uom
media type, and with 415 when its `Content-Type` does not name the resource type.
Shared UOM writes therefore send `Accept: */*` with a typed `Content-Type`, and
shared deletes send `Accept: */*` ([ADR 0178](adr/0178-uom-writes-send-untyped-accept.md)).

LPAR creation (`hmc_create_lpar`, `hmc_provision_lpar`) falls back to `mksyscfg`
over SSH when the REST create is refused with a 406 or with a 400 `REST0001`
schema rejection, which V10R3 returns for the current create document.
`HMC_PASSWORD` (or `HMC_SSH_KEY_FILE`) must be set for SSH auth; the fallback is
transparent to the caller.
