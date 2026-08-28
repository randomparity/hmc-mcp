"""Request schemas and payload builders for HMC and VIOS update jobs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal, NotRequired, Required, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing_extensions import TypedDict

from ..jobs import build_job_request



ConsoleUpdateMediaType = Literal[
    "USB", "NFS", "SFTP", "FTP", "IBMWebsite", "Disk", "VirtualMedia", "CDDVD"
]
_CONSOLE_UPDATE_MEDIA_TYPES = frozenset(get_args(ConsoleUpdateMediaType))
VIOSUpdateResourceType = Literal["HMC", "NFS", "SFTP", "USB", "IBMWebsite"]
VIOSUpgradeResourceType = Literal["HMC", "NFS", "SFTP", "USB"]
_VIOS_UPDATE_RESOURCE_TYPES = frozenset(get_args(VIOSUpdateResourceType))
_VIOS_UPGRADE_RESOURCE_TYPES = frozenset(get_args(VIOSUpgradeResourceType))


class ConsoleUpdateSource(TypedDict, total=False):
    """Documented parameters for ``UpdateManagementConsole``."""

    MediaType: Required[
        Annotated[
            ConsoleUpdateMediaType, Field(description="Location of the update image.")
        ]
    ]
    ServerHostOrIP: Annotated[str, Field(description="Remote server hostname or IP.")]
    UserName: Annotated[str, Field(description="Remote server username.")]
    Password: Annotated[str, Field(description="Remote server password.")]
    SFTPKey: Annotated[str, Field(description="SSH private key for SFTP.")]
    PassPhrase: Annotated[str, Field(description="SFTP private-key passphrase.")]
    Directory: Annotated[str, Field(description="HMC-local update-image directory.")]
    UpdateFile: Annotated[str, Field(description="Update image filename.")]
    MountLocation: Annotated[str, Field(description="NFS mount location.")]
    MountOptions: Annotated[str, Field(description="Additional NFS mount options.")]
    PTFNumber: Annotated[str, Field(description="PTF number for IBMWebsite.")]
    Device: Annotated[str, Field(description="USB, optical, or virtual-media device.")]
    RestartConsole: Annotated[
        Literal["True", "False"],
        Field(description="Restart the console after the update."),
    ]


_CONSOLE_UPDATE_KEYS = frozenset(ConsoleUpdateSource.__annotations__)

_VIOS_NAME = Annotated[str, Field(description="Name of the VIOS image.")]
_VIOS_SERVER = Annotated[str, Field(description="Remote server host or IP.")]
_VIOS_REMOTE_DIRECTORY = Annotated[str, Field(description="Remote image directory.")]
_VIOS_FILE_NAMES = Annotated[str, Field(description="Comma-separated image files.")]
_VIOS_MOUNT_LOCATION = Annotated[str, Field(description="NFS mount location.")]
_VIOS_MOUNT_OPTIONS = Annotated[str, Field(description="Additional NFS mount options.")]
_VIOS_USER = Annotated[str, Field(description="Remote SFTP user name.")]
_VIOS_PASSWORD = Annotated[str, Field(description="Remote SFTP password.")]
_VIOS_SSH_KEY = Annotated[str, Field(description="SSH private key for SFTP.")]
_VIOS_PASSPHRASE = Annotated[str, Field(description="SSH-key passphrase.")]
_VIOS_USB_DEVICE = Annotated[str, Field(description="USB device name.")]
_VIOS_DISKS = Annotated[
    str, Field(description="Comma-separated free physical volumes.")
]


class _VIOSOptionalSource(TypedDict, total=False):
    Name: _VIOS_NAME
    SaveFile: Annotated[str, Field(description="Save the remote image on the HMC.")]


class _VIOSUpdateOptional(_VIOSOptionalSource, total=False):
    RestartVIOS: Annotated[str, Field(description="Restart the VIOS after the update.")]


class VIOSUpdateHMCSource(TypedDict):
    ResourceType: Annotated[Literal["HMC"], Field(description="HMC image source.")]
    Name: _VIOS_NAME
    RestartVIOS: NotRequired[
        Annotated[str, Field(description="Restart the VIOS after the update.")]
    ]


class VIOSUpdateNFSSource(_VIOSUpdateOptional):
    ResourceType: Annotated[Literal["NFS"], Field(description="NFS image source.")]
    ServerHostOrIP: _VIOS_SERVER
    RemoteDirectory: _VIOS_REMOTE_DIRECTORY
    FileNames: NotRequired[_VIOS_FILE_NAMES]
    MountLocation: NotRequired[_VIOS_MOUNT_LOCATION]
    MountOptions: NotRequired[_VIOS_MOUNT_OPTIONS]


class VIOSUpdateSFTPSource(_VIOSUpdateOptional):
    ResourceType: Annotated[Literal["SFTP"], Field(description="SFTP image source.")]
    ServerHostOrIP: _VIOS_SERVER
    RemoteDirectory: _VIOS_REMOTE_DIRECTORY
    UserName: NotRequired[_VIOS_USER]
    Password: NotRequired[_VIOS_PASSWORD]
    SSHKey: NotRequired[_VIOS_SSH_KEY]
    PassPhrase: NotRequired[_VIOS_PASSPHRASE]
    FileNames: NotRequired[_VIOS_FILE_NAMES]


class VIOSUpdateUSBSource(_VIOSUpdateOptional):
    ResourceType: Annotated[Literal["USB"], Field(description="USB image source.")]
    USBDevice: _VIOS_USB_DEVICE


class VIOSUpdateIBMWebsiteSource(_VIOSUpdateOptional):
    ResourceType: Annotated[
        Literal["IBMWebsite"], Field(description="IBM website image source.")
    ]


VIOSUpdateSource = (
    VIOSUpdateHMCSource
    | VIOSUpdateNFSSource
    | VIOSUpdateSFTPSource
    | VIOSUpdateUSBSource
    | VIOSUpdateIBMWebsiteSource
)


class VIOSUpgradeHMCSource(TypedDict):
    ResourceType: Annotated[Literal["HMC"], Field(description="HMC image source.")]
    Name: _VIOS_NAME
    Disks: _VIOS_DISKS


class VIOSUpgradeNFSSource(_VIOSOptionalSource):
    ResourceType: Annotated[Literal["NFS"], Field(description="NFS image source.")]
    ServerHostOrIP: _VIOS_SERVER
    RemoteDirectory: _VIOS_REMOTE_DIRECTORY
    Disks: _VIOS_DISKS
    FileNames: NotRequired[_VIOS_FILE_NAMES]
    MountLocation: NotRequired[_VIOS_MOUNT_LOCATION]
    MountOptions: NotRequired[_VIOS_MOUNT_OPTIONS]


class VIOSUpgradeSFTPSource(_VIOSOptionalSource):
    ResourceType: Annotated[Literal["SFTP"], Field(description="SFTP image source.")]
    ServerHostOrIP: _VIOS_SERVER
    RemoteDirectory: _VIOS_REMOTE_DIRECTORY
    Disks: _VIOS_DISKS
    UserName: NotRequired[_VIOS_USER]
    Password: NotRequired[_VIOS_PASSWORD]
    SSHKey: NotRequired[_VIOS_SSH_KEY]
    PassPhrase: NotRequired[_VIOS_PASSPHRASE]
    FileNames: NotRequired[_VIOS_FILE_NAMES]


class VIOSUpgradeUSBSource(_VIOSOptionalSource):
    ResourceType: Annotated[Literal["USB"], Field(description="USB image source.")]
    USBDevice: _VIOS_USB_DEVICE
    Disks: _VIOS_DISKS


VIOSUpgradeSource = (
    VIOSUpgradeHMCSource
    | VIOSUpgradeNFSSource
    | VIOSUpgradeSFTPSource
    | VIOSUpgradeUSBSource
)


VIOSSource = VIOSUpdateSource | VIOSUpgradeSource
_VIOS_COMMON_KEYS = frozenset(
    {
        "Name",
        "ServerHostOrIP",
        "UserName",
        "Password",
        "SSHKey",
        "PassPhrase",
        "RemoteDirectory",
        "FileNames",
        "MountLocation",
        "MountOptions",
        "USBDevice",
        "SaveFile",
        "ResourceType",
    }
)
_VIOS_UPDATE_KEYS = _VIOS_COMMON_KEYS | {"ResourceType", "RestartVIOS"}
_VIOS_UPGRADE_KEYS = _VIOS_COMMON_KEYS | {"ResourceType", "Disks"}
_VIOS_UPDATE_REQUIRED = {
    "HMC": frozenset({"Name"}),
    "NFS": frozenset({"ServerHostOrIP", "RemoteDirectory"}),
    "SFTP": frozenset({"ServerHostOrIP", "RemoteDirectory"}),
    "USB": frozenset({"USBDevice"}),
    "IBMWebsite": frozenset(),
}
_VIOS_UPGRADE_REQUIRED = {
    "HMC": frozenset({"Name", "Disks"}),
    "NFS": frozenset({"ServerHostOrIP", "RemoteDirectory", "Disks"}),
    "SFTP": frozenset({"ServerHostOrIP", "RemoteDirectory", "Disks"}),
    "USB": frozenset({"USBDevice", "Disks"}),
}


_PLATFORM_MODEL_CONFIG = ConfigDict(extra="forbid", frozen=True, strict=True)


class SriovAdapterUpdate(BaseModel):
    """One documented SR-IOV adapter update selection."""

    model_config = _PLATFORM_MODEL_CONFIG
    AdapterID: Annotated[
        str,
        Field(min_length=1, pattern=r"\S", description="SR-IOV adapter identifier."),
    ]
    SubType: Annotated[
        Literal["adapterdriver", "Adapter", "adapterdriver,adapter"],
        Field(description="Documented SR-IOV firmware update subtype."),
    ]


class SystemFirmwareUpdateModel(BaseModel):
    """System firmware and nested SR-IOV work for PlatformUpdate."""

    model_config = _PLATFORM_MODEL_CONFIG
    UpdateType: Annotated[
        Literal["Update", "Upgrade", "NoUpdate"],
        Field(description="System firmware action."),
    ]
    UpdateOrder: Annotated[int, Field(description="Platform update execution order.")]
    SRIOVAdapterUpdate: Annotated[
        list[SriovAdapterUpdate] | None,
        Field(description="SR-IOV adapters updated with the system firmware step."),
    ] = None

    @model_validator(mode="after")
    def reject_empty_sriov(self) -> "SystemFirmwareUpdateModel":
        """Reject an explicitly empty adapter selection."""
        if self.SRIOVAdapterUpdate == []:
            raise ValueError("SRIOVAdapterUpdate must contain at least one adapter")
        return self


class IOAdapterUpdateModel(BaseModel):
    """One documented VIOS-owned IO-adapter firmware update."""

    model_config = _PLATFORM_MODEL_CONFIG
    Id: Annotated[
        str,
        Field(min_length=1, pattern=r"\S", description="VIOS partition identifier."),
    ]
    Device: Annotated[
        str, Field(min_length=1, pattern=r"\S", description="IO-adapter device name.")
    ]
    Repository: Annotated[
        Literal["MOUNTPOINT", "SFTP", "USB", "IBMWebsite", "DISK", "disk"],
        Field(description="Documented IO-adapter image repository."),
    ]


class VIOSPlatformUpdate(BaseModel):
    """One VIOS update and its optional nested IO-adapter work."""

    model_config = _PLATFORM_MODEL_CONFIG
    UpdateType: Annotated[
        Literal["Update", "update", "Upgrade", "NoUpdate"],
        Field(description="VIOS update action."),
    ]
    VIOSName: Annotated[
        str, Field(min_length=1, pattern=r"\S", description="VIOS name.")
    ]
    UpdateOrder: Annotated[
        int | None, Field(description="Platform update execution order.")
    ] = None
    Name: Annotated[
        str | None,
        Field(min_length=1, pattern=r"\S", description="VIOS image name."),
    ] = None
    ResourceType: Annotated[
        Literal["HMC", "NFS", "SFTP", "USB", "IBMWebsite"] | None,
        Field(description="VIOS image source."),
    ] = None
    IOAdapterUpdate: Annotated[
        list[IOAdapterUpdateModel] | None,
        Field(description="IO adapters owned by this VIOS."),
    ] = None

    @model_validator(mode="after")
    def validate_update_shape(self) -> "VIOSPlatformUpdate":
        """Enforce conditional resource and non-empty adapter requirements."""
        if self.UpdateType != "NoUpdate" and self.ResourceType is None:
            raise ValueError(f"ResourceType is required for {self.UpdateType}")
        if self.IOAdapterUpdate == []:
            raise ValueError("IOAdapterUpdate must contain at least one adapter")
        return self


class PlatformUpdateParameter(BaseModel):
    """Strict documented parameter object for the PlatformUpdate operation."""

    model_config = _PLATFORM_MODEL_CONFIG
    SystemFirmwareUpdate: Annotated[
        SystemFirmwareUpdateModel | None,
        Field(description="System firmware and SR-IOV update selection."),
    ] = None
    VIOSUpdate: Annotated[
        list[VIOSPlatformUpdate] | None,
        Field(description="VIOS and IO-adapter update selections."),
    ] = None

    @model_validator(mode="after")
    def require_update_action(self) -> "PlatformUpdateParameter":
        """Reject requests that contain no firmware or adapter action."""
        if self.VIOSUpdate == []:
            raise ValueError("VIOSUpdate must contain at least one VIOS")
        system_action = self.SystemFirmwareUpdate is not None and (
            self.SystemFirmwareUpdate.UpdateType != "NoUpdate"
            or self.SystemFirmwareUpdate.SRIOVAdapterUpdate is not None
        )
        vios_action = any(
            update.UpdateType != "NoUpdate" or update.IOAdapterUpdate is not None
            for update in self.VIOSUpdate or []
        )
        if not system_action and not vios_action:
            raise ValueError("PlatformUpdate requires at least one update action")
        return self


def platform_update_job(parameters: PlatformUpdateParameter) -> dict[str, Any]:
    """Build the documented native JSON PlatformUpdate JobRequest."""
    return {
        "JobRequest": {
            "RequestedOperation": {
                "OperationName": "PlatformUpdate",
                "GroupName": "ManagedSystem",
            },
            "JobParameters": {
                "JobParameter": [
                    {
                        "ParameterName": "PlatformUpdateParameter",
                        "ParameterValue": parameters.model_dump(exclude_none=True),
                    }
                ]
            },
        }
    }


def update_hmc_job(source: ConsoleUpdateSource) -> str:
    """Build a documented ``UpdateManagementConsole`` request."""
    unknown = set(source) - _CONSOLE_UPDATE_KEYS
    if unknown:
        raise ValueError(
            f"Unknown console update parameter(s): {', '.join(sorted(unknown))}. "
            f"Recognised parameters: {', '.join(sorted(_CONSOLE_UPDATE_KEYS))}."
        )
    return build_job_request(
        "UpdateManagementConsole",
        "ManagementConsole",
        {key: str(value) for key, value in source.items() if value is not None},
    )


def list_management_console_updates_job() -> str:
    """Build the parameterless ``ListManagementConsoleUpdates`` request."""
    return build_job_request("ListManagementConsoleUpdates", "ManagementConsole")


def _vios_params(
    source: Mapping[str, Any],
    operation: str,
    allowed_keys: frozenset[str],
    resource_types: frozenset[str],
    required_keys: Mapping[str, frozenset[str]],
) -> dict[str, str]:
    """Validate and stringify one documented VIOS job request."""
    unknown = set(source) - allowed_keys
    if unknown:
        raise ValueError(
            f"Unknown {operation} parameter(s): {', '.join(sorted(unknown))}. "
            f"Recognised parameters: {', '.join(sorted(allowed_keys))}."
        )
    resource_type = source.get("ResourceType")
    if resource_type is None:
        raise ValueError(f"{operation} source is missing required 'ResourceType'.")
    if resource_type not in resource_types:
        expected = ", ".join(sorted(resource_types))
        raise ValueError(
            f"Invalid {operation} ResourceType {resource_type!r}. "
            f"Expected one of: {expected}."
        )
    missing = {key for key in required_keys[resource_type] if source.get(key) is None}
    if missing:
        raise ValueError(
            f"{operation} ResourceType {resource_type!r} requires parameter(s): "
            f"{', '.join(sorted(missing))}."
        )
    save_file = source.get("SaveFile")
    if (
        isinstance(save_file, str)
        and save_file.lower() == "true"
        and source.get("Name") is None
    ):
        raise ValueError(f"{operation} SaveFile='true' requires parameter: Name.")
    return {key: str(value) for key, value in source.items() if value is not None}


def update_vios_job(source: VIOSUpdateSource) -> str:
    """Build a documented ``UpdateVIOS`` request."""
    params = _vios_params(
        source,
        "UpdateVIOS",
        _VIOS_UPDATE_KEYS,
        _VIOS_UPDATE_RESOURCE_TYPES,
        _VIOS_UPDATE_REQUIRED,
    )
    return build_job_request("UpdateVIOS", "VirtualIOServer", params)


def upgrade_vios_job(source: VIOSUpgradeSource) -> str:
    """Build a documented ``UpgradeVIOS`` request."""
    params = _vios_params(
        source,
        "UpgradeVIOS",
        _VIOS_UPGRADE_KEYS,
        _VIOS_UPGRADE_RESOURCE_TYPES,
        _VIOS_UPGRADE_REQUIRED,
    )
    return build_job_request("UpgradeVIOS", "VirtualIOServer", params)
