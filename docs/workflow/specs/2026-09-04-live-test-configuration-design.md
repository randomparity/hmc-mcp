# Live-test configuration design

## Goal

Make the live-test harness reusable without source edits or publication of
environment identifiers. This implements issue #609 and the user's decisions
recorded in its `WORK:SCOPE` charter.

## Configuration contract

The runner reads all scenario-specific values directly from `LIVE_TEST_*`
entries in the local `.env`; ambient `LIVE_TEST_*` exports are ignored. It
requires values for the managed system, persistent and temporary LPAR names,
test user, disk and dry-run resource names, protected LPAR list, SR-IOV
adapter/port/profile inputs, and virtual-media file and media names.

Virtual media has a local bind host and a distinct HMC-advertised host, plus a
shared TCP port. The HTTP server listens on the bind host; the URL and HMC
allow-list use the advertised host. The ISO URL is derived from the configured
path, advertised host, and port; it is not separately configurable.

Each value is non-empty after trimming. Integer and decimal fields must parse,
the HTTP port must be in the TCP port range, the SR-IOV capacity must be
positive, and the protected list must contain at least one name. Configuration
validation reports every invalid or absent key and stops before the runner
creates `Client`. Existing `HMC_*` configuration continues unchanged.

The reader preserves line numbers and rejects every duplicate `LIVE_TEST_*`
key with the conflicting lines. A shared pre-bootstrap validation step builds
the context before either HMC connection bootstrap or direct `main()` execution;
both the CLI and programmatic entry pass that context onward unchanged.

`LiveTestContext` owns the immutable configured fields plus mutable execution
state. Scenario modules receive the existing `RunState` and obtain all
environment-specific values through that context. No scenario module reads
`os.environ` directly.

## Complete source inventory

| Current source value class | `LIVE_TEST_*` field |
| --- | --- |
| Managed system, baseline LPAR, scratch LPAR, network-test LPAR | `SYSTEM_NAME`, `LPAR_NAME`, `SCRATCH_LPAR_NAME`, `NETWORK_TEST_LPAR_NAME` |
| Scratch-LPAR create memory and processors | `SCRATCH_CREATE_DESIRED_MEMORY_MIB`, `SCRATCH_CREATE_MAX_MEMORY_MIB`, `SCRATCH_CREATE_DESIRED_VCPUS`, `SCRATCH_CREATE_MAX_VCPUS` |
| Scratch-LPAR modify memory | `SCRATCH_MODIFY_DESIRED_MEMORY_MIB`, `SCRATCH_MODIFY_MAX_MEMORY_MIB` |
| Test user, primary virtual disk, dry-run LPAR and disk | `TEST_USER_NAME`, `VDISK_NAME`, `DRY_RUN_LPAR_NAME`, `DRY_RUN_STORAGE_NAME` |
| Provisioning volume-group fallback | `VDISK_VOLUME_GROUP_NAME` |
| Dry-run VIOS fallback slot, partition ID, and memory | `DRY_RUN_VIOS_SLOT`, `DRY_RUN_VIOS_PARTITION_ID`, `DRY_RUN_MEMORY_MIB` |
| Live-provision fallback memory and processor sizes | `PROVISION_MIN_MEMORY_MIB`, `PROVISION_DESIRED_MEMORY_MIB`, `PROVISION_MAX_MEMORY_MIB`, `PROVISION_DESIRED_VCPUS`, `PROVISION_MAX_VCPUS` |
| Protected LPAR names | `PROTECTED_LPAR_NAMES` |
| SR-IOV adapter, physical port, logical port, capacity, and profile | `SRIOV_ADAPTER_ID`, `SRIOV_PHYSICAL_PORT_ID`, `SRIOV_LOGICAL_PORT_ID`, `SRIOV_CAPACITY_PERCENT`, `SRIOV_PROFILE_NAME` |
| ISO file path, primary media name, alternate HTTP media name | `ISO_PATH`, `ISO_MEDIA_NAME`, `ISO_HTTP_MEDIA_NAME` |
| Local HTTP listener and HMC-visible URL host | `ISO_BIND_HOST`, `ISO_ADVERTISED_HOST`, `ISO_HTTP_PORT` |
| Main and short virtual-media repository capacities | `VMEDIA_REPOSITORY_SIZE_MIB`, `VMEDIA_SHORT_REPOSITORY_SIZE_MIB` |
| Placement probe memory and dynamic VLAN selection range | `PLACEMENT_MEMORY_MIB`, `VLAN_RANGE_START`, `VLAN_RANGE_END` |

This inventory is the required parser and example key set. Per-invocation random
password generation, generic user descriptions, protocol constants,
observed-resource fallbacks, timing bounds, and expected error classifications
are scenario behavior rather than environment selection and remain fixed.

## Example file

The repository adds `.env.example` with a definition, format, and these exact
fictional samples for every `LIVE_TEST_*` setting. The example-contract test
asserts this map and permits no additional live-test key or duplicate key.

```text
SYSTEM_NAME=example-lt-609-system
LPAR_NAME=example-lt-609-lpar
SCRATCH_LPAR_NAME=example-lt-609-scratch
NETWORK_TEST_LPAR_NAME=example-lt-609-network
TEST_USER_NAME=example-lt-609-user
VDISK_NAME=example-lt-609-disk
SCRATCH_CREATE_DESIRED_MEMORY_MIB=1536
SCRATCH_CREATE_MAX_MEMORY_MIB=3072
SCRATCH_CREATE_DESIRED_VCPUS=3
SCRATCH_CREATE_MAX_VCPUS=6
SCRATCH_MODIFY_DESIRED_MEMORY_MIB=2304
SCRATCH_MODIFY_MAX_MEMORY_MIB=4608
DRY_RUN_LPAR_NAME=example-lt-609-dry-run
DRY_RUN_STORAGE_NAME=example-lt-609-dry-disk
VDISK_VOLUME_GROUP_NAME=example-lt-609-vg
DRY_RUN_VIOS_SLOT=17
DRY_RUN_VIOS_PARTITION_ID=307
DRY_RUN_MEMORY_MIB=1536
PROVISION_MIN_MEMORY_MIB=1536
PROVISION_DESIRED_MEMORY_MIB=3072
PROVISION_MAX_MEMORY_MIB=6144
PROVISION_DESIRED_VCPUS=3
PROVISION_MAX_VCPUS=6
PROTECTED_LPAR_NAMES=example-lt-609-protected-a,example-lt-609-protected-b
PLACEMENT_MEMORY_MIB=3072
VLAN_RANGE_START=3100
VLAN_RANGE_END=3199
SRIOV_ADAPTER_ID=17
SRIOV_PHYSICAL_PORT_ID=9
SRIOV_LOGICAL_PORT_ID=917003
SRIOV_CAPACITY_PERCENT=7.5
SRIOV_PROFILE_NAME=example-lt-609-profile
ISO_PATH=/srv/example-lt-609/example-lt-609.iso
ISO_MEDIA_NAME=example-lt-609.iso
ISO_HTTP_MEDIA_NAME=example-lt-609-http.iso
ISO_BIND_HOST=0.0.0.0
ISO_ADVERTISED_HOST=iso.example.test
ISO_HTTP_PORT=18090
VMEDIA_REPOSITORY_SIZE_MIB=6144
VMEDIA_SHORT_REPOSITORY_SIZE_MIB=1536
```

`example.test` is a reserved documentation domain. Every selectable example
value differs from the current live-test source values. The example may include
commented `HMC_*` connection guidance, but it contains no credentials or
site-specific endpoint values.

## Error and safety behavior

The runner prints an actionable configuration error naming each required key
that needs correction. It does not run a selected subtask as a way around this
check. Virtual-media allow-list handling adds only the configured advertised
host and port. Protected-LPAR refusal uses the configured list.

Every memory, CPU, capacity, and repository-size setting must be positive. The
parser rejects desired memory or CPU values above their matching maximum, and
rejects provision memory triples unless minimum is at most desired and desired
is at most maximum. VLAN bounds must be within 1 through 4094 and ordered.

## Testing

Focused tests will prove that a complete fictional mapping creates a context,
that missing and malformed values produce aggregated actionable errors, and
that configuration failure precedes MCP and client construction on both direct
`main()` calls and command-line entry, and `.env`
takes precedence over conflicting ambient `LIVE_TEST_*` exports, including on
the successful TOML-connection path. Duplicate keys must report an error before
either entry path reaches HMC bootstrap. Existing scenario
tests will be updated to construct contexts with fictional inputs and will
prove the scratch-LPAR lifecycle, SR-IOV, provisioning, and virtual-media paths
use configured values, including separate ISO bind and advertised hosts. A
structural test will ensure
`.env.example` defines exactly the inventory key set and contains none of the
retired source identifiers. Focused tests also prove placement and VLAN calls
use configuration, and that invalid resource relationships fail before MCP or
client construction.

## Scope and constraints

This design changes only the live-test harness and its direct tests and
documentation. It adds no dependency, production API, persistence, or network
behavior. The host is `x86_64`; the project declares no target architecture.
The configuration read is local operator input; strict validation prevents it
from reaching a live tool call in an incomplete form.
