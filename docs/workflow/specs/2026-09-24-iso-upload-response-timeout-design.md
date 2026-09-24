# ISO upload response timeout (#1055)

## Problem

`_web_file_upload` streams the ISO under the client-wide `HMC_TIMEOUT` (60 s default), which
httpx applies to each connect, write, pool and read wait. The read wait that matters is the one
after the last byte: the HMC answers only when it has finished with the file. ADR 0177 left it
unmeasured.

Measured on 2026-09-24 against `sys-R1` (`V10R3 M1060`, `8375-42A`), at main 9947ef10, with the
default 60 s timeout, through `_upload_iso_via_web_file` unchanged:

| ISO bytes | contents PUT | last byte to response | longest gap between chunk writes |
|---:|---:|---:|---:|
| 1,048,627,200 | 343.9 s | 6.7 s | 0.53 s |
| 4,194,355,200 | 1,540.1 s | 14.8 s | 1.96 s |

Both uploads succeeded and were listed on the first visibility poll. The response wait grows with
size: a line through the two points (about 4.0 s plus 2.6 s per GB) crosses 60 s near 21.6 GB,
and reaches about 282 s at the 100 GiB download ceiling (`MAX_DOWNLOAD_SIZE_BYTES`). A larger ISO
therefore fails with default configuration while the HMC may still import it.

## Design

1. `HMCConfig.upload_timeout: float`, default `600.0`, `gt=0` (`HMC_UPLOAD_TIMEOUT`, TOML
   `upload_timeout`). 600 s is about twice the extrapolated wait at the download ceiling.
2. `_web_file_upload` sends the contents PUT with
   `httpx.Timeout(config.timeout, read=max(config.timeout, config.upload_timeout))`. Connect,
   write and pool waits keep `HMC_TIMEOUT`: the measured write gaps are under 2 s. The `max`
   keeps an operator who already raised `HMC_TIMEOUT` above 600 s from getting a shorter wait.
3. A read timeout on that PUT raises `HMCTransportError` saying the ISO was sent, no response came
   within the read timeout, the HMC may still import it (check `list-optical-media` before
   retrying), and to raise `HMC_UPLOAD_TIMEOUT`. The generic "Increase HMC_TIMEOUT" message would
   name the wrong setting. Other timeouts keep the generic message.
4. ADR 0177 gets an "Amended by #1055" Status block recording the measurement and this timeout.
5. `docs/environment-variables.md` documents `HMC_UPLOAD_TIMEOUT`; `CHANGELOG.md` gets one entry.

### Rejected

- **Record the measurement only.** judgment: fit. The extrapolation crosses 60 s inside the
  supported download size.
- **Scale the read timeout from `content_length`.** judgment: cost. It builds a two-point fit
  into code, and operators still need a setting when their HMC is slower.
- **Raise the `HMC_TIMEOUT` default.** Excluded by the operator (2026-09-24).

## Failure model

1. Actors and deployments: an operator or MCP client calling `upload_iso` against a V10R3 M1060
   HMC. Other HMC levels are unmeasured.
2. Invariants and assets: no other request's timeout changes. A timeout after the bytes were
   sent must not read as "nothing happened".
3. Accepted failure classes:
   - A stalled HMC holds an upload open for up to `upload_timeout`. The cost is bounded and set by
     the operator.
   - The line is fitted to two points up to 4.2 GB. A slower HMC or VIOS is the reason the value
     is configurable.
   - The value is operator-supplied local configuration, the same trust level as `HMC_TIMEOUT`.
     No new untrusted input.
4. Covered elsewhere: resumable or chunked upload, and the global `HMC_TIMEOUT` default (both
   operator-owned exclusions); whether deleting the File cancels an import (ADR 0177).

## Validation

- Config default, env parsing, and refusal of `0` and negative values. focused-test:
  `tests/unit/test_config.py`.
- The contents PUT carries `read=600` with the other waits at `HMC_TIMEOUT`, and `read` follows a
  larger `HMC_TIMEOUT`. focused-test: `tests/storage/test_web_file_upload.py`, which reads the
  request's `timeout` extension.
- A read timeout on the PUT raises the upload-specific message, and a write timeout keeps the
  generic one. focused-test: same module, through a respx `side_effect`.
- Env-var documentation. focused-test: `just env-vars`.
- ADR amendment and CHANGELOG. task-test-not-applicable: the ADR body is prose that no gate reads.
  `CHANGELOG.md` is covered by the repo's doc tests.
