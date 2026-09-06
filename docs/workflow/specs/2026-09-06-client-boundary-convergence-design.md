# Client boundary convergence design

## Scope and authority

The user authorized this pre-release cleanup on 2026-09-06 while pursuing the
`refactor/pre-release` code-health branch. It covers four internal HMC client
method signatures, REST-only CLI commands that duplicate the runtime client
lifecycle, and package guidance for snapshots. It excludes new public facade
exports, compatibility wrappers, SSH-only CLI paths, and unrelated callback
annotations.

## Design

`AdaptersMixin.add_network_adapter`, `ClusterMixin.create_logical_unit`, and
`LpmMixin.lpar_migrate` plus `LpmMixin.lpar_migrate_validate` retain their
required resource identifiers as positional parameters. Every following
optional control becomes keyword-only. Their operation-layer callers pass the
controls by name, so values cannot be shifted into a neighboring control.
Those mixin members are not part of the stable reusable API: ADR 0029 limits
the exported `HMCClient` contract to lifecycle methods.

The following REST command functions will pass their one-operation call to
`with_client`: `lpars_set_description`, `lpars_decommission`, `lpars_modify`,
`lpars_provision`, `metrics_show`, `snapshot_capture`, `storage_map`,
`vios_power_on`, `vios_power_off`, `_power_lpar`, and `lpars_delete`.
Validation and confirmation remain before the call, including the LPAR delete
confirmation that currently occurs after client entry; output remains after it.
The shared helper continues to own configuration lookup, async context
entry/exit, event-loop execution, and error conversion. SSH paths, the local
`snapshot_assess_affinity` coroutine, and closures that coordinate more than
one client operation remain unchanged.

`hmc_mcp.snapshots` will describe its `models` and `operations` modules as the
pre-release import owners. It will identify `hmc_mcp.api` as ADR 0118's
six-name stable connection/configuration/common-error facade, rather than
claiming that it exports snapshot contracts.

## Failure and compatibility contract

Calling an affected client method with an optional positional argument now
raises Python's ordinary `TypeError`; callers must use the named control. No
compatibility adapter accepts the old form. CLI successes, errors, prompts, and
client cleanup retain the existing `with_client` behavior. Snapshot import
guidance becomes accurate without changing importable modules or exports.

## Testing and acceptance

Focused tests will inspect each client signature and prove every direct operation
delegate and fake-client assertion names its optional controls. A structural CLI
test will enumerate the named REST command functions and reject local
`client()` lifecycle use only in those functions, while integration tests
exercise representative result and error paths through `with_client`. A
package-guidance test will assert the corrected owner and stable-facade wording.
Relevant client, operation, and CLI tests; static checks; and the desloppify
resolve command must succeed.
