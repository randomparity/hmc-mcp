"""Contract tests for shared PCIe validation protocols."""

from decimal import Decimal

import pytest

from hmc_mcp.operations.pcie_validation import (
    require_command_safe_text,
    require_nonblank_text,
    validate_capacity_percent,
)


def test_nonblank_protocol_is_explicitly_weaker_than_command_safe_text() -> None:
    assert require_nonblank_text("profile/name", "profile_name") == "profile/name"
    with pytest.raises(ValueError, match="alter HMC command structure"):
        require_command_safe_text("profile/name", "profile_name")


@pytest.mark.parametrize(
    "value",
    [Decimal("NaN"), Decimal("0"), Decimal("100.01"), Decimal("1.001")],
)
def test_capacity_protocol_rejects_invalid_percentages(value: Decimal) -> None:
    with pytest.raises(ValueError, match="capacity_percent"):
        validate_capacity_percent(value)


def test_capacity_protocol_preserves_valid_decimal() -> None:
    value = Decimal("12.34")
    assert validate_capacity_percent(value) is value
