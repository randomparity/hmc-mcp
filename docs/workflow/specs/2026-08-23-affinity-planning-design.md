# Affinity planning design

**Issue:** #311  
**Branch:** `feat/affinity-planning-311` from `main`  
**Decision:** [ADR 0083](../../adr/0083-affinity-planning-contract.md)

Authority comes from issue #311 and the campaign acceptance criteria. The implementation reuses
issue #310's merged selector/parser/API pattern and preserves issue #313's merged distinction
between replayable configuration and non-replayable current or predicted observations.

## Goal and scope

Add read-only current and predicted memory-affinity information at LPAR and managed-system scope.
The supported surfaces are shared async Python operations, MCP tools, CLI commands, documentation,
contract tests, and a live-runner path that invokes only `lsmemopt`. `optmem`, state mutation,
resource-group planning, and any guarantee of achieved placement are excluded.

## Public contract

The existing `get_lpar_memopt_score` and `list_lpar_memopt_scores` remain the current LPAR API.
Add these shared operations to `operations_ssh_network` and `hmc_mcp.api`:

```python
@dataclass(frozen=True)
class MemoptLparSelector:
    names: tuple[str, ...] = ()
    ids: tuple[int, ...] = ()

async def get_system_memopt_score(config: HMCConfig, system: str) -> dict[str, object]: ...
async def plan_lpar_memopt_scores(
    config: HMCConfig,
    system: str,
    prioritized: MemoptLparSelector | None = None,
    excluded: MemoptLparSelector | None = None,
) -> list[dict[str, object]]: ...
async def plan_system_memopt_score(
    config: HMCConfig,
    system: str,
    prioritized: MemoptLparSelector | None = None,
    excluded: MemoptLparSelector | None = None,
) -> dict[str, object]: ...
```

`MemoptLparSelector` rejects simultaneous names and IDs, blank/structural names, non-positive IDs,
duplicates, and an empty explicit selector. Prioritized and excluded selectors using the same
representation must be disjoint, and when both are supplied they must use the same representation.
Mixed representations fail before transport with `ValueError("prioritized and excluded selectors
must use the same representation")`.
Each MCP tool accepts structured optional `prioritized` and
`excluded` selectors. CLI commands expose repeatable `--prioritize-name`, `--prioritize-id`,
`--exclude-name`, and `--exclude-id` options and build the same type, so all adapters share one
validator.

The MCP tools are `hmc_get_system_memopt_score` (`system.get_memopt_score`),
`hmc_plan_lpar_memopt_scores` (`lpar.plan_memopt_scores`), and
`hmc_plan_system_memopt_score` (`system.plan_memopt_score`). All three declare
`target_kind="managed_system"` and the system selector as their authorization target, matching the
existing plural LPAR score operation. Prioritized and excluded LPARs are non-mutating scenario
inputs, not independently authorized targets. Metadata and policy tests prove that a grant for the
selected system permits planning even when its LPAR target table is narrower; a denied system
remains denied.

Current system output requires `curr_sys_score`. Predicted LPAR rows require `lpar_name`,
`lpar_id`, `curr_lpar_score`, and `predicted_lpar_score`; predicted system output requires
`curr_sys_score` and `predicted_sys_score`. HMC extension fields remain intact. Prediction
operations add `prediction_guaranteed: false` to each returned result. Empty LPAR planning output
is `[]`; an empty system result or malformed/multiple system rows is an `HMCCLIError`.

## Command construction and data flow

The SSH layer builds fixed verbs only:

- current system: `lsmemopt -m <system> -r sys -o currscore`
- predicted LPARs: `lsmemopt -m <system> -r lpar -o calcscore`
- predicted system: `lsmemopt -m <system> -r sys -o calcscore`

Name selectors map to `-p`/`-x`; ID selectors map to `--id`/`--xid`. Values are rendered from
validated tuples, joined with commas, and shell-quoted as one argument. Shared operations first
resolve the system name or UUID, then delegate to SSH primitives. MCP and CLI remain thin adapters.

## Error and capability behavior

Validation fails before transport with a field-specific `ValueError`. SSH transport, unsupported
firmware, permission, and HMC command failures retain the existing actionable `HMCCLIError`
contract, including the HMC command and stderr diagnostic. Tests stub an unsupported-capability
diagnostic (including the documented multi-resource-group system limitation), permission-denied
stderr, and a generic nonzero command error and assert each diagnostic remains visible. Exit-zero
malformed output is rejected with the row and missing field; multiple/empty system rows state the
observed cardinality. The implementation does not silently fall back from prediction to current
scores because that would misstate capability.

## Security model

The added boundary is user-controlled selector data entering an SSH command. Authenticated MCP and
local CLI callers are untrusted with respect to command structure. The selector type validates
scalar form and rejects delimiters/control characters, and the command builder shell-quotes the
joined value. Existing access-policy target authorization continues to constrain MCP system/LPAR
operations, and existing SSH configuration owns credentials and host trust. HMC output is untrusted
structured text: the existing parser plus required-field and cardinality checks prevents malformed
responses from becoming valid results. Threats in HMC firmware, credential provisioning, and
actually running DPO are out of scope because this change neither controls those systems nor invokes
`optmem`.

## Testing and verification

Focused tests prove exact commands for name and ID scenarios, selector validation, mixed
representations, and overlap at the shared validator, MCP, and CLI boundaries,
current/predicted distinction, `none`, empty and malformed output, system cardinality, and transport
errors. Exact-command assertions cover every SSH and live-runner planning path and inventory those
commands to prove no `optmem` invocation exists. MCP and CLI tests prove delegation, policy
metadata, and structured options. Public API and application boundary tests prove exports and
registration. The live runner gains current-system and both prediction calls, explicitly without
`optmem`. Finish with `just test`, `just smoke`, and
`just verify`.
