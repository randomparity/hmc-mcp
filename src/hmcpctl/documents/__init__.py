"""Domain-specific XML request-document builders for the HMC REST API."""

# The package intentionally re-exports the stable pre-release builder surface.
from .access import (  # noqa: F401
    AUTHENTICATION_TYPES,
    AuthenticationType,
    build_hmc_user_document,
    build_logon_request_document,
    build_remote_access_document,
    merge_remote_access_document,
)
from .adapters import (  # noqa: F401
    build_client_network_adapter_document,
    build_vfc_adapter_document,
    build_vscsi_adapter_document,
)
from .boot import (  # noqa: F401
    BOOT_DEVICE_SELECTORS,
    BootDeviceSelector,
    build_boot_order_document,
    build_clear_boot_order_document,
)
from .lpar import (  # noqa: F401
    KEYLOCK_POSITIONS,
    OS_TYPES,
    PARTITION_TYPES,
    SHARING_MODES,
    VIOS_DEFAULT_RESOURCES,
    Keylock,
    LparResources,
    OsType,
    PartitionType,
    SharingMode,
    build_dlpar_mem_document,
    build_dlpar_proc_document,
    build_lpar_document,
    build_vios_document,
)
from .storage import (  # noqa: F401
    STORAGE_KINDS,
    StorageKind,
    build_brokered_file_document,
    build_linked_optical_media_document,
    build_media_repository_delete_document,
    build_virtual_disk_delete_document,
    build_virtual_disk_document,
    build_virtual_network_document,
    build_virtual_optical_mapping_document,
    build_virtual_optical_media_delete_document,
    build_volume_group_document,
    build_vscsi_mapping_document,
)
from .system import (  # noqa: F401
    MEM_MIRRORING_MODES,
    POWER_OFF_POLICIES,
    POWER_ON_LPAR_START_POLICIES,
    MemoryMirroringMode,
    PowerOffPolicy,
    PowerOnLparStartPolicy,
    build_managed_system_document,
)
