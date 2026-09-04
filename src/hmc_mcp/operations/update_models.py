"""Compatibility imports for update request models.

The implementations live in :mod:`hmc_mcp.operations.updates.models`; this
module preserves the pre-0.1 import path for callers during the package move.
"""

from .updates.models import (
    _CONSOLE_UPDATE_MEDIA_TYPES,  # noqa: F401
    _VIOS_UPDATE_RESOURCE_TYPES,  # noqa: F401
    _VIOS_UPGRADE_RESOURCE_TYPES,  # noqa: F401
    ConsoleUpdateMediaType,
    ConsoleUpdateSource,
    IOAdapterUpdateModel,
    PlatformUpdateParameter,
    SriovAdapterUpdate,
    SystemFirmwareUpdateModel,
    VIOSPlatformUpdate,
    VIOSUpdateHMCSource,
    VIOSUpdateIBMWebsiteSource,
    VIOSUpdateNFSSource,
    VIOSUpdateSFTPSource,
    VIOSUpdateUSBSource,
    VIOSUpgradeHMCSource,
    VIOSUpgradeNFSSource,
    VIOSUpgradeSFTPSource,
    VIOSUpgradeUSBSource,
)

__all__ = [
    "ConsoleUpdateMediaType", "ConsoleUpdateSource", "IOAdapterUpdateModel",
    "PlatformUpdateParameter", "SriovAdapterUpdate", "SystemFirmwareUpdateModel",
    "VIOSPlatformUpdate", "VIOSUpdateHMCSource", "VIOSUpdateIBMWebsiteSource",
    "VIOSUpdateNFSSource", "VIOSUpdateSFTPSource", "VIOSUpdateUSBSource",
    "VIOSUpgradeHMCSource", "VIOSUpgradeNFSSource", "VIOSUpgradeSFTPSource",
    "VIOSUpgradeUSBSource",
]
