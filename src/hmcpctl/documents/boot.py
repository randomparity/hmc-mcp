"""Boot-order input validation for ``BootListInformation/PendingBootString``."""

from __future__ import annotations


def _is_joinable(path: str) -> bool:
    """A ``/``-rooted path of printable, non-space ASCII.

    Single-space joining can then neither split nor merge it, and every
    character is legal XML text.
    """
    return path.startswith("/") and all("!" <= ch <= "~" for ch in path)


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
                "read-boot-order reports, of printable ASCII with no whitespace"
            )
    return " ".join(paths)
