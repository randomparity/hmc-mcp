"""Shared virtual-adapter vocabulary and validation."""

from typing import Literal, get_args

AdapterType = Literal[
    "ClientNetworkAdapter",
    "VirtualSCSIClientAdapter",
    "VirtualFibreChannelClientAdapter",
    "VirtualNICDedicated",
]
ADAPTER_TYPES = frozenset(get_args(AdapterType))


def validate_adapter_type(adapter_type: AdapterType) -> AdapterType:
    if adapter_type not in ADAPTER_TYPES:
        raise ValueError(
            f"Invalid adapter_type {adapter_type!r}. "
            f"Must be one of: {', '.join(sorted(ADAPTER_TYPES))}"
        )
    return adapter_type
