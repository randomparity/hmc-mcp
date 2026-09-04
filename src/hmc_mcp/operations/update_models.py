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
    VIOSUpdateResourceType,
    VIOSUpdateSFTPSource,
    VIOSUpdateSource,
    VIOSUpdateUSBSource,
    VIOSUpgradeHMCSource,
    VIOSUpgradeNFSSource,
    VIOSUpgradeResourceType,
    VIOSUpgradeSFTPSource,
    VIOSUpgradeSource,
    VIOSUpgradeUSBSource,
    list_management_console_updates_job,
    platform_update_job,
    update_hmc_job,
    update_vios_job,
    upgrade_vios_job,
)

__all__ = [
    "ConsoleUpdateMediaType",
    "ConsoleUpdateSource",
    "IOAdapterUpdateModel",
    "PlatformUpdateParameter",
    "SriovAdapterUpdate",
    "SystemFirmwareUpdateModel",
    "VIOSPlatformUpdate",
    "VIOSUpdateHMCSource",
    "VIOSUpdateIBMWebsiteSource",
    "VIOSUpdateNFSSource",
    "VIOSUpdateResourceType",
    "VIOSUpdateSFTPSource",
    "VIOSUpdateSource",
    "VIOSUpdateUSBSource",
    "VIOSUpgradeHMCSource",
    "VIOSUpgradeNFSSource",
    "VIOSUpgradeResourceType",
    "VIOSUpgradeSFTPSource",
    "VIOSUpgradeSource",
    "VIOSUpgradeUSBSource",
    "list_management_console_updates_job",
    "platform_update_job",
    "update_hmc_job",
    "update_vios_job",
    "upgrade_vios_job",
]
