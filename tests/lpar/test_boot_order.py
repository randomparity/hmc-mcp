"""Tests for LPAR boot order operations."""

import pytest

from hmc_mcp.documents import (
    BootDeviceSelector,
    BOOT_DEVICE_SELECTORS,
    build_boot_order_document,
    build_clear_boot_order_document,
)


# ------------------------------------------------------------------ #
# build_boot_order_document unit tests
# ------------------------------------------------------------------ #


def test_build_boot_order_document_single_device():
    """Build a boot order document with a single device."""
    xml = build_boot_order_document(["cd"])
    assert "PendingBootString" in xml
    assert "cd" in xml
    assert "LogicalPartition" in xml
    assert 'PendingBootString kb="CUR" kxe="false">cd</PendingBootString>' in xml


def test_build_boot_order_document_multiple_devices():
    """Build a boot order document with multiple devices in order."""
    xml = build_boot_order_document(["network", "cd", "disk"])
    assert "PendingBootString" in xml
    assert "network cd disk" in xml
    assert "LogicalPartition" in xml


def test_build_boot_order_document_empty_list_raises():
    """Empty device list raises ValueError."""
    with pytest.raises(ValueError, match="Boot order must contain at least one device"):
        build_boot_order_document([])


def test_build_boot_order_document_invalid_device_raises():
    """Invalid device selector raises ValueError."""
    with pytest.raises(ValueError, match="Invalid boot device selector"):
        build_boot_order_document(["invalid"])


def test_build_boot_order_document_validates_all_devices():
    """All devices are validated against BOOT_DEVICE_SELECTORS."""
    with pytest.raises(ValueError, match="Invalid boot device selector"):
        build_boot_order_document(["cd", "invalid", "disk"])


def test_build_boot_order_document_preserves_order():
    """Device order is preserved in the generated XML."""
    xml = build_boot_order_document(["network", "cd", "disk"])
    # Find the PendingBootString element
    import re
    match = re.search(r'<PendingBootString[^>]*>(.*?)</PendingBootString>', xml)
    assert match is not None
    assert match.group(1) == "network cd disk"


def test_build_boot_order_document_uses_correct_namespace():
    """Document uses correct UOM namespace."""
    xml = build_boot_order_document(["cd"])
    assert 'xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/"' in xml


# ------------------------------------------------------------------ #
# build_clear_boot_order_document unit tests
# ------------------------------------------------------------------ #


def test_build_clear_boot_order_document():
    """Build a document to clear boot order."""
    xml = build_clear_boot_order_document()
    assert "PendingBootString" in xml
    assert "LogicalPartition" in xml
    assert 'PendingBootString kb="CUR" kxe="false"></PendingBootString>' in xml


def test_build_clear_boot_order_document_empty_string():
    """Clear boot order document contains empty PendingBootString."""
    xml = build_clear_boot_order_document()
    assert 'PendingBootString kb="CUR" kxe="false"></PendingBootString>' in xml


def test_build_clear_boot_order_document_uses_correct_namespace():
    """Clear document uses correct UOM namespace."""
    xml = build_clear_boot_order_document()
    assert 'xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/"' in xml


# ------------------------------------------------------------------ #
# BOOT_DEVICE_SELECTORS validation
# ------------------------------------------------------------------ #


def test_boot_device_selectors_contains_valid_types():
    """BOOT_DEVICE_SELECTORS contains the expected device types."""
    assert "cd" in BOOT_DEVICE_SELECTORS
    assert "disk" in BOOT_DEVICE_SELECTORS
    assert "network" in BOOT_DEVICE_SELECTORS


def test_boot_device_selectors_is_frozen():
    """BOOT_DEVICE_SELECTORS is a frozen tuple."""
    assert isinstance(BOOT_DEVICE_SELECTORS, tuple)
    assert len(BOOT_DEVICE_SELECTORS) == 3


def test_boot_device_selector_type_annotation():
    """BootDeviceSelector is properly typed."""
    # This is a type alias, so we just check it exists
    assert BootDeviceSelector is not None
    # Check it accepts the expected values
    valid_selectors: list[BootDeviceSelector] = ["cd", "disk", "network"]
    assert len(valid_selectors) == 3


def test_all_boot_device_selectors_are_valid():
    """All BOOT_DEVICE_SELECTORS values are valid BootDeviceSelector values."""
    for selector in BOOT_DEVICE_SELECTORS:
        assert selector in ("cd", "disk", "network")


# ------------------------------------------------------------------ #
# Boot order operations validation
# ------------------------------------------------------------------ #


def test_set_lpar_boot_order_validates_devices():
    """Setting boot order validates device selectors."""
    from hmc_mcp.documents import BOOT_DEVICE_SELECTORS

    # Test that each selector is valid
    for selector in BOOT_DEVICE_SELECTORS:
        # This should not raise
        from hmc_mcp.documents import _build_pending_boot_string
        result = _build_pending_boot_string([selector])
        assert selector in result


def test_set_lpar_boot_order_rejects_invalid_devices():
    """Setting boot order rejects invalid device selectors."""
    from hmc_mcp.documents import _build_pending_boot_string

    invalid_devices = ["invalid", "tape", "floppy", "invalid-device"]
    for device in invalid_devices:
        with pytest.raises(ValueError, match="Invalid boot device selector"):
            _build_pending_boot_string([device])


def test_boot_order_string_format():
    """PendingBootString is space-separated."""
    from hmc_mcp.documents import _build_pending_boot_string

    result = _build_pending_boot_string(["cd", "disk", "network"])
    assert result == "cd disk network"


def test_boot_order_single_device_format():
    """Single device PendingBootString has no spaces."""
    from hmc_mcp.documents import _build_pending_boot_string

    result = _build_pending_boot_string(["network"])
    assert result == "network"


def test_boot_order_string_no_extra_spaces():
    """No extra spaces in PendingBootString."""
    from hmc_mcp.documents import _build_pending_boot_string

    result = _build_pending_boot_string(["cd", "disk"])
    assert result == "cd disk"
    assert "  " not in result


def test_boot_order_string_order_preservation():
    """Device order is preserved in PendingBootString."""
    from hmc_mcp.documents import _build_pending_boot_string

    result = _build_pending_boot_string(["network", "cd", "disk"])
    assert result == "network cd disk"
    assert result.index("network") < result.index("cd")
    assert result.index("cd") < result.index("disk")
