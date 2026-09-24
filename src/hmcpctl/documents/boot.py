"""Boot-order input validation for ``BootListInformation/PendingBootString``."""

from __future__ import annotations


def _is_joinable(path: str) -> bool:
    """A path that single-space joining can neither split nor merge with its neighbour."""
    return path.startswith("/") and not any(
        ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in path
    )


def join_boot_device_paths(paths: list[str]) -> str:
    """Join Open Firmware device paths, first to last, into a ``PendingBootString`` value.

    Not a ``build_*`` document builder: the value becomes element text through
    ElementTree, which is its only escaping point.
    """
    if not paths:
        raise ValueError("Boot order must contain at least one Open Firmware device path")
    for path in paths:
        if not _is_joinable(path):
            raise ValueError(
                f"Invalid boot device path: {path!r}. Give an Open Firmware device path "
                "such as '/vdevice/v-scsi@30000002/disk@8100000000000000', as "
                "read-boot-order reports, with no whitespace or control characters"
            )
    return " ".join(paths)
