# Document-domain ownership design

Decision: [ADR 0122](../../adr/0122-document-domain-ownership.md)

## Scope and outcome

The approved pre-release refactoring moves each XML document vocabulary and builder
implementation from the mixed `hmc_mcp.documents.common` module to its owning domain
module. Package-level `hmc_mcp.documents` exports retain object identity and behavior.
`UOM_NS` and `document_envelope` remain the only shared common infrastructure.

This change does not alter generated XML, validation, tool schemas, REST calls, or
package-level public names. It intentionally removes the moved names from
`hmc_mcp.documents.common`; direct consumers must import from the owning module.

## Design

`lpar.py` owns the LogicalPartition envelope, partition vocabularies, resource model,
and its XML fragments. `system.py`, `storage.py`, `access.py`, and `boot.py` own their
respective vocabulary types and constants. `access.py` imports XML parsers and `WEB_NS`
directly, and `storage.py` imports `ATOM_NS` directly; neither treats those XML
primitives as common document-domain state.

`documents.__init__` imports each exported object from its owner. It does not recreate
or alias objects, so callers observing identity through the package receive the same
objects that domain modules use internally. Other modules retain their dependency on
only the shared envelope helpers in `common.py`.

The moved package-level symbols and their sole owners are:

| Owner | Package-level symbols |
| --- | --- |
| `access` | `AUTHENTICATION_TYPES`, `AuthenticationType` |
| `boot` | `BOOT_DEVICE_SELECTORS`, `BootDeviceSelector` |
| `lpar` | `KEYLOCK_POSITIONS`, `OS_TYPES`, `PARTITION_TYPES`, `SHARING_MODES`, `Keylock`, `LparResources`, `OsType`, `PartitionType`, `SharingMode` |
| `storage` | `STORAGE_KINDS`, `StorageKind` |
| `system` | `MEM_MIRRORING_MODES`, `POWER_OFF_POLICIES`, `POWER_ON_LPAR_START_POLICIES`, `MemoryMirroringMode`, `PowerOffPolicy`, `PowerOnLparStartPolicy` |

Private LPAR XML helpers are implementation details and are not package exports. Boot
builders call `document_envelope("LogicalPartition", body)` directly, rather than
importing `lpar_envelope` from `lpar.py`; this retains a shared-infrastructure dependency
without introducing a boot-to-LPAR dependency.

The structural test also asserts that `common` lacks every relocated non-facade name:
`lpar_envelope`, `_memory_config`, `_validate_sharing_mode`, `_dedicated_processor_body`,
`_shared_processor_body`, `_processor_config`, `ATOM_NS`, `WEB_NS`, `ET`, `DET`, and
`escapes_string_arguments`. The move removes common's blanket `F401` suppression. Together
with the table, that makes the infrastructure-only boundary falsifiable.

## Verification

Focused document tests prove XML output and validation are unchanged, package exports
are identical to the mapped domain owners, every mapped attribute is absent from `common`,
every listed private helper is absent from `common`, and access/storage use their direct
primitive import paths. The absent-name check includes the XML primitives formerly leaked
by `common`. `just lint`, `just typecheck`, and `just adr-numbering` validate the
implementation and decision record before the task is resolved.
