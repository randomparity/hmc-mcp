# Web File ISO upload — design (#978)

Decision record: [ADR 0177](../../adr/0177-iso-upload-through-the-web-file-api.md).

## Problem

`storage upload-iso` / `hmc_upload_iso` sends a `BrokeredFile` document that V10R3 rejects
(`400 REST0001`). ADR 0177 records the web File flow that the 2026-09-23 window proved.

## Scope

In scope:

- `documents/storage.py`: `build_web_file_document(filename: str, size_bytes: int,
  vios_uuid: str) -> str` replaces `build_brokered_file_document` and
  `build_linked_optical_media_document`. It is escaped by `@escapes_string_arguments`, and
  its element order is the order ADR 0177 records.
- `client/client_storage.py`: `_web_file_create`, `_web_file_upload`, `_web_file_delete` replace
  the four `_broker_*` methods. Paths that carry `file_uuid` go through
  `_request_with_uuid_path_arguments`. `vios_uuid` is checked as a UUID before the create
  request is built. Headers are the ones ADR 0177 records, passed through `_web_headers`.
- `operations/storage/resources.py`: `_upload_iso_via_web_file` replaces
  `_upload_iso_via_broker`. Visibility polling is bounded by module constants
  `VISIBILITY_POLLS = 12` and `VISIBILITY_POLL_SECONDS = 5.0`, so it waits about 55 s at
  most. `upload_iso`'s pre-checks, download, and temp-file cleanup do not change.
- The tool, CLI, and operation docstrings name the web File API. `docs/tools/media.md` is
  regenerated.
- Stale references are corrected: the ADR 0031 supersession banner, an ADR 0052 status
  amendment, the #978 blocker note in `docs/recipes/lpar-iso-install.md`, the "storage-broker
  ISO calls" clauses in `docs/compatibility.md` and `docs/environment-variables.md`, and a
  CHANGELOG entry.

Out of scope, per the WORK:SCOPE exclusions: a `SHA256` element (operator), the
`AddOpticalMedia` job (operator), a 406 fallback (#935), and the CLI recipe gap (#776).

## Behaviour

1. The operation creates the File and gets a `FileUUID`. A create that fails leaves nothing to
   delete. It raises `HMCError` for a status other than 200 or 201, or for a response without
   exactly one UUID-shaped `FileUUID`.
2. It streams the staged file with `_aiter_file_chunks` and `Content-Length: file_size`. Any
   status outside {200, 201, 202, 204} raises `HMCError`.
3. It polls `hmc.list_optical_media(vios_uuid, vg_uuid)` up to `VISIBILITY_POLLS` times. It
   sleeps `VISIBILITY_POLL_SECONDS` between polls, never before the first one. It returns the
   first entry whose `MediaName` equals `media_name`. If none appears, it raises
   `HMCError(status_code=None)`. The message names the media, the volume group, and the poll
   count, and says the ISO may still land in the VIOS repository, so the operator should check
   `list-optical-media` before retrying.
4. In a `finally`, once a `FileUUID` exists, it calls `_web_file_delete`. A delete failure is
   raised if nothing else failed. If something else failed, the delete failure is logged with
   the `FileUUID`, and the first failure is the one raised.
5. `upload_iso` returns the existing five keys. `media` is always the visible entry.

## Failure model

1. **Actors and deployments:** an operator or MCP client with a `mutate` grant on the VIOS
   (`media.upload_iso`), running the CLI or the MCP server against the V10R3 M1060 HMC the
   2026-09-23 window verified; other HMC releases are outside the model.
2. **Invariants and assets:** the VIOS media repository's contents. The shared server
   process's memory (ADR 0052: the ISO is never buffered). No leaked File handle after a
   successful create. `uploaded` is never reported for media the repository does not list.
3. **Accepted failure classes:**
   - A create that the HMC applied but whose response was lost or malformed leaves an
     orphaned handle. We have no `FileUUID` to delete, and the error names the create.
   - The contents PUT timing out under `HMC_TIMEOUT` on a large ISO. The existing timeout
     message tells the operator to raise it (ADR 0177 consequence).
   - Media that becomes visible after the poll bound. The error says so and does not tell the
     operator to retry blindly.
4. **Covered elsewhere:** a 406 on typed headers (#935). URL allowlist, redirect refusal, and
   the size bound (ADRs 0049, 0050, and #303, unchanged). Outbound XML escaping (ADR 0042).
   Response-size bounds (the shared `_request`).

## Success

- Three requests are pinned by unit tests, including their headers, bodies and paths: create
  (method, path, `Content-Type`, `Accept`, element order and values), contents (path,
  `Content-Type`, `Content-Length`, streamed body equal to the staged bytes), delete (path).
- On every exit after a successful create (upload failure, visibility failure, success), the
  delete runs exactly once.
- An upload the repository never lists raises, and does not return `uploaded`.
- `just verify` and `uv run --no-sync prek run --all-files` pass.
- Live: on the authorized HMC, create, upload, visibility and delete of a small ISO succeed
  under the campaign lock. The media is then removed with `storage delete-media`, or with
  `rmvopt` through `viosvrcmd` while #935 blocks UOM writes. The before and after baselines match.
