# ADR 0178: UOM writes send an untyped Accept and a typed Content-Type

## Status

Accepted (2026-09-24)

## Context

The shared write helpers sent the typed uom media type as both `Accept` and `Content-Type`, and
deletes sent a generic uom `Accept`. A V10R3 M1060 HMC answered every such write with 406
before mutating (#935). The issue's 2026-09-23 probe of a vSCSI client-adapter PUT recorded:
typed `Accept` → 406 whatever the `Content-Type`; `*/*` with a generic `Content-Type` → 415
naming the typed one; `*/*` with the typed `Content-Type` → 200 once the body was valid. The
adapter DELETE gave 406 with the generic `Accept` and 204 with `*/*`. ADR 0169 and ADR 0171
already send that shape by hand. The per-call-site `fallback_to_generic_uom_on_406` retry did
not help: the issue body (2026-09-22) records 406 on the VolumeGroup POST that used it.

LPAR create reached `mksyscfg` only because the REST PUT got 406. With the header fixed, the
2026-09-23 probe got a 400 `REST0001` on `PartitionMemoryConfiguration` (since fixed by #961), and
a 2026-09-24 create on this change's headers got 400 `REST0001`: "Attribute 'schemaVersion' must
appear on element 'PartitionProcessorConfiguration'". An HMC CLI read-back found no partition.

## Decision

`_write_uom` sends `Accept: */*` and `Content-Type: application/vnd.ibm.powervm.uom+xml;
type=<T>` on every `_put` and `_post`, once, with no retry. `_delete` sends `Accept: */*`.
The `X-HMC-Schema-Version` rule is unchanged. The opt-in fallback parameter is removed. GETs
keep the typed `Accept`.

`create_lpar` falls back to `mksyscfg` on a 406 and on a 400 whose body carries `REST0001`.
Both mean the HMC refused the document before creating anything.

## Consequences

Adapter, virtual-network, user, system and LPAR-create writes and every shared DELETE use one
header shape. An HMC level that insists on a typed `Accept` for a write would now fail with 406
where it passed before; none is in evidence. On V10R3 the create still takes `mksyscfg` and its
profile apply. An HMC level that accepts the REST create takes the REST path, which applies no
profile, as before this change. A 400 `REST0001` from a bad caller value is reported by
`mksyscfg`'s validation rather than the REST message.

## Considered & rejected

- **Keep typed `Accept` and retry with `*/*` on 406.** judgment: two requests per write on the
  one HMC level in evidence, and a second code path that only an older level would exercise.
- **Keep the per-call-site opt-in.** verified: the #935 issue body (2026-09-22) records 406 on
  the VolumeGroup POST that used it; its one remaining caller, the VolumeGroup PUT, was not
  probed.
- **Send no `Accept` at all.** verified: `python -c "import httpx; print(httpx.AsyncClient().headers)"`
  prints `'accept': '*/*'` (httpx 0.28.1), so the request is the same; naming it states the
  contract instead of relying on a library default.
- **Fall back to `mksyscfg` on every 400.** judgment: a 400 for another reason would be hidden
  behind a second, SSH-dependent attempt.
- **Leave LPAR create on the typed header so it keeps reaching 406.** judgment: that preserves a
  per-call-site exception, which is what #935 asks to remove.
- **Add `schemaVersion` to the processor wrappers so the REST create succeeds.** judgment: the
  REST path skips the profile apply that provisioning needs on V10R3 (#939), and a REST-created
  partition is unproven there; #961 left those wrappers unchanged.

## Amendment (2026-09-30, #1164): a schema-conformant V10R3 REST create succeeds

The 400 `REST0001` responses in the Context above were refusals of our own document, not of
the REST create path. With `schemaVersion` on the processor configurations and every element
in the order and enumeration spelling of the XSDs the HMC serves, a V10R3 `PUT
ManagedSystem/<S>/LogicalPartition` returns 200 with the created partition (#1161 P28–P31,
P36). The rejected alternative "Add `schemaVersion` to the processor wrappers" is therefore
adopted, because its premise no longer holds. A REST create writes `default_profile`,
populates the current configuration, and activates without a profile apply, for shared and
dedicated partitions alike (P37). The REST branch reports `apply_profile` as skipped for that
reason. It does not skip the apply because a profile is missing.

The create logic also enforces requirements the schema leaves optional: the processor mode,
a dedicated `SharingMode`, and the processing units and minimum virtual processors, each
refused with a 500 `REST0126` (P38). `mksyscfg` has no dedicated `sharing_mode` default
either. Both create paths now take their omitted values from one defaults function,
`ssh.lpar.complete_create_resources`, so the partition does not depend on which path created
it. A dedicated create without a sharing mode gets `keep_idle_procs`. That is a choice: the
HMC's own default for a REST create with no processor configuration was `share_idle_procs`
(P39), and neither path had one for a dedicated request. A create with no resource values still goes straight to `mksyscfg`, because only its
`all_resources=1` expresses that.

The `mksyscfg` fallback on 406 and on 400 `REST0001` stays. The fallback now logs the HMC's
unmarshal message, so a regression in our document shows up in the logs instead of being
masked by a successful CLI create.
