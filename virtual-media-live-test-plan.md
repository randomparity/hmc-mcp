# Virtual Media Live Test Plan — ltczz386

## Overview

Add sub-tasks **ST16–ST22** directly to the existing
[`scripts/live_test_runner.py`](scripts/live_test_runner.py), exercising every virtual-media
MCP tool added in the issue #200 epic against the real HMC/VIOS on system **ltczz386**.

**Protected LPARs:** `ltczz386-lp1` and `ltczz386-lp2` must not be touched at any point.

**Test LPAR:** `ltczz386-lp3` is used as the boot target. It will be powered off, have a
media repository and ISO mounted, have its boot order set to `cd` first, be powered on to
verify CD boot, then be restored to its pre-test state at the end of ST22.

**ISO source:** `~/Downloads/ubuntu-26.04-live-server-ppc64el.iso` (local file on the
machine running the test script).

**Results file:** `test-results-vmedia.json` (separate output file so the virtual-media
round is independently inspectable).

**Entry point:** ST16–ST22 are added to `live_test_runner.py`'s `SUBTASKS` dict. The
existing group-select mechanism is extended so a caller can run a named group of
sub-tasks:

```
# Run all virtual-media sub-tasks:
uv run python scripts/live_test_runner.py --group vmedia

# Run a single sub-task by number (existing behaviour):
uv run python scripts/live_test_runner.py 18
```

---

## New Context Fields

The `LiveTestContext` dataclass gains the following fields to carry state across sub-tasks:

```python
vmedia_repo_created: bool = False          # ST16: True after repository is confirmed live
vmedia_iso_name: str | None = None         # ST17/ST18: MediaName of the uploaded ISO
vmedia_mapping_uuid: str | None = None     # ST19: UUID of the active VirtualSCSIMapping
vmedia_orig_boot_order: list[str] = field(default_factory=list)  # ST20: saved for restore
```

---

## Sub-Task Groups

Two named groups are defined in a `SUBTASK_GROUPS` dict added to `live_test_runner.py`:

```python
SUBTASK_GROUPS = {
    "round2":  list(range(0, 16)),    # ST0–ST15 (existing)
    "vmedia":  list(range(16, 23)),   # ST16–ST22 (new)
    "all":     list(range(0, 23)),    # complete suite
}
```

The `main()` function is updated to accept an optional `--group` argument. When `--group`
is supplied without a subtask number, all sub-tasks in that group run in order. A bare
numeric argument continues to run exactly one sub-task (unchanged behaviour).

---

## Sub-Tasks

---

### ST16 — VG Free-Space Check + Repository Create

**Intent:** Inspect the Volume Group on the VIOS, determine available free space, and
create the virtual media repository only if the requested size fits. If free space is
unknown, attempt creation at the proposed size and treat a rejection as a skip. Leaves
the repository live for ST17–ST21.

A repository created here must be deleted by ST22 under all outcomes. The sub-task records
whether it created the repository so ST22 knows to attempt teardown.

**Expected Outcomes:**
- `hmc_list_vios(system_name_or_uuid="ltczz386")` resolves `context.vios_uuid`
- `hmc_list_volume_groups(vios)` returns the VG; free-space field is read and printed
- If free space ≥ 7000 MiB (or unknown): `hmc_create_media_repository(size_mib=7000)` succeeds
- `hmc_get_media_repository` confirms the repository exists
- `context.vmedia_repo_created` is set to `True`

**Todo List:**
1. If `context.vios_uuid` is already set (from a previous results file), skip `hmc_list_vios`.
   Otherwise call `hmc_list_vios(system_name_or_uuid="ltczz386")` and pick the first entry's
   UUID. Note: `hmc_list_vios` returns all VIOSes; the first result when scoped to a single
   system is the system's only VIOS.
2. Call `hmc_list_volume_groups(vios_uuid)`. Pick the first VG UUID; read its
   `FreeSpace` / `FreeSpaceInMBytes` field. Print free-space value to stdout.
   If `context.vg_uuid` is already set, still call this to read current free space.
3. If free space is known and < 7000 MiB, record SKIP for all remaining ST16 steps and
   set a flag to skip ST17–ST22.
4. Call `hmc_create_media_repository(vios_uuid, vg_uuid, size_mib=7000)`. Record PASS/FAIL.
   On FAIL, skip remaining ST16 steps and abort ST17–ST22.
5. Call `hmc_get_media_repository(vios_uuid, vg_uuid)`. Confirm non-empty. Record PASS/FAIL.
6. Set `context.vmedia_repo_created = True` on PASS.

**Relevant Context:**
- `hmc_list_vios`, `hmc_list_volume_groups`, `hmc_create_media_repository`,
  `hmc_get_media_repository` MCP tools
- `LiveTestContext.vios_uuid` / `vg_uuid` may already be seeded from `test-results-round2.json`
- Free-space field names vary by HMC firmware: try `FreeSpace`, `FreeSpaceInMBytes`, and
  `free_space` in order

**Status:** `[x] done`

---

### ST17 — Short Repository Lifecycle (no ISO)

**Intent:** Verify the pure repository CRUD path — create a second, tiny (512 MiB)
repository, inspect it, then delete it — before committing to the full ISO upload. This
exercises `hmc_create_media_repository`, `hmc_get_media_repository`,
`hmc_list_optical_media`, and `hmc_delete_media_repository` without any file-transfer cost.

Note: two repositories cannot exist on the same VG simultaneously. ST16 already created
the main 7000 MiB repository. This sub-task therefore **deletes the ST16 repository first,
creates a small one, verifies it, deletes the small one, and re-creates the 7000 MiB
repository** before exiting. The 7000 MiB repository must be live when ST17 exits.

**Expected Outcomes:**
- `hmc_delete_media_repository` on the ST16 repository succeeds
- `hmc_create_media_repository(size_mib=512)` succeeds
- `hmc_get_media_repository` returns the small repository
- `hmc_list_optical_media` returns an empty list
- `hmc_delete_media_repository` on the small repository succeeds
- `hmc_get_media_repository` returns None/empty (confirmed deleted)
- `hmc_create_media_repository(size_mib=7000)` re-creates the main repository
- `context.vmedia_repo_created` remains `True`

**Todo List:**
1. Guard: if `context.vmedia_repo_created` is False, skip all steps.
2. Delete the ST16 repository: `hmc_delete_media_repository(vios_uuid, vg_uuid)`.
3. Create a small repository: `hmc_create_media_repository(vios_uuid, vg_uuid, size_mib=512)`.
4. `hmc_get_media_repository(vios_uuid, vg_uuid)`. Assert non-empty.
5. `hmc_list_optical_media(vios_uuid, vg_uuid)`. Assert empty list.
6. `hmc_delete_media_repository(vios_uuid, vg_uuid)`. Record PASS/FAIL.
7. `hmc_get_media_repository(vios_uuid, vg_uuid)`. Assert None/empty.
8. Re-create the main repository: `hmc_create_media_repository(vios_uuid, vg_uuid, size_mib=7000)`.
   Update `context.vmedia_repo_created` to the PASS/FAIL outcome.

**Relevant Context:**
- `hmc_create_media_repository`, `hmc_get_media_repository`, `hmc_list_optical_media`,
  `hmc_delete_media_repository` MCP tools
- VIOS allows only one VMLibrary per VG — the delete/create cycle is required to test both
  create and delete in sequence

**Status:** `[x] done`

---

### ST18 — ISO Upload via HTTP

**Intent:** Exercise `hmc_upload_iso` against its only accepted source type, an
`http`/`https` URL — here `~/Downloads/ubuntu-26.04-live-server-ppc64el.iso` served by a
local `http.server` thread the runner starts. ADR 0049 removed the local-path source: a
value that is not an http(s) URL is refused before anything is read.

The SHA-256 deduplication behaviour is also validated: the second upload of the same
content returns `status="existing"`.

After both uploads are confirmed, the ISO is deleted and re-uploaded so the repository
holds a single clean copy ready for ST19.

**Expected Outcomes:**
- First upload: `status` is `"uploaded"`, `MediaName` matches requested name
- Second upload of the same URL under another name: `status` is `"existing"`
- `hmc_list_optical_media` shows exactly one entry
- `hmc_delete_optical_media` succeeds on the unmounted media
- Re-upload succeeds; `context.vmedia_iso_name` is set

**Todo List:**
1. Guard: if `context.vmedia_repo_created` is False, skip all steps.
2. Resolve `iso_path = str(Path.home() / "Downloads" / "ubuntu-26.04-live-server-ppc64el.iso")`.
   If the file does not exist, record FAIL and skip the rest of ST18 and ST19.
3. Start a Python `http.server.HTTPServer` in a daemon thread serving that directory on
   `localhost:18765`, giving the URL
   `http://localhost:18765/ubuntu-26.04-live-server-ppc64el.iso`. The server is started
   once per process and is not shut down — ST20 uploads from it too. If the bind fails,
   record FAIL and skip the rest of ST18.
4. Call `hmc_upload_iso(vios_uuid, vg_uuid, media_name="ubuntu-26.04-test.iso",
   iso_source=<url>)`. This will take several minutes — print a progress note before
   calling. Record PASS/FAIL; capture `status` field.
5. Call `hmc_list_optical_media(vios_uuid, vg_uuid)`. Assert one entry; capture
   `MediaName` into `context.vmedia_iso_name`.
6. Call `hmc_upload_iso(vios_uuid, vg_uuid, media_name="ubuntu-26.04-http-test.iso",
   iso_source=<url>)`. Expect `status="existing"` because the SHA-256 matches the
   already-uploaded ISO. Record PASS if status is `"existing"`, FAIL otherwise.
7. Call `hmc_list_optical_media(vios_uuid, vg_uuid)`. Assert still exactly one entry
   (deduplication did not add a second copy).
8. Call `hmc_delete_optical_media(vios_uuid, vg_uuid, media_name="ubuntu-26.04-test.iso")`.
9. Call `hmc_list_optical_media(vios_uuid, vg_uuid)`. Assert empty.
10. Re-upload from the same URL (as step 4) to restore the media for ST19.
    Update `context.vmedia_iso_name` from the result.

**Relevant Context:**
- `hmc_upload_iso` MCP tool; `operations_storage.upload_iso` accepts http(s) URLs only
- Use Python stdlib `http.server.HTTPServer` + `threading.Thread` — no Flask dependency
- Upload timeout is 300 s read timeout; the ISO is large; step 4 may take minutes
- SHA-256 deduplication: second upload of identical bytes → `status="existing"`,
  returns prior `media_name` without touching the repository

**Status:** `[x] done`

---

### ST19 — Mount / Unmount + Safe-Delete Validation

**Intent:** Verify `hmc_mount_optical_media` and `hmc_unmount_optical_media` round-trip
correctly: mount the ISO to lp3, confirm the mapping is visible, verify that the safe-delete
guard blocks media deletion while mounted, then unmount and verify the mapping disappears.

**Expected Outcomes:**
- `hmc_mount_optical_media` returns a mapping dict; `context.vmedia_mapping_uuid` is set
- `hmc_list_optical_mappings(vios, lpar="ltczz386-lp3")` includes the mapping UUID
- `hmc_delete_optical_media` is rejected with a "mapped" / "in use" error (safe-delete)
- `hmc_unmount_optical_media` succeeds
- `hmc_list_optical_mappings` returns an empty list after unmount
- `hmc_delete_optical_media` succeeds after unmount (media cleaned up for ST20)
- `hmc_list_optical_media` returns an empty list after deletion

**Todo List:**
1. Guard: if `context.vmedia_iso_name` is None, skip all steps.
2. `hmc_mount_optical_media(vios_uuid, media_name=context.vmedia_iso_name,
   lpar_name_or_uuid="ltczz386-lp3")`. Record PASS/FAIL. Capture mapping UUID from result
   into `context.vmedia_mapping_uuid`.
3. `hmc_list_optical_mappings(vios_uuid, lpar_name_or_uuid="ltczz386-lp3")`. Assert the
   captured UUID appears in the list.
4. Attempt `hmc_delete_optical_media(vios_uuid, vg_uuid,
   media_name=context.vmedia_iso_name)`. Expect FAIL with text containing "mapped" / "in
   use" / "mapping". Record as PASS if the expected rejection is returned (safe-delete
   guard working correctly), FAIL otherwise.
5. `hmc_unmount_optical_media(vios_uuid,
   mapping_uuid=context.vmedia_mapping_uuid)`. Record PASS/FAIL.
   Clear `context.vmedia_mapping_uuid` on PASS.
6. `hmc_list_optical_mappings(vios_uuid,
   lpar_name_or_uuid="ltczz386-lp3")`. Assert empty list.
7. `hmc_delete_optical_media(vios_uuid, vg_uuid,
   media_name=context.vmedia_iso_name)`. Expect PASS. Clear `context.vmedia_iso_name`.
8. `hmc_list_optical_media(vios_uuid, vg_uuid)`. Assert empty list.

**Relevant Context:**
- `hmc_mount_optical_media` (STATE_CHANGING), `hmc_unmount_optical_media` (DESTRUCTIVE),
  `hmc_list_optical_mappings` (READ_ONLY), `hmc_delete_optical_media` (DESTRUCTIVE)
- Safe-delete rejection pattern (mirror `_record_expected_or_real`):
  ```python
  if st == "FAIL" and any(s in str(data).lower()
          for s in ["mapped", "in use", "mapping"]):
      record(state, 19, "hmc_delete_optical_media (blocked — expected)", "PASS", ...)
  else:
      record(state, 19, "hmc_delete_optical_media (blocked)", st, data)
  ```

**Status:** `[x] done`

---

### ST20 — Boot Verification: Power Off → Set CD Boot → Power On → Verify → Restore

**Intent:** Perform a full boot-from-CD-ROM test using lp3. Power lp3 off, re-mount the
ISO, set the boot order to `["cd", "network", "disk"]`, power lp3 on, verify that the
LPAR enters `Running` state (boot started from the virtual CD), then power it off, unmount
the ISO, and restore the original boot order. This is the highest-value live validation of
the virtual media epic.

This sub-task re-uploads the ISO (it was deleted in ST19) at the start and cleans up fully
at the end.

**Expected Outcomes:**
- lp3 is powered off before the test (`hmc_power_off_lpar`)
- `hmc_upload_iso` re-uploads the Ubuntu ISO; `context.vmedia_iso_name` is set again
- `hmc_mount_optical_media` mounts the ISO to lp3; mapping UUID is captured
- `hmc_read_lpar_boot_order` returns current boot order (saved as baseline)
- `hmc_set_lpar_boot_order(devices=["cd", "network", "disk"])` succeeds
- `hmc_power_on_lpar` job completes; LPAR state transitions to `Running`
- `hmc_lpar_state` or `hmc_lpar_summary` confirms `Running` state
- `hmc_power_off_lpar --immediate` returns lp3 to `Not Activated`
- `hmc_unmount_optical_media` removes the optical mapping
- `hmc_clear_lpar_boot_order` or `hmc_set_lpar_boot_order` restores original boot order
- `context.vmedia_orig_boot_order` is cleared after restore

**Todo List:**
1. Guard: if `context.vmedia_repo_created` is False, skip all steps.
2. Re-upload the ISO from the ST18 HTTP server: `hmc_upload_iso(vios_uuid, vg_uuid,
   media_name="ubuntu-26.04-test.iso", iso_source=<url>)`.
   Update `context.vmedia_iso_name`. Record PASS/FAIL. Skip remainder on FAIL.
3. Power off lp3 (it may already be off after ST15):
   `hmc_power_off_lpar(lpar_name_or_uuid="ltczz386-lp3", immediate=True, wait=True)`.
   Use `_record_expected_or_real` to treat "already powered off" / "not activated" as PASS.
4. Mount the ISO: `hmc_mount_optical_media(vios_uuid,
   media_name=context.vmedia_iso_name, lpar_name_or_uuid="ltczz386-lp3")`.
   Capture mapping UUID into `context.vmedia_mapping_uuid`. Record PASS/FAIL.
5. Read current boot order: `hmc_read_lpar_boot_order(system_name_or_uuid="ltczz386",
   lpar_uuid=context.lp3_uuid)`. Save `pending_boot_string` / `boot_device_list` into
   `context.vmedia_orig_boot_order`. Record PASS/FAIL.
6. Set CD-first boot order: `hmc_set_lpar_boot_order(system_name_or_uuid="ltczz386",
   lpar_uuid=context.lp3_uuid, devices=["cd", "network", "disk"])`. Record PASS/FAIL.
7. Power on lp3: `hmc_power_on_lpar(lpar_name_or_uuid="ltczz386-lp3",
   wait=True, timeout=120)`. Record PASS/FAIL.
   Note: the Ubuntu ISO boots to a live-server installer — no OS install occurs; the goal
   is to confirm the partition reaches `Running` state from the CD device.
8. Verify state: `hmc_lpar_summary(lpar_name_or_uuid="ltczz386-lp3")`. Assert `state` is
   `Running`. Record as PASS/FAIL.
9. Power off lp3 immediately: `hmc_power_off_lpar(lpar_name_or_uuid="ltczz386-lp3",
   immediate=True, wait=True)`. Record PASS/FAIL.
10. Unmount optical media: `hmc_unmount_optical_media(vios_uuid,
    mapping_uuid=context.vmedia_mapping_uuid)`. Record PASS/FAIL.
    Clear `context.vmedia_mapping_uuid` on PASS.
11. Restore boot order:
    - If `context.vmedia_orig_boot_order` is empty, call `hmc_clear_lpar_boot_order(...)`.
    - Otherwise call `hmc_set_lpar_boot_order(..., devices=context.vmedia_orig_boot_order)`.
    - Record PASS/FAIL. Clear `context.vmedia_orig_boot_order` on PASS.
12. Verify restored boot order: `hmc_read_lpar_boot_order(...)`. Record result for audit.

**Relevant Context:**
- `hmc_power_off_lpar`, `hmc_power_on_lpar` MCP tools (both accept `wait=True`)
- `hmc_read_lpar_boot_order`, `hmc_set_lpar_boot_order`, `hmc_clear_lpar_boot_order`
  all take `system_name_or_uuid` and `lpar_uuid` (UUID, not name)
- Valid `devices` values: `"cd"`, `"disk"`, `"network"` (from `BOOT_DEVICE_SELECTORS`)
- Boot-order changes take effect on next activation — no reboot needed before power-on
- The Ubuntu live-server ISO boots to the installer; reaching `Running` state is sufficient
  proof of CD boot

**Status:** `[x] done`

---

### ST21 — List Storage Mappings Cross-Validation

**Intent:** After all mounts are cleaned up (end of ST20), exercise
`hmc_list_storage_mappings` and `hmc_list_optical_mappings` in isolation to verify they
return correct results at rest. This confirms the optical-only filter in
`list_optical_mappings` correctly excludes disk mappings, and that the lpar-scoped filter
path works.

**Expected Outcomes:**
- `hmc_list_storage_mappings(vios)` returns at least one mapping (lp3's disk)
- `hmc_list_optical_mappings(vios)` returns an empty list (no mounts active)
- `hmc_list_storage_mappings(vios, lpar="ltczz386-lp3")` returns lp3-scoped disk mappings
- `hmc_list_optical_mappings(vios, lpar="ltczz386-lp3")` returns empty list

**Todo List:**
1. Guard: if `context.vios_uuid` is None, skip all steps.
2. `hmc_list_storage_mappings(vios_uuid)`. Record PASS/FAIL. Assert list (empty OK on
   minimal setups).
3. `hmc_list_optical_mappings(vios_uuid)`. Record PASS/FAIL. Assert empty list.
4. `hmc_list_storage_mappings(vios_uuid,
   lpar_name_or_uuid="ltczz386-lp3")`. Record PASS/FAIL.
5. `hmc_list_optical_mappings(vios_uuid,
   lpar_name_or_uuid="ltczz386-lp3")`. Record PASS/FAIL. Assert empty.

**Relevant Context:**
- `hmc_list_storage_mappings`, `hmc_list_optical_mappings` MCP tools (READ_ONLY)
- `list_optical_mappings` filters by `BackingDeviceType == "VirtualOpticalMedia"`

**Status:** `[x] done`

---

### ST22 — Teardown: Unmount Orphans → Delete ISO → Delete Repository

**Intent:** Idempotent cleanup: unmount any active optical mappings, delete any remaining
media, then delete the media repository. This runs even if earlier sub-tasks failed
partially, ensuring the system is left clean. Also restores lp3's boot order if the
ST20 restore step was skipped.

**Expected Outcomes:**
- Any active optical mappings on the VIOS are unmounted
- Any remaining optical media in the repository are deleted
- `hmc_delete_media_repository` succeeds (or is already gone)
- `hmc_get_media_repository` returns None/empty
- lp3's boot order is restored if `context.vmedia_orig_boot_order` is non-empty
- `hmc_list_volume_groups` final audit is recorded

**Todo List:**
1. **Boot order restore guard:** if `context.vmedia_orig_boot_order` is non-empty (ST20
   restore was skipped), call `hmc_set_lpar_boot_order(...)` or
   `hmc_clear_lpar_boot_order(...)` to restore it. Record PASS/FAIL.
2. **Orphan mapping cleanup:** `hmc_list_optical_mappings(vios_uuid)`. For each mapping
   in the result, call `hmc_unmount_optical_media(vios_uuid, mapping_uuid)`. If list is
   empty, record SKIP "no orphan mappings".
3. **Media cleanup:** if `context.vg_uuid` is set, call
   `hmc_list_optical_media(vios_uuid, vg_uuid)`. For each entry, call
   `hmc_delete_optical_media(vios_uuid, vg_uuid, media_name)`.
4. **Repository delete:** call `hmc_delete_media_repository(vios_uuid, vg_uuid)`.
   Use `_record_expected_or_real` to treat "not found" / "does not exist" as SKIP
   (already cleaned up).
5. **Confirm gone:** `hmc_get_media_repository(vios_uuid, vg_uuid)`. Expect None/empty.
6. **Final VG audit:** `hmc_list_volume_groups(vios_uuid)`. Record for comparison with
   ST16 baseline free-space.

**Relevant Context:**
- All cleanup calls must be tolerant of "already gone" responses; use
  `_record_expected_or_real` with appropriate expected-fail substrings
  (`"not found"`, `"does not exist"`, `"no repository"`)
- `context.vmedia_mapping_uuid` and `context.vmedia_iso_name` are cleared by earlier
  sub-tasks on success; ST22 uses the live list calls rather than relying on context values

**Status:** `[x] done`

---

## Implementation Notes

### `live_test_runner.py` Changes

Four changes are needed to the existing file:

1. **`LiveTestContext` dataclass** — add the four new fields listed above.

2. **`SUBTASKS` dict** — add entries `16` through `22` pointing to the new async functions.

3. **`SUBTASK_GROUPS` dict** — new constant:
   ```python
   SUBTASK_GROUPS: dict[str, list[int]] = {
       "round2": list(range(0, 16)),
       "vmedia": list(range(16, 23)),
       "all":    list(range(0, 23)),
   }
   ```

4. **`main()` and CLI argument parsing** — extend to accept `--group <name>` in addition
   to the existing positional subtask number. When `--group` is given, the results file
   defaults to `test-results-<group>.json`. Pass the selected subtask list to the run loop.
   The existing single-number behaviour is unchanged.

### HTTP Server for ST18

Use Python stdlib only — no Flask dependency:

```python
import http.server, threading, functools

handler = functools.partial(
    http.server.SimpleHTTPRequestHandler,
    directory=str(Path.home() / "Downloads"),
)
server = http.server.HTTPServer(("localhost", 18765), handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
# ... run the upload call ...
server.shutdown()
```

The upload tool's HTTP path uses `httpx` with `READ_TIMEOUT=300s` and does not follow
redirects (ADR 0050). The server's host must also be on the operator's allowlist —
`HMC_ISO_URL_ALLOWLIST=localhost:18765` — which the runner sets for its own run in
`_allow_iso_host()`; without it every upload is refused.

### VG Free-Space Field Names

HMC firmware varies. ST16 tries these field names in order:
`FreeSpace`, `FreeSpaceInMBytes`, `free_space`, `FreeSpaceInMegabytes`.
If none match, free space is treated as "unknown" and creation is attempted anyway.

### Multi-VIOS Gap

`hmc_list_vios` already returns all VIOSes on a system as a list. The new sub-tasks pick
the first entry when scoped to `system_name_or_uuid="ltczz386"`. For ltczz386 (single
VIOS) this is correct. No code changes are needed for multi-VIOS systems — the first-entry
heuristic is the same pattern used throughout the existing runner. A future gap to note:
if a system has multiple VIOSes and only one has a media repository, the discovery logic
would need to iterate and probe. That is out of scope for this test round.

### Boot Order: `hmc_read_lpar_boot_order` / `hmc_set_lpar_boot_order` / `hmc_clear_lpar_boot_order`

All three tools take `system_name_or_uuid` (string name OK) and `lpar_uuid` (UUID required,
not name). ST20 must use `context.lp3_uuid` (captured in ST16), not the LPAR name.

Valid `devices` elements: `"cd"`, `"disk"`, `"network"`.

The restore in step 11 uses `hmc_clear_lpar_boot_order` when `vmedia_orig_boot_order` is
empty (meaning the LPAR had no explicit pending boot string set before the test), and
`hmc_set_lpar_boot_order` when a non-empty list was captured.

### Protected LPAR Guard

ST20 steps 4 and 7 (mount/power-on) must assert `lpar_name_or_uuid != "ltczz386-lp1"`
and `!= "ltczz386-lp2"` before any mutating call. The assertion is a safety belt only —
the code never constructs calls targeting lp1 or lp2.
