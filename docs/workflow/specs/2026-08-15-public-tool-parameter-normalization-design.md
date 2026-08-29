# Public tool parameter normalization design

## Goal and authority

Issue #147 requires consistent public names and units, a fix for the install wait/HMC
timeout collision, documented migration `wait_time` units, and schema-pinned closed
vocabularies. The campaign operator selected outright breaking renames because the
project is pre-release. [ADR-0025](../../adr/0025-normalize-public-tool-parameters.md)
records that decision.

The permitted surface is VIOS installation timing; storage, system, metrics, network,
adapter, and LPAR-configuration public parameters; their direct clients, operations,
CLI adapters, tests, schemas, and README. Compatibility aliases, result-shape changes,
and unrelated tools are excluded.

## Public contract

The following replacements are exact and leave no old keyword behind:

| Existing parameter | Replacement | Meaning |
|---|---|---|
| install `timeout` | `hmc_timeout_minutes` | HMC InstallVIOS/InstallLPAR job field, minutes |
| install `timeout_seconds` | `wait_timeout_seconds` | optional client polling budget, seconds |
| `capacity_mb` | `capacity_mib` | virtual-disk capacity, mebibytes |
| `size_mb` | `size_mib` | media repository or optical-media size, mebibytes |
| `lu_size_gb` | `lu_size_gib` | shared-storage logical-unit size, gibibytes |
| `vswitch_id`, `virtual_switch_id` | `virtual_switch_id` | numeric HMC SwitchID |
| `vswitch_name` | `virtual_switch_name` | SSH vNIC virtual-switch name |

CLI option spelling and help use the same unit vocabulary (`--capacity-mib`,
`--size-mib`, and `--lu-size-gib` where those concepts are exposed). Positional CLI
arguments retain their position but their Python identifiers and help text use the final
names. README examples and tool descriptions quote only the final contract.

## Install timing behavior

Both install tools accept `hmc_timeout_minutes: int = 60`, `wait: bool = False`,
`wait_timeout_seconds: int | None = None`, and `poll_interval: int = 5`. The HMC job
document always receives `hmc_timeout_minutes`. When `wait=False`, no polling occurs and
the optional client budget does not affect submission. When `wait=True`, the effective
polling budget is the explicit `wait_timeout_seconds` when supplied, otherwise
`hmc_timeout_minutes * 60 + poll_interval`. The extra interval lets the client issue one
final observation at or immediately after the HMC deadline rather than expiring at the
same boundary.

HMC minutes must be positive. An explicit client budget may be zero for an immediate
status observation, matching the shared wait contract, but cannot be negative. The
existing positive poll-interval rule remains. Validation occurs before opening a client
or submitting a job.

## Closed vocabularies

Shared type aliases expose finite schemas at the public boundary:

- `PcmCategory`: `ManagedSystem` or `LogicalPartition`, used by all six PCM tools and
  their presentation-neutral operations;
- `PartitionState`: documented partition states used by the LPAR and VIOS list filters;
- `ProcessorCompatibilityMode`: `default` plus the POWER modes supported across the
  README's HMC V8–V11 compatibility range.

The aliases constrain MCP and CLI schemas but do not add redundant runtime validators.
Managed-system state filters deliberately remain open strings because HMC can return
exact state values beyond a client-side documented vocabulary.
Processor-mode documentation continues to direct callers to
`hmc_get_proc_compat_modes`, because each managed system supports only a subset.

## Data flow and errors

Renamed values pass unchanged through server adapters, operations, client methods, and XML
builders; the rename must cover every direct in-repository keyword call. Unit renaming does
not scale or round values. Existing HMC error translation remains unchanged. Install timing
adds only deterministic validation and effective-budget selection before the existing job
submission/wait path.

## Verification

Focused tests first pin the new parameter names, absence of the old names, enum values,
and install timeout derivation/override/validation. Existing server, CLI, storage, network,
system, metrics, LPAR configuration, and live-runner tests are updated to the final names.
The complete branch must pass `just verify` and
`UV_NO_SYNC=1 uv run prek run --all-files` on arm64; CI proves the supported amd64/arm64
and Python 3.11–3.14 matrix.

## Durable workflow context

- Branch: `feat/normalize-tool-parameters-147`
- Base branch: `main`
- Guardrails: `just verify`; `UV_NO_SYNC=1 uv run prek run --all-files`
- Architecture: host arm64; declared targets amd64 and arm64; relationship included
