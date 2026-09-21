# ADR-0025: Normalize public tool parameters before release

## Status

Accepted

## Context

Several public MCP tools expose the same concepts under inconsistent or misleading
names. Binary storage quantities use `_mb` or `_gb` even where the HMC fields are
MiB/GiB, virtual switches use three spellings, and the install tools call an HMC-side
minute value `timeout` beside a client-side seconds value. The latter defaults let a
waiting client abandon a five-minute poll while the HMC job may continue for sixty
minutes. Some bounded HMC vocabularies are also emitted as unconstrained strings.

The project is pre-release. Keeping compatibility aliases would leave two public
mechanisms for each renamed concept and create permanent schema debt.

## Decision

Replace the following inconsistent public names outright:

| Public tools | Removed | Replacement | Unit or selector |
|---|---|---|---|
| `hmc_install_vios`, `hmc_install_vios_by_lpar_selector` | `timeout` | `hmc_timeout_minutes` | minutes |
| `hmc_install_vios`, `hmc_install_vios_by_lpar_selector` | `timeout_seconds` | `wait_timeout_seconds` | seconds |
| `hmc_attach_disk_to_lpar`, `hmc_create_virtual_disk` | `capacity_mb` | `capacity_mib` | MiB |
| `hmc_create_media_repository`, `hmc_create_optical_media` | `size_mb` | `size_mib` | MiB |
| `hmc_create_logical_unit` | `lu_size_gb` | `lu_size_gib` | GiB |
| `hmc_create_virtual_network` | `vswitch_id` | `virtual_switch_id` | numeric SwitchID |
| `hmc_add_network_adapter` | `virtual_switch_id` | `virtual_switch_id` | unchanged canonical name |
| `hmc_add_vnic` | `vswitch_name` | `virtual_switch_name` | switch name |

For installation, `wait_timeout_seconds=None` means that `wait=True` derives its client
budget from `hmc_timeout_minutes * 60 + poll_interval`. The extra polling interval is an
observation margin: it permits a final status request at or immediately after the HMC
deadline instead of expiring at the same boundary. An explicit client budget remains
supported and must be non-negative; `hmc_timeout_minutes` must be positive. The HMC
`WaitTime` migration field remains
`wait_time` because it is a distinct protocol field, and its seconds unit is documented.

Closed public vocabularies for PCM resource categories, system/partition state filters,
and processor compatibility modes are expressed as `Literal` aliases and pinned in the
capability-schema tests. All in-repository callers move to the new names in the same
change; no aliases or migration shims remain.

## Consequences

Existing pre-release MCP and direct Python callers using the old keyword names break and
must update. Schemas become self-describing, size units match their identifiers, and an
installation wait no longer expires before the HMC's own default job window. Explicitly
short client waits remain possible for callers that want them.

Processor compatibility modes vary by managed-system generation. The closed schema is
the union supported by this repository's documented HMC V8–V11 range; callers still use
`hmc_get_proc_compat_modes` to select a value supported by their particular system.

## Considered & rejected

- **Keep existing names and fix documentation only.** Rejected because misleading unit
  suffixes and three switch spellings would become permanent public contract.
- **Add compatibility aliases.** Rejected because the pre-release project does not need a
  migration layer, and dual parameters would create ambiguous schemas.
- **Always force the client wait to equal the HMC timeout.** Rejected because callers need
  the option to impose a shorter explicit observation budget.
