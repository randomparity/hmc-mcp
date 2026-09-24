# Boot order: Advanced-group read and read-modify-write (#980)

## Problem

On V10R3 the boot-order commands do not work.

- **Read.** `BootListInformation`'s four boot fields are leaf elements that carry
  `group="Advanced"`. An empty one parses as its attribute dict, which the read returns as
  `{"group": "Advanced", "ksv": "V1_5_0"}`. A populated one parses as `{"@attrs": ..., "text": ...}`.
  Neither is a string.
- **Write.** Set and clear POST a sparse `LogicalPartition` with `PendingBootString` as a direct
  child. V10R3 rejects it: `400 REST0001 ... Invalid content was found starting with element
  'PendingBootString'`. The field belongs inside `BootListInformation` (`kb="UOD"`).
- **Value.** The write sends the selectors `cd`, `disk` and `network`. The HMC's boot strings are
  Open Firmware device paths.

Evidence, all V10R3 M1060, private #879 capture (2026-09-23) and a read-only probe (2026-09-24):

- A never-booted partition has all four fields empty, with and without `?group=Advanced`.
- 74 of the 77 partitions in a `LogicalPartition` feed have a populated `BootDeviceList`: Open
  Firmware paths separated by spaces, for example `/vdevice/v-scsi@30000003/disk@8100000000000000`
  or `/vdevice/l-lan@30000002:speed=auto,...`. None holds a CD path. None has a populated
  `PendingBootString`.
- A single-partition GET returns an `ETag` header equal to the entry's `etag:etag`.

## Decision

**The input is Open Firmware device paths, not selectors.** The CLI and the MCP tool take an
ordered list of paths. The write joins them with single spaces. Each path must start with `/` and
must not contain whitespace or control characters, so joining cannot merge two paths or split
one. `hmcpctl` does not map selectors to paths. `read-boot-order` reports the paths the HMC
knows, and the docs point there.

**Set and clear read, modify, and write the whole partition.** This follows ADR 0171, the
VolumeGroup precedent, applied to one more resource.

1. GET `/rest/api/uom/LogicalPartition/<uuid>?group=Advanced`. Headers: typed `Accept`, no
   `X-HMC-Schema-Version`.
2. A response that is not 200, or that has no `ETag`, is refused before any POST.
3. Find the `LogicalPartition` element: either the root, or the child of an Atom `content`.
   Then find its direct `BootListInformation` child and that element's `PendingBootString`
   child. If any of these is missing, refuse. The code never makes an element up.
4. Replace only the text of `PendingBootString`. Clear sets it to empty. Every attribute and
   sibling stays as the HMC sent it.
5. POST the element to `/rest/api/uom/LogicalPartition/<uuid>`. Headers: `Accept: */*`, typed
   `Content-Type`, and `If-Match` set to the ETag. A 412 means a concurrent change and nothing
   was written. Any status other than 200, 201 or 202 raises `HMCError`.

The new client method is `LparsMixin.set_pending_boot_string(lpar_uuid, boot_string)`.
`modify_logical_partition` does not change.

**The read requests `?group=Advanced`.** It returns each field's text, or `None` when the field
is empty or missing. `boot_device_list` is the HMC's space-separated string, the same form the
write joins. The output keys do not change.

Ownership:

- `documents/boot.py` validates and joins paths. `build_pending_boot_string` replaces
  `BOOT_DEVICE_SELECTORS`, `BootDeviceSelector`, and both sparse builders, which are removed.
- The client mixin owns the HTTP read-modify-write.
- `operations/lpar/boot_order.py` validates, then authorizes, then calls the client. It still
  translates errors with `translate_lpar_write_error`.

Callers of the changed contract:

- The CLI `set-boot-order` takes paths as positional arguments, not a comma list. Network paths
  contain commas.
- Live-test ST20 can no longer set `cd,network,disk`. It sets the baseline `boot_device_list`
  paths instead. It skips that step when the list is empty, and splits the saved pending string
  on whitespace.

The ISO recipe's blocker note and `CHANGELOG.md` describe the new contract.

## Considered & rejected

- **Selectors resolved against `BootDeviceList`.** verified: in the 2026-09-24 read-only V10R3
  feed probe, the list is empty on the never-booted partition the ISO recipe targets, and no
  populated list holds a CD path. So `cd`, the recipe's selector, resolves nowhere.
- **Selectors and paths both.** judgment: this keeps a selector path that never resolves in the
  case it exists for.
- **Fix the sparse document's placement.** judgment: a sparse `BootListInformation` still omits
  the rest of the partition, which ADR 0171 declined for VolumeGroup. It also has not been shown
  to validate.
- **Send `If-Match` only when an ETag exists.** judgment: an unconditional whole-partition write
  could lose a concurrent change. ADR 0171 rejected the same thing.

## Failure model

1. **Actors and deployments:** a local operator using the CLI, and an MCP client driven by an
   agent. Both write through `hmcpctl` to one HMC, V10R3 as observed. V11R2 behavior is
   unobserved.
2. **Invariants and assets:**
   - Partition configuration other than `PendingBootString` must not change. The POST sends the
     values that were read, under `If-Match`.
   - The CLI and tool input contract is published and changes here. The project is pre-release.
3. **Accepted failure classes:**
   - A path that passes validation but is not a real device. The HMC or firmware decides, and
     `hmcpctl` cannot tell without a boot.
   - A 406 on the POST, if #935 still applies to this header set. It is reported as the HMC's
     error.
   - A partition whose GET has no ETag cannot have its boot order set. The error names the
     missing ETag.
4. **Covered elsewhere:**
   - The UOM write header strategy: #935.
   - The sparse POSTs from rename and DLPAR: a follow-up candidate, not fixed here.

### Threat model

- **Boundaries:**
  - Caller paths enter an XML text node. ElementTree escapes them, and validation bounds their
    shape.
  - The HMC response is parsed with `defusedxml`, as the existing parsers do.
- **Actor:** an MCP client can send any string. Authorization is the existing
  `resolve_and_authorize_lpar_mutation`.
- **Out of scope:** a compromised HMC returning a hostile document. The client trusts its HMC.

## Success

1. The read returns plain strings or `None` from `?group=Advanced`, for a live-shaped redacted
   fixture holding both empty and populated fields.
2. Set and clear send exactly one GET and one POST. The POST carries `If-Match`, `Accept: */*`,
   and the fixture's `LogicalPartition` with only `PendingBootString` text changed. They refuse
   without a POST when the GET has no ETag or no `BootListInformation/PendingBootString`, and
   report a 412 as nothing written.
3. Path validation rejects an empty list, a path that does not start with `/`, and a path
   containing whitespace or a control character. Validation runs before authorization or any
   request.
4. The CLI and tool help describe Open Firmware paths, and the recipe note no longer calls the
   commands blocked by #980.
5. Live: the Advanced-group read on the authorized partition. The write, under the campaign
   lock, either round-trips or is recorded against #935.

## Validation

Focused tests: `tests/lpar/test_boot_order.py` covers criteria 1-3 through a respx-mocked client
and a redacted live-shaped `LogicalPartition` entry built in that module. `tests/app/test_cli_commands.py`
covers the CLI. `tests/test_live_runner.py` covers the ST20 migration. The guardrails are
`just verify` and `uv run --no-sync prek run --all-files`. Live acceptance runs on minimus, as
`docs/live-testing.md` and the campaign lock describe.
