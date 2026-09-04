"""Public update operations and request models."""

from . import models as models
from . import service as service

ConsoleUpdateMediaType = models.ConsoleUpdateMediaType
ConsoleUpdateSource = models.ConsoleUpdateSource
IOAdapterUpdateModel = models.IOAdapterUpdateModel
PlatformUpdateParameter = models.PlatformUpdateParameter
SriovAdapterUpdate = models.SriovAdapterUpdate
SystemFirmwareUpdateModel = models.SystemFirmwareUpdateModel
VIOSPlatformUpdate = models.VIOSPlatformUpdate
VIOSUpdateHMCSource = models.VIOSUpdateHMCSource
VIOSUpdateIBMWebsiteSource = models.VIOSUpdateIBMWebsiteSource
VIOSUpdateNFSSource = models.VIOSUpdateNFSSource
VIOSUpdateSFTPSource = models.VIOSUpdateSFTPSource
VIOSUpdateSource = models.VIOSUpdateSource
VIOSUpdateUSBSource = models.VIOSUpdateUSBSource
VIOSUpgradeHMCSource = models.VIOSUpgradeHMCSource
VIOSUpgradeNFSSource = models.VIOSUpgradeNFSSource
VIOSUpgradeSFTPSource = models.VIOSUpgradeSFTPSource
VIOSUpgradeSource = models.VIOSUpgradeSource
VIOSUpgradeUSBSource = models.VIOSUpgradeUSBSource

submit_available_hmc_ptfs_query = service.submit_available_hmc_ptfs_query
update_console_software = service.update_console_software
update_firmware = service.update_firmware
update_vios = service.update_vios
upgrade_vios = service.upgrade_vios
