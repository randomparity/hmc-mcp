"""Shared validation protocols for PCIe and vNIC operations."""

from __future__ import annotations

from decimal import Decimal

_COMMAND_STRUCTURAL_CHARACTERS = {
    "/": "slash",
    ",": "comma",
    "=": "equals sign",
    '"': "double quote",
}


def require_nonblank_text(value: str, name: str) -> str:
    """Require text without imposing the stronger HMC-command protocol."""
    if not value.strip():
        raise ValueError(f"{name} must not be blank")
    return value


def require_command_safe_text(value: str, name: str) -> str:
    """Require text that cannot alter an HMC command's field structure."""
    require_nonblank_text(value, name)
    for character, label in _COMMAND_STRUCTURAL_CHARACTERS.items():
        if character in value:
            raise ValueError(
                f"{name} contains {label}; it would alter HMC command structure"
            )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{name} contains a control character")
    return value


def validate_capacity_percent(value: Decimal) -> Decimal:
    """Require a finite percentage from 1 through 100 with two decimal places."""
    if not value.is_finite() or value < 1 or value > 100:
        raise ValueError("capacity_percent must be between 1 and 100")
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2:
        raise ValueError("capacity_percent supports at most two decimal places")
    return value
