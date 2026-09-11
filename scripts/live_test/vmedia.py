"""Virtual-media scenarios and their local HTTP fixture."""

from __future__ import annotations

import functools
import http.server
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from fastmcp import Client

from hmc_mcp.config import env_var_value

from .observation import ExpectedOutcome
from .results import entries
from .results import resource as get_resource

_ALREADY_POWERED_OFF = ExpectedOutcome(
    operation="lpar.power_off",
    variant="pre-boot-power-off",
    transient=True,
    reason="lp3 already powered off (expected)",
    error_codes=frozenset(
        {"already", "not activated", "powered off", "not running"}
    ),
)
_REPOSITORY_ALREADY_GONE = ExpectedOutcome(
    operation="media.delete_repository",
    variant="repository-removal",
    transient=True,
    reason="repository already gone (expected on re-run)",
    error_codes=frozenset(
        {"not found", "does not exist", "no repository", "no media"}
    ),
)

if TYPE_CHECKING:
    from live_test_runner import LiveTestConfig, RunState


async def _discover_vmedia_prerequisites(client: Client, state: RunState) -> bool:
    """Resolve the VIOS and test-partition identities required by ST16."""
    config = state.config
    artifacts = state.artifacts
    if not artifacts.vios_uuid:
        st, data = await state.call(
            client, "hmc_list_vios", system_name_or_uuid=config.system_name
        )
        state.record(16, "hmc_list_vios", st, data)
        if st == "PASS":
            for e in entries(data):
                resource = get_resource(e)
                uuid = e.get("UUID") or e.get("uuid")
                pid = resource.get("PartitionID") or resource.get("partition_id")
                if uuid:
                    artifacts.vios_uuid = uuid
                    artifacts.vios_partition_id = int(pid) if pid is not None else None
                    break
    else:
        print(f"  ℹ  vios_uuid already set: {artifacts.vios_uuid}")

    # lp3 UUID (needed by ST20 boot-order tools which require UUID not name)
    if not artifacts.lp3_uuid:
        st, data = await state.call(
            client, "hmc_get_lpar", lpar_name_or_uuid=config.lp3_name
        )
        state.record(16, "hmc_get_lpar (lp3 uuid)", st, data)
        if st == "PASS" and isinstance(data, dict):
            artifacts.lp3_uuid = data.get("uuid") or data.get("UUID")
    else:
        print(f"  ℹ  lp3_uuid already set: {artifacts.lp3_uuid}")

    if not artifacts.vios_uuid:
        for name in [
            "hmc_list_volume_groups",
            "hmc_create_media_repository",
            "hmc_get_media_repository",
        ]:
            state.skip(16, name, "no VIOS UUID resolved")
        return False

    return True


async def _select_vmedia_volume_group(
    client: Client, state: RunState, repo_size_mib: int
) -> bool:
    """Select a VIOS volume group with enough space for the repository."""
    artifacts = state.artifacts

    # Step 2 — VG discovery + free-space check
    st, data = await state.call(
        client, "hmc_list_volume_groups", vios_name_or_uuid=artifacts.vios_uuid
    )
    state.record(16, "hmc_list_volume_groups", st, data)
    if st == "PASS":
        for vg in entries(data):
            resource = get_resource(vg)
            uuid = vg.get("UUID") or vg.get("uuid")
            if not artifacts.vg_uuid and uuid:
                artifacts.vg_uuid = uuid
            # Always read free space from the selected VG
            if uuid == artifacts.vg_uuid or not artifacts.vg_uuid:
                free_raw = (
                    resource.get("FreeSpace")
                    or resource.get("FreeSpaceInMBytes")
                    or resource.get("free_space")
                    or resource.get("FreeSpaceInMegabytes")
                )
                try:
                    free_mib = int(float(free_raw)) if free_raw is not None else None
                except (TypeError, ValueError):
                    free_mib = None
                print(f"  VG UUID: {artifacts.vg_uuid}  free space: {free_mib} MiB")
                if free_mib is not None and free_mib < repo_size_mib:
                    for name in [
                        "hmc_create_media_repository",
                        "hmc_get_media_repository",
                    ]:
                        state.skip(
                            16,
                            name,
                            f"insufficient free space: {free_mib} MiB < {repo_size_mib} MiB",
                        )
                    return False
                break

    if not artifacts.vg_uuid:
        for name in ["hmc_create_media_repository", "hmc_get_media_repository"]:
            state.skip(16, name, "no VG UUID resolved")
        return False

    return True


async def _create_and_confirm_vmedia_repository(
    client: Client, state: RunState, repo_size_mib: int
) -> None:
    """Create the ST16 repository and confirm that the HMC reports it."""
    artifacts = state.artifacts

    # Step 4 — Create repository
    st, data = await state.call(
        client,
        "hmc_create_media_repository",
        vios_name_or_uuid=artifacts.vios_uuid,
        vg_uuid=artifacts.vg_uuid,
        size_mib=repo_size_mib,
    )
    state.record(16, "hmc_create_media_repository", st, data)
    if st != "PASS":
        state.skip(16, "hmc_get_media_repository", "repository creation failed")
        return

    # Step 5 — Confirm repository exists
    st, data = await state.call(
        client,
        "hmc_get_media_repository",
        vios_name_or_uuid=artifacts.vios_uuid,
        vg_uuid=artifacts.vg_uuid,
    )
    state.record(16, "hmc_get_media_repository", st, data)
    if st == "PASS" and data:
        artifacts.vmedia_repo_created = True
        print("  ✅ Repository created — vmedia_repo_created=True")


async def vmedia_bootstrap_and_create_repo(client: Client, state: RunState) -> None:
    print("\n=== ST16: VG Free-Space Check + Repository Create ===")
    repo_size_mib = state.config.vmedia_repository_size_mib

    if not await _discover_vmedia_prerequisites(client, state):
        return
    if not await _select_vmedia_volume_group(client, state, repo_size_mib):
        return
    await _create_and_confirm_vmedia_repository(client, state, repo_size_mib)


# ---------------------------------------------------------------------------
# ST17 — Short Repository Lifecycle (no ISO)
# ---------------------------------------------------------------------------


async def vmedia_short_repo_lifecycle(client: Client, state: RunState) -> None:
    config = state.config
    artifacts = state.artifacts
    print("\n=== ST17: Short Repository Lifecycle (no ISO) ===")

    if not artifacts.vmedia_repo_created:
        for name in [
            "hmc_delete_media_repository (main)",
            "hmc_create_media_repository (small)",
            "hmc_get_media_repository (small)",
            "hmc_list_optical_media (empty)",
            "hmc_delete_media_repository (small)",
            "hmc_get_media_repository (confirm gone)",
            "hmc_create_media_repository (restore main)",
        ]:
            state.skip(17, name, "vmedia_repo_created=False (ST16 failed)")
        return

    vios = artifacts.vios_uuid
    vg = artifacts.vg_uuid

    # Step 2 — Delete the ST16 main repository
    st, data = await state.call(
        client,
        "hmc_delete_media_repository",
        vios_name_or_uuid=vios,
        vg_uuid=vg,
    )
    state.record(17, "hmc_delete_media_repository (main)", st, data)

    # Step 3 — Create small repository
    st, data = await state.call(
        client,
        "hmc_create_media_repository",
        vios_name_or_uuid=vios,
        vg_uuid=vg,
        size_mib=config.vmedia_short_repository_size_mib,
    )
    state.record(17, "hmc_create_media_repository (small)", st, data)

    # Step 4 — Verify small repository
    st, data = await state.call(
        client,
        "hmc_get_media_repository",
        vios_name_or_uuid=vios,
        vg_uuid=vg,
    )
    state.record(17, "hmc_get_media_repository (small)", st, data)

    # Step 5 — List optical media (must be empty)
    st, data = await state.call(
        client,
        "hmc_list_optical_media",
        vios_name_or_uuid=vios,
        vg_uuid=vg,
    )
    state.record(17, "hmc_list_optical_media (empty)", st, data)

    # Step 6 — Delete small repository
    st, data = await state.call(
        client,
        "hmc_delete_media_repository",
        vios_name_or_uuid=vios,
        vg_uuid=vg,
    )
    state.record(17, "hmc_delete_media_repository (small)", st, data)

    # Step 7 — Confirm gone
    st, data = await state.call(
        client,
        "hmc_get_media_repository",
        vios_name_or_uuid=vios,
        vg_uuid=vg,
    )
    state.record(17, "hmc_get_media_repository (confirm gone)", st, data)

    # Step 8 — Re-create main repository for subsequent sub-tasks
    st, data = await state.call(
        client,
        "hmc_create_media_repository",
        vios_name_or_uuid=vios,
        vg_uuid=vg,
        size_mib=config.vmedia_repository_size_mib,
    )
    state.record(17, "hmc_create_media_repository (restore main)", st, data)
    if st == "PASS":
        artifacts.vmedia_repo_created = True
    else:
        artifacts.vmedia_repo_created = False
        print("  ⚠  Failed to restore main repository — ST18–ST22 may be skipped")


# ---------------------------------------------------------------------------
# ST18 — ISO Upload via HTTP
# ---------------------------------------------------------------------------


class IsoHttpServer:
    """Invocation-owned HTTP fixture for virtual-media ISO uploads."""

    def __init__(self) -> None:
        self._server: http.server.HTTPServer | None = None

    def start(self, config: LiveTestConfig) -> None:
        """Start serving the configured ISO directory once for this invocation."""
        _allow_iso_host(config)
        if self._server is not None:
            return
        handler = functools.partial(
            http.server.SimpleHTTPRequestHandler,
            directory=str(Path(config.iso_path).parent),
        )
        self._server = http.server.HTTPServer(
            (config.iso_bind_host, config.iso_http_port), handler
        )
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        """Stop serving and release the listening socket."""
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None


def _allow_iso_host(config: LiveTestConfig) -> None:
    """Put this runner's own ISO server on ``HMC_ISO_URL_ALLOWLIST``.

    ADR 0050 made ``hmc_upload_iso`` refuse every URL whose host an operator has
    not named, including when nothing is named at all, so the runner has to
    configure the allowlist for its own run exactly as an operator would. The
    entry is appended to whatever the environment or ``.env`` already carries
    rather than replacing it, and it names the port as well as the host, so this
    permits the one server started below and no other loopback service.
    """
    # env_var_value reads whatever casing the operator exported, because that is
    # the one `HMCConfig` will resolve — an exact-case read dropped a case
    # variant's entries from the merged allowlist (#543).
    name = "HMC_ISO_URL_ALLOWLIST"
    configured = env_var_value(name) or ""
    entries = [entry.strip() for entry in configured.split(",") if entry.strip()]
    if config.iso_host in entries:
        return
    entries.append(config.iso_host)
    # The merged value has to be the one that reaches the field, so every other
    # casing goes first. Assigning to a key that already exists updates it in
    # place rather than moving it, so a variant inserted after the canonical name
    # would stay last in `os.environ` order and stay the one pydantic-settings
    # folds onto `iso_url_allowlist` — the runner would print an allowlist
    # carrying its own host while ADR 0050 refused every one of its uploads.
    # Removing the variants makes the canonical spelling the only spelling, which
    # is also what lets the guard above short-circuit a second call.
    for variant in [k for k in os.environ if k.lower() == name.lower() and k != name]:
        del os.environ[variant]
    os.environ[name] = ",".join(entries)
    print(f"  ℹ  {name}={os.environ[name]}")


def _prepare_iso_upload(state: RunState, skip_names: list[str]) -> bool:
    """Validate ST18 prerequisites and start its invocation-owned HTTP server."""
    config = state.config
    artifacts = state.artifacts
    if not artifacts.vmedia_repo_created:
        for name in skip_names:
            state.skip(18, name, "vmedia_repo_created=False (ST16/ST17 failed)")
        return False

    if not Path(config.iso_path).is_file():
        state.record(
            18,
            "iso_file_check",
            "FAIL",
            f"ISO not found: {config.iso_path}",
        )
        for name in skip_names:
            state.skip(18, name, f"ISO file missing: {config.iso_path}")
        return False

    state.record(18, "iso_file_check", "PASS", f"ISO found: {config.iso_path}")

    try:
        state.iso_http_server.start(config)
    except OSError as exc:
        state.record(
            18,
            "iso_http_server",
            "FAIL",
            str(exc),
            f"HTTP server could not bind to port {config.iso_http_port}",
        )
        for name in skip_names:
            state.skip(18, name, f"no HTTP server on port {config.iso_http_port}")
        return False

    state.record(18, "iso_http_server", "PASS", f"serving {config.iso_url}")
    return True


async def _upload_and_discover_iso(client: Client, state: RunState) -> None:
    """Upload the ST18 ISO and capture the media name returned by the HMC."""
    config = state.config
    artifacts = state.artifacts
    print(f"  ⏳ Uploading ISO via HTTP ({config.iso_url}) — may take several minutes…")
    st, data = await state.call(
        client,
        "hmc_upload_iso",
        vios_name_or_uuid=artifacts.vios_uuid,
        vg_uuid=artifacts.vg_uuid,
        media_name=config.iso_media_name,
        iso_source=config.iso_url,
    )
    state.record(18, "hmc_upload_iso (http)", st, data)

    st, data = await state.call(
        client,
        "hmc_list_optical_media",
        vios_name_or_uuid=artifacts.vios_uuid,
        vg_uuid=artifacts.vg_uuid,
    )
    state.record(18, "hmc_list_optical_media (post-upload)", st, data)
    if st != "PASS":
        return
    for entry in entries(data) if isinstance(data, list) else []:
        name = get_resource(entry).get("MediaName") or entry.get("MediaName")
        if name:
            artifacts.vmedia_iso_name = name
            break
    if not artifacts.vmedia_iso_name and isinstance(data, list) and data:
        artifacts.vmedia_iso_name = data[0].get("MediaName") or config.iso_media_name


async def _verify_iso_deduplication(client: Client, state: RunState) -> None:
    """Re-upload the ST18 content and verify the broker deduplicates it."""
    config = state.config
    artifacts = state.artifacts
    print(f"  ⏳ Uploading ISO via HTTP ({config.iso_url}) again — expect dedup hit…")
    st_http, data_http = await state.call(
        client,
        "hmc_upload_iso",
        vios_name_or_uuid=artifacts.vios_uuid,
        vg_uuid=artifacts.vg_uuid,
        media_name=config.iso_http_media_name,
        iso_source=config.iso_url,
    )
    http_status = data_http.get("status") if isinstance(data_http, dict) else ""
    if st_http == "PASS" and http_status == "existing":
        state.record(
            18,
            "hmc_upload_iso (http dedup)",
            "PASS",
            data_http,
            "status=existing — deduplication fired as expected",
        )
    else:
        state.record(
            18,
            "hmc_upload_iso (http dedup)",
            st_http,
            data_http,
            f"expected status=existing, got status={http_status!r}",
        )

    st, data = await state.call(
        client,
        "hmc_list_optical_media",
        vios_name_or_uuid=artifacts.vios_uuid,
        vg_uuid=artifacts.vg_uuid,
    )
    state.record(18, "hmc_list_optical_media (post-http)", st, data)


async def _reset_iso_for_mount_scenario(client: Client, state: RunState) -> None:
    """Delete the ST18 media, confirm absence, then re-upload it for ST19."""
    config = state.config
    artifacts = state.artifacts
    st, data = await state.call(
        client,
        "hmc_delete_optical_media",
        vios_name_or_uuid=artifacts.vios_uuid,
        vg_uuid=artifacts.vg_uuid,
        media_name=config.iso_media_name,
    )
    state.record(18, "hmc_delete_optical_media", st, data)
    if st == "PASS":
        artifacts.vmedia_iso_name = None

    st, data = await state.call(
        client,
        "hmc_list_optical_media",
        vios_name_or_uuid=artifacts.vios_uuid,
        vg_uuid=artifacts.vg_uuid,
    )
    state.record(18, "hmc_list_optical_media (confirm empty)", st, data)

    print("  ⏳ Re-uploading ISO for ST19 (may take several minutes)…")
    st, data = await state.call(
        client,
        "hmc_upload_iso",
        vios_name_or_uuid=artifacts.vios_uuid,
        vg_uuid=artifacts.vg_uuid,
        media_name=config.iso_media_name,
        iso_source=config.iso_url,
    )
    state.record(18, "hmc_upload_iso (re-upload for ST19)", st, data)
    if st == "PASS" and isinstance(data, dict):
        artifacts.vmedia_iso_name = data.get("media_name") or config.iso_media_name
    print(f"  vmedia_iso_name: {artifacts.vmedia_iso_name}")


async def vmedia_upload_iso(client: Client, state: RunState) -> None:
    print("\n=== ST18: ISO Upload via HTTP ===")
    skip_names = [
        "hmc_upload_iso (http)",
        "hmc_list_optical_media (post-upload)",
        "hmc_upload_iso (http dedup)",
        "hmc_list_optical_media (post-http)",
        "hmc_delete_optical_media",
        "hmc_list_optical_media (confirm empty)",
        "hmc_upload_iso (re-upload for ST19)",
    ]
    if not _prepare_iso_upload(state, skip_names):
        return

    await _upload_and_discover_iso(client, state)

    await _verify_iso_deduplication(client, state)
    await _reset_iso_for_mount_scenario(client, state)


# ---------------------------------------------------------------------------
# ST19 — Mount / Unmount + Safe-Delete Validation
# ---------------------------------------------------------------------------


async def _mount_vmedia_and_confirm(client: Client, state: RunState) -> None:
    """Mount the ST19 ISO and verify that its mapping is visible."""
    config = state.config
    artifacts = state.artifacts
    vios = artifacts.vios_uuid
    st, data = await state.call(
        client,
        "hmc_mount_optical_media",
        vios_name_or_uuid=vios,
        media_name=artifacts.vmedia_iso_name,
        lpar_name_or_uuid=config.lp3_name,
    )
    state.record(19, "hmc_mount_optical_media", st, data)
    if st == "PASS" and isinstance(data, dict):
        artifacts.vmedia_mapping_uuid = (
            data.get("ElementID")
            or data.get("UUID")
            or data.get("uuid")
            or data.get("mapping_uuid")
        )
        # Dig into Resource wrapper if present
        if not artifacts.vmedia_mapping_uuid:
            resource = data.get("Resource") or {}
            artifacts.vmedia_mapping_uuid = resource.get("ElementID") or resource.get(
                "UUID"
            )
    print(f"  mapping_uuid: {artifacts.vmedia_mapping_uuid}")

    # Step 3 — Confirm mapping visible in list
    st, data = await state.call(
        client,
        "hmc_list_optical_mappings",
        vios_name_or_uuid=vios,
        lpar_name_or_uuid=config.lp3_name,
    )
    state.record(19, "hmc_list_optical_mappings (confirm mounted)", st, data)


async def _verify_mounted_media_delete_is_blocked(
    client: Client, state: RunState
) -> None:
    """Exercise and record the safe-delete guard while the ISO is mounted."""
    artifacts = state.artifacts
    st_del, data_del = await state.call(
        client,
        "hmc_delete_optical_media",
        vios_name_or_uuid=artifacts.vios_uuid,
        vg_uuid=artifacts.vg_uuid,
        media_name=artifacts.vmedia_iso_name,
    )
    rejection_text = str(data_del).lower()
    if st_del == "FAIL" and any(
        s in rejection_text for s in ["mapped", "in use", "mapping", "mount"]
    ):
        state.record(
            19,
            "hmc_delete_optical_media (blocked — expected)",
            "PASS",
            data_del,
            "safe-delete guard fired correctly",
        )
    else:
        state.record(
            19,
            "hmc_delete_optical_media (blocked — expected)",
            st_del,
            data_del,
            f"expected rejection, got st={st_del}",
        )


async def _unmount_and_delete_vmedia(client: Client, state: RunState) -> None:
    """Unmount the ST19 ISO, delete it, and confirm the repository is empty."""
    config = state.config
    artifacts = state.artifacts
    vios = artifacts.vios_uuid
    vg = artifacts.vg_uuid
    # The tool resolves the mapping from the LPAR and media name, so the captured
    # mapping UUID is only the marker that a mount happened.
    if artifacts.vmedia_mapping_uuid and artifacts.vmedia_iso_name:
        st, data = await state.call(
            client,
            "hmc_unmount_optical_media",
            vios_name_or_uuid=vios,
            lpar_name_or_uuid=config.lp3_name,
            media_name=artifacts.vmedia_iso_name,
        )
        state.record(19, "hmc_unmount_optical_media", st, data)
        if st == "PASS":
            artifacts.vmedia_mapping_uuid = None
    else:
        state.skip(19, "hmc_unmount_optical_media", "no mounted media captured")

    # Step 6 — Confirm mapping gone
    st, data = await state.call(
        client,
        "hmc_list_optical_mappings",
        vios_name_or_uuid=vios,
        lpar_name_or_uuid=config.lp3_name,
    )
    state.record(19, "hmc_list_optical_mappings (confirm unmounted)", st, data)

    # Step 7 — Delete media (now unmounted, safe-delete allows it)
    st, data = await state.call(
        client,
        "hmc_delete_optical_media",
        vios_name_or_uuid=vios,
        vg_uuid=vg,
        media_name=artifacts.vmedia_iso_name,
    )
    state.record(19, "hmc_delete_optical_media (post-unmount)", st, data)
    if st == "PASS":
        artifacts.vmedia_iso_name = None

    # Step 8 — Confirm empty
    st, data = await state.call(
        client,
        "hmc_list_optical_media",
        vios_name_or_uuid=vios,
        vg_uuid=vg,
    )
    state.record(19, "hmc_list_optical_media (confirm empty)", st, data)


async def vmedia_mount_unmount(client: Client, state: RunState) -> None:
    artifacts = state.artifacts
    print("\n=== ST19: Mount / Unmount + Safe-Delete Validation ===")
    skip_names = [
        "hmc_mount_optical_media",
        "hmc_list_optical_mappings (confirm mounted)",
        "hmc_delete_optical_media (blocked — expected)",
        "hmc_unmount_optical_media",
        "hmc_list_optical_mappings (confirm unmounted)",
        "hmc_delete_optical_media (post-unmount)",
        "hmc_list_optical_media (confirm empty)",
    ]
    if not artifacts.vmedia_iso_name:
        for name in skip_names:
            state.skip(19, name, "vmedia_iso_name not set (ST18 failed)")
        return

    await _mount_vmedia_and_confirm(client, state)
    await _verify_mounted_media_delete_is_blocked(client, state)
    await _unmount_and_delete_vmedia(client, state)


# ---------------------------------------------------------------------------
# ST20 — Boot Verification: Power Off → CD Boot → Power On → Verify → Restore
# ---------------------------------------------------------------------------


async def _prepare_boot_media(
    client: Client,
    state: RunState,
    vios_uuid: str,
    vg_uuid: str,
    remaining_steps: list[str],
) -> bool:
    """Upload and mount the boot ISO after placing the test LPAR offline."""
    config = state.config
    artifacts = state.artifacts
    print("  ⏳ Re-uploading ISO for boot test (may take several minutes)…")
    try:
        state.iso_http_server.start(config)
    except OSError as exc:
        for name in remaining_steps:
            state.skip(
                20, name, f"no HTTP server on port {config.iso_http_port}: {exc}"
            )
        return False

    status, data = await state.call(
        client,
        "hmc_upload_iso",
        vios_name_or_uuid=vios_uuid,
        vg_uuid=vg_uuid,
        media_name=config.iso_media_name,
        iso_source=config.iso_url,
    )
    state.record(20, "hmc_upload_iso (re-upload for boot test)", status, data)
    if status != "PASS":
        for name in remaining_steps[1:]:
            state.skip(20, name, "ISO re-upload failed")
        return False
    if isinstance(data, dict):
        artifacts.vmedia_iso_name = data.get("media_name") or config.iso_media_name

    status, data = await state.call(
        client,
        "hmc_power_off_lpar",
        lpar_name_or_uuid=config.lp3_name,
        immediate=True,
        wait=True,
        expected=[_ALREADY_POWERED_OFF],
    )
    state.record_with_expected(
        20, "hmc_power_off_lpar (pre-boot)", status, data, [_ALREADY_POWERED_OFF]
    )

    status, data = await state.call(
        client,
        "hmc_mount_optical_media",
        vios_name_or_uuid=vios_uuid,
        media_name=artifacts.vmedia_iso_name,
        lpar_name_or_uuid=config.lp3_name,
    )
    state.record(20, "hmc_mount_optical_media (boot test)", status, data)
    if status != "PASS":
        for name in remaining_steps[3:]:
            state.skip(20, name, "mount failed")
        return False
    if isinstance(data, dict):
        resource = data.get("Resource") or {}
        artifacts.vmedia_mapping_uuid = (
            data.get("ElementID")
            or data.get("UUID")
            or data.get("uuid")
            or data.get("mapping_uuid")
            or resource.get("ElementID")
            or resource.get("UUID")
        )
    return True


async def _configure_boot_order(
    client: Client, state: RunState, lpar_uuid: str
) -> None:
    """Capture the current boot order and replace it with the boot-test order."""
    config = state.config
    artifacts = state.artifacts
    status, data = await state.call(
        client,
        "hmc_read_lpar_boot_order",
        system_name_or_uuid=config.system_name,
        lpar_name_or_uuid=lpar_uuid,
    )
    state.record(20, "hmc_read_lpar_boot_order (baseline)", status, data)
    if status == "PASS" and isinstance(data, dict):
        pending = data.get("pending_boot_string") or ""
        artifacts.vmedia_orig_boot_order = [
            device.strip() for device in pending.split(",") if device.strip()
        ]

    status, data = await state.call(
        client,
        "hmc_set_lpar_boot_order",
        system_name_or_uuid=config.system_name,
        lpar_name_or_uuid=lpar_uuid,
        devices=["cd", "network", "disk"],
    )
    state.record(20, "hmc_set_lpar_boot_order (cd first)", status, data)


async def _run_boot_probe(client: Client, state: RunState) -> None:
    """Boot the test partition, report its state, and power it off again."""
    config = state.config
    status, data = await state.call(
        client,
        "hmc_power_on_lpar",
        lpar_name_or_uuid=config.lp3_name,
        wait=True,
        timeout_seconds=120,
    )
    state.record(20, "hmc_power_on_lpar", status, data)

    status, data = await state.call(
        client, "hmc_lpar_summary", lpar_name_or_uuid=config.lp3_name
    )
    state.record(20, "hmc_lpar_summary (verify running)", status, data)
    if status == "PASS" and isinstance(data, dict):
        lpar_state = data.get("state") or ""
        icon = "✅" if lpar_state.lower() == "running" else "⚠️"
        print(f"  {icon} lp3 state: {lpar_state}")

    status, data = await state.call(
        client,
        "hmc_power_off_lpar",
        lpar_name_or_uuid=config.lp3_name,
        immediate=True,
        wait=True,
    )
    state.record(20, "hmc_power_off_lpar (post-boot)", status, data)


async def _restore_boot_configuration(
    client: Client, state: RunState, vios_uuid: str, lpar_uuid: str
) -> None:
    """Unmount the test ISO, restore boot order, and verify the restored state."""
    config = state.config
    artifacts = state.artifacts
    if artifacts.vmedia_mapping_uuid and artifacts.vmedia_iso_name:
        status, data = await state.call(
            client,
            "hmc_unmount_optical_media",
            vios_name_or_uuid=vios_uuid,
            lpar_name_or_uuid=config.lp3_name,
            media_name=artifacts.vmedia_iso_name,
        )
        state.record(20, "hmc_unmount_optical_media (boot test cleanup)", status, data)
        if status == "PASS":
            artifacts.vmedia_mapping_uuid = None
    else:
        state.skip(
            20,
            "hmc_unmount_optical_media (boot test cleanup)",
            "no mounted media to unmount",
        )

    if artifacts.vmedia_orig_boot_order:
        status, data = await state.call(
            client,
            "hmc_set_lpar_boot_order",
            system_name_or_uuid=config.system_name,
            lpar_name_or_uuid=lpar_uuid,
            devices=artifacts.vmedia_orig_boot_order,
        )
    else:
        status, data = await state.call(
            client,
            "hmc_clear_lpar_boot_order",
            system_name_or_uuid=config.system_name,
            lpar_name_or_uuid=lpar_uuid,
        )
    state.record(20, "hmc_set_lpar_boot_order (restore)", status, data)
    if status == "PASS":
        artifacts.vmedia_orig_boot_order = []

    status, data = await state.call(
        client,
        "hmc_read_lpar_boot_order",
        system_name_or_uuid=config.system_name,
        lpar_name_or_uuid=lpar_uuid,
    )
    state.record(20, "hmc_read_lpar_boot_order (verify restore)", status, data)


async def vmedia_boot_verification(client: Client, state: RunState) -> None:
    config = state.config
    artifacts = state.artifacts
    print(
        "\n=== ST20: Boot Verification: Power Off → CD Boot → Power On → Verify → Restore ==="
    )

    _skip_names = [
        "hmc_upload_iso (re-upload for boot test)",
        "hmc_power_off_lpar (pre-boot)",
        "hmc_mount_optical_media (boot test)",
        "hmc_read_lpar_boot_order (baseline)",
        "hmc_set_lpar_boot_order (cd first)",
        "hmc_power_on_lpar",
        "hmc_lpar_summary (verify running)",
        "hmc_power_off_lpar (post-boot)",
        "hmc_unmount_optical_media (boot test cleanup)",
        "hmc_set_lpar_boot_order (restore)",
        "hmc_read_lpar_boot_order (verify restore)",
    ]

    if not artifacts.vmedia_repo_created:
        for name in _skip_names:
            state.skip(20, name, "vmedia_repo_created=False (ST16 failed)")
        return

    # Safety belt — never touch protected LPARs, even under ``python -O``.
    if config.lp3_name in config.protected_lpar_names:
        raise ValueError(f"ST20 refuses to mutate protected LPAR {config.lp3_name!r}")

    vios = artifacts.vios_uuid
    vg = artifacts.vg_uuid
    lp3_uuid = artifacts.lp3_uuid

    if not lp3_uuid:
        for name in _skip_names:
            state.skip(20, name, "lp3_uuid not set (ST16 failed to capture it)")
        return

    if not await _prepare_boot_media(client, state, str(vios), str(vg), _skip_names):
        return
    await _configure_boot_order(client, state, str(lp3_uuid))
    await _run_boot_probe(client, state)
    await _restore_boot_configuration(client, state, str(vios), str(lp3_uuid))


# ---------------------------------------------------------------------------
# ST21 — List Storage Mappings Cross-Validation
# ---------------------------------------------------------------------------


async def vmedia_mapping_crossvalidation(client: Client, state: RunState) -> None:
    config = state.config
    artifacts = state.artifacts
    print("\n=== ST21: List Storage Mappings Cross-Validation ===")

    if not artifacts.vios_uuid:
        for name in [
            "hmc_list_storage_mappings (all)",
            "hmc_list_optical_mappings (all)",
            "hmc_list_storage_mappings (lp3)",
            "hmc_list_optical_mappings (lp3)",
        ]:
            state.skip(21, name, "no VIOS UUID in context")
        return

    vios = artifacts.vios_uuid

    st, data = await state.call(
        client,
        "hmc_list_storage_mappings",
        vios_name_or_uuid=vios,
    )
    state.record(21, "hmc_list_storage_mappings (all)", st, data)

    st, data = await state.call(
        client,
        "hmc_list_optical_mappings",
        vios_name_or_uuid=vios,
    )
    state.record(21, "hmc_list_optical_mappings (all)", st, data)

    st, data = await state.call(
        client,
        "hmc_list_storage_mappings",
        vios_name_or_uuid=vios,
        lpar_name_or_uuid=config.lp3_name,
    )
    state.record(21, "hmc_list_storage_mappings (lp3)", st, data)

    st, data = await state.call(
        client,
        "hmc_list_optical_mappings",
        vios_name_or_uuid=vios,
        lpar_name_or_uuid=config.lp3_name,
    )
    state.record(21, "hmc_list_optical_mappings (lp3)", st, data)


# ---------------------------------------------------------------------------
# ST22 — Teardown: Unmount Orphans → Delete ISO → Delete Repository
# ---------------------------------------------------------------------------


async def _restore_teardown_boot_order(client: Client, state: RunState) -> None:
    """Restore a saved boot order when ST20 did not finish its own cleanup."""
    config = state.config
    artifacts = state.artifacts
    if artifacts.vmedia_orig_boot_order and artifacts.lp3_uuid:
        st, data = await state.call(
            client,
            "hmc_set_lpar_boot_order",
            system_name_or_uuid=config.system_name,
            lpar_name_or_uuid=artifacts.lp3_uuid,
            devices=artifacts.vmedia_orig_boot_order,
        )
        state.record(22, "hmc_set_lpar_boot_order (boot order restore guard)", st, data)
        if st == "PASS":
            artifacts.vmedia_orig_boot_order = []
    elif not artifacts.vmedia_orig_boot_order:
        state.skip(
            22,
            "hmc_set_lpar_boot_order (boot order restore guard)",
            "no saved boot order to restore",
        )
    else:
        state.skip(
            22,
            "hmc_set_lpar_boot_order (boot order restore guard)",
            "lp3_uuid not available",
        )


def _mapping_identity(mapping: object) -> tuple[str, str] | None:
    """Read the LPAR and media name that address one optical mapping.

    ``hmc_unmount_optical_media`` resolves a mapping from the pair, so an entry
    missing either half cannot be unmounted and is skipped rather than guessed at.
    """
    if not isinstance(mapping, dict):
        return None
    storage = mapping.get("Storage")
    optical = storage.get("VirtualOpticalMedia") if isinstance(storage, dict) else None
    media_name = optical.get("MediaName") if isinstance(optical, dict) else None
    partition = mapping.get("AssociatedLogicalPartition")
    href = partition.get("href") if isinstance(partition, dict) else None
    lpar = urlparse(href).path.rsplit("/", 1)[-1] if isinstance(href, str) else None
    if not isinstance(media_name, str) or not media_name or not lpar:
        return None
    return lpar, media_name


async def _remove_orphan_mappings(client: Client, state: RunState, vios: str) -> None:
    """Unmount every optical mapping left behind by earlier vMedia stages."""
    st, data = await state.call(
        client,
        "hmc_list_optical_mappings",
        vios_name_or_uuid=vios,
    )
    state.record(22, "hmc_list_optical_mappings (orphan cleanup)", st, data)
    if st == "PASS":
        mappings = data if isinstance(data, list) else []
        if not mappings:
            state.skip(22, "hmc_unmount_optical_media (orphan)", "no orphan mappings")
        for mapping in mappings:
            identity = _mapping_identity(mapping)
            if identity is None:
                continue
            lpar, media_name = identity
            st_u, data_u = await state.call(
                client,
                "hmc_unmount_optical_media",
                vios_name_or_uuid=vios,
                lpar_name_or_uuid=lpar,
                media_name=media_name,
            )
            state.record(
                22,
                f"hmc_unmount_optical_media (orphan {media_name})",
                st_u,
                data_u,
            )


async def _remove_optical_media(
    client: Client, state: RunState, vios: str, vg: str | None
) -> None:
    """Delete all optical media from the test repository when it exists."""
    if not vg:
        state.skip(22, "hmc_list_optical_media (media cleanup)", "no VG UUID")
        return
    st, data = await state.call(
        client,
        "hmc_list_optical_media",
        vios_name_or_uuid=vios,
        vg_uuid=vg,
    )
    state.record(22, "hmc_list_optical_media (media cleanup)", st, data)
    if st != "PASS":
        return
    for entry in data if isinstance(data, list) else []:
        media_name = entry.get("MediaName") or get_resource(entry).get("MediaName")
        if not media_name:
            continue
        st_d, data_d = await state.call(
            client,
            "hmc_delete_optical_media",
            vios_name_or_uuid=vios,
            vg_uuid=vg,
            media_name=media_name,
        )
        state.record(22, f"hmc_delete_optical_media ({media_name})", st_d, data_d)


async def _remove_repository_and_audit(
    client: Client, state: RunState, vios: str, vg: str | None
) -> None:
    """Remove the repository, confirm its absence, and capture final VG state."""
    if vg:
        st, data = await state.call(
            client,
            "hmc_delete_media_repository",
            vios_name_or_uuid=vios,
            vg_uuid=vg,
            expected=[_REPOSITORY_ALREADY_GONE],
        )
        state.record_with_expected(
            22,
            "hmc_delete_media_repository",
            st,
            data,
            [_REPOSITORY_ALREADY_GONE],
        )
        if st == "PASS":
            state.artifacts.vmedia_repo_created = False
        st, data = await state.call(
            client,
            "hmc_get_media_repository",
            vios_name_or_uuid=vios,
            vg_uuid=vg,
        )
        state.record(22, "hmc_get_media_repository (confirm gone)", st, data)
    else:
        state.skip(22, "hmc_delete_media_repository", "no VG UUID")
        state.skip(22, "hmc_get_media_repository (confirm gone)", "no VG UUID")

    st, data = await state.call(
        client,
        "hmc_list_volume_groups",
        vios_name_or_uuid=vios,
    )
    state.record(22, "hmc_list_volume_groups (final audit)", st, data)


async def vmedia_teardown(client: Client, state: RunState) -> None:
    """Restore boot state and remove every vMedia artifact in phase order."""
    artifacts = state.artifacts
    print("\n=== ST22: Teardown: Unmount Orphans → Delete ISO → Delete Repository ===")

    await _restore_teardown_boot_order(client, state)
    if not artifacts.vios_uuid:
        for name in [
            "hmc_list_optical_mappings (orphan cleanup)",
            "hmc_list_optical_media (media cleanup)",
            "hmc_delete_media_repository",
            "hmc_get_media_repository (confirm gone)",
            "hmc_list_volume_groups (final audit)",
        ]:
            state.skip(22, name, "no VIOS UUID in context")
        return

    await _remove_orphan_mappings(client, state, artifacts.vios_uuid)
    await _remove_optical_media(client, state, artifacts.vios_uuid, artifacts.vg_uuid)
    await _remove_repository_and_audit(
        client, state, artifacts.vios_uuid, artifacts.vg_uuid
    )
