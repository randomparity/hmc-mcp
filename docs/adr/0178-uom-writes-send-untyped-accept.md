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
not help: the VolumeGroup POST that used it still got 406.

LPAR create reached `mksyscfg` only because the REST PUT got 406. With the header fixed the
probe got a 400 `REST0001` schema rejection instead.

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
where it passed before; none is in evidence. A create that V10R3 accepts takes the REST path,
which applies no `mksyscfg` profile. A 400 `REST0001` from a bad caller value is reported by
`mksyscfg`'s validation rather than the REST message.

## Considered & rejected

- **Keep typed `Accept` and retry with `*/*` on 406.** judgment: two requests per write on the
  one HMC level in evidence, and a second code path that only an older level would exercise.
- **Keep the per-call-site opt-in.** verified: the #935 comment probe records 406 on the
  VolumeGroup POST that already used it.
- **Send no `Accept` at all.** verified: `python -c "import httpx; print(httpx.AsyncClient().headers)"`
  prints `'accept': '*/*'` (httpx 0.28.1), so the request is the same; naming it states the
  contract instead of relying on a library default.
- **Fall back to `mksyscfg` on every 400.** judgment: a 400 for another reason would be hidden
  behind a second, SSH-dependent attempt.
- **Leave LPAR create on the typed header so it keeps reaching 406.** judgment: that preserves a
  per-call-site exception, which is what #935 asks to remove.
