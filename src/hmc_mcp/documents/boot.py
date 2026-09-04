# Domain modules use the common vocabulary and XML imports directly.
# ruff: noqa: F401,F403,F405,I001
from __future__ import annotations

from .common import *
from ..documents_shared import document_envelope, lpar_envelope
from ..xmlutil import escapes_string_arguments

def _build_pending_boot_string(devices: list[str]) -> str:
    """Join validated boot device selectors for ``PendingBootString``."""
    if not devices:
        raise ValueError("Boot order must contain at least one device")

    for device in devices:
        if device not in BOOT_DEVICE_SELECTORS:
            raise ValueError(
                f"Invalid boot device selector: {device!r}. "
                f"Must be one of: {BOOT_DEVICE_SELECTORS}"
            )

    return " ".join(devices)


@escapes_string_arguments
def build_boot_order_document(devices: list[str]) -> str:
    """Set boot-device priority for the LPAR's next activation."""
    pending_boot_string = _build_pending_boot_string(devices)

    body = f"""  <PendingBootString kb="CUR" kxe="false">{pending_boot_string}</PendingBootString>"""

    return lpar_envelope(body)


@escapes_string_arguments
def build_clear_boot_order_document() -> str:
    """Restore the HMC's default boot order on the LPAR's next activation."""
    body = """  <PendingBootString kb="CUR" kxe="false"></PendingBootString>"""

    return lpar_envelope(body)
