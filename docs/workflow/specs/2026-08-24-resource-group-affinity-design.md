# Resource-group affinity design

**Branch:** `feat/resource-group-affinity-312` from `main`  
**Decision:** [ADR 0084](../../adr/0084-capability-gated-resource-group-affinity.md)  
**Guardrails:** `just test`, `just smoke`, `just verify`

## Outcome and boundaries

Expose current and potential resource-group affinity scores through shared async operations, MCP,
the supported Python facade, and CLI. Capability admission is based on IBM's HMC V11R1M1110
command contract and the version-labelled POWER9/POWER10/POWER11 captures on issue #312.

The change is read-only. It does not start, stop, schedule, or otherwise control Dynamic Platform
Optimization; it does not create or modify resource groups; and it does not change existing LPAR
or system affinity contracts.

## Public contract

`MemoptResourceGroupSelector` is frozen and chooses exactly one non-empty mode: resource-group
names, non-negative decimal IDs, or `all=True`. ID `0` is valid and identifies the observed default
resource group; negative IDs are rejected. Operations accept `None` as the documented all-groups
default and normalize it to the explicit all selector.

`ResourceGroupAffinityResult` contains:

- `capability`: `available` or `capability-unavailable`;
- `mode`: `current` or `calculated`;
- `system`: resolved HMC system name;
- `selector`: the normalized selector;
- `items`: score rows, empty when unavailable;
- `unavailable_reason`: actionable text when unavailable, otherwise `None`.

The async operations are:

```python
async def list_resource_group_memopt_scores(
    config: HMCConfig,
    system: str,
    selector: MemoptResourceGroupSelector | None = None,
) -> ResourceGroupAffinityResult

async def plan_resource_group_memopt_scores(
    config: HMCConfig,
    system: str,
    selector: MemoptResourceGroupSelector | None = None,
) -> ResourceGroupAffinityResult
```

MCP mirrors these as `hmc_list_resource_group_memopt_scores` and
`hmc_plan_resource_group_memopt_scores`. CLI mirrors them as
`hmc-mcp lpars resource-group-memopt-scores` and
`hmc-mcp lpars plan-resource-group-memopt-scores`, with mutually exclusive repeatable
`--resource-group-name`, repeatable `--resource-group-id`, and `--all` options. Omitting all three
means all groups.

## Capability and command flow

The SSH boundary reads `lshmc -V` and accepts both compact `V11R2M1120` and labelled multiline
`Version: 11 / Release: 2 / Service Pack: 1120` forms. Missing, malformed, or older versions
return a capability-unavailable result explaining that HMC V11R1M1110 or later is required. This
prevents V10 from receiving grammar it does not implement.

On an admitted HMC, the command uses `-r resgroup`, current or calculated mode, an explicit `-g`
or `--gid` selector, an explicit `-F` projection, and `--header`. Shell quoting applies after
selector validation. The evidence-backed schemas are:

- current: `resource_group_name,resource_group_id,curr_score`;
- calculated: `resource_group_name,resource_group_id,curr_score,predicted_score,
  requested_lpar_names,requested_lpar_ids,protected_lpar_names,protected_lpar_ids`.

Rows preserve vendor fields and string values. Calculated rows add
`prediction_guaranteed: false`. Header-only output is a valid available result with no items;
zero-byte or whitespace-only output is a missing-header `HMCCLIError`. Missing, duplicate, or
reordered headers, wrong column counts, and blank identity/score fields likewise raise an actionable
`HMCCLIError`. Focused tests distinguish header-only from blank output. The literal score sentinel
`none` is valid.

Only error code `HSCLCA00` maps to capability-unavailable after admission. Other command,
permission, timeout, and transport failures propagate unchanged.

## Components

- `ssh_commands.py` owns selector validation, HMC-version parsing, command construction, strict
  delimited parsing, and capability-error recognition.
- `operations/ssh_network.py` resolves system UUID/name selectors and returns the stable result.
- `server_tools/lpar_config.py` and `cli_commands/lpars.py` adapt the shared operations without duplicating logic.
- `_app.py`, `server.py`, and `api.py` register/export the MCP and supported Python contracts.
- Focused tests cover validation, command strings, capability paths, schemas, malformed rows,
  adapters, CLI, API inventory, and security metadata. ID-selector tests include the observed
  default-group ID `0` and reject negative values. The live runner calls only current and calculated
  score queries and retains its existing no-`optmem` assertion.

## Security and trust boundaries

Callers control system and resource-group selectors. Existing system resolution governs the
system boundary. Selector constructors reject blanks, negative IDs, mixed modes, duplicates,
control characters, and unsafe aggregate size before I/O; `shlex.quote` encodes the resulting
fixed argument. HMC stdout is untrusted and must pass the exact header/column and required-field
checks before becoming a result. Vendor error text is not returned as capability evidence except
for the exact `HSCLCA00` code; the stable message is package-owned.

Credential handling, HMC authorization, SSH host verification policy, HMC defects, and DPO itself
remain governed by existing boundaries and are outside this change.

## Verification

Tests first establish failures for all new public behavior. Focused tests exercise current,
calculated, named, ID, and all selectors; HMC V10 and malformed-version admission; `HSCLCA00` and
ordinary failures; valid `none`; exact projections; malformed headers/rows; MCP, API, CLI, and live
runner wiring. Then run `just test`, `just smoke`, and `just verify` from the branch worktree.

No live HMC is configured in this worktree. The version-labelled issue capture is the live evidence
for this implementation; the updated live runner is the operator-facing replay path.
