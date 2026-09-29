# kdive Tier A contract page — design (#878)

## Problem

kdive's BYO-host provider will import hmcpctl for power, state readback and console capture.
Nothing tells its author which module to import, how `PowerAction` maps onto the PowerOff
job, what the console guarantees, or which settings govern ownership and SSH host keys.

## Scope

Add `docs/kdive-tier-a-contract.md`, one row in `docs/index.md`, and
`tests/app/test_kdive_tier_a_contract.py`. No `src/` change. The page states:

- imports, written fully dotted: `power_lpar`, `get_lpar`, `get_lpar_state` from
  `hmcpctl.operations.lpar.core`; `capture_lpar_console_by_selector`; `list_lpar_ownership`;
  all pre-release (`docs/python-api.md`, ADR 0118); the MCP tool equivalents;
- `PowerAction` in job terms (#871, #872, ADR 0164): `on` is PowerOn (a running partition
  returns `already_running`, no job, unless `force=True`); `off` is `operation=shutdown`,
  `immediate=True`; `cycle` and `reset` add `restart=True`, indistinguishable at the job;
  graceful off is `operation=osshutdown` (needs RMC); crash is `operation=dumprestart` with
  `allow_dump_restart=True`; `dumpretry` is refused; a timed-out wait returns the
  non-terminal job — poll it, never resubmit;
- console: capture stdin is sealed (ADR 0072); `released` true means an independent probe
  proved the slot free, false means it may still be held unless `error` names
  `ConsoleHoldLostError`; a held slot raises `ConsoleHeldError`, never a takeover;
  `WritableConsoleSession` (ADR 0176) is separate and never used by capture; sessions: #957;
- `HMC_AUTHORIZE_POWER_OPERATIONS`/`authorize_power_operations`: off means power calls check
  no ownership; on refuses a foreign-owned partition unless `ownership_override=True`;
- `HMC_SSH_VERIFY_HOST_KEY`/`ssh_verify_host_key`: default true against `~/.ssh/known_hosts`;
  false disables server authentication for credential-bearing sessions.

The test fails closed over every backticked token: a dotted `hmcpctl.` path imports (module,
attribute or dataclass field); `hmc_*` is a `TOOL_SECURITY` key; `name=value` names a
`power_lpar` parameter, and an `operation=` value is in `POWER_OFF_OPERATIONS`; `HMC_*` and
lowercase field names are `HMCConfig` fields; anything else must be in an explicit prose-term
allowlist. Import paths, tools and settings each equal an expected set.

### Failure model

1. Actors and deployments: kdive's author reading the page; the test in `just test` and CI.
2. Invariants: the page names no symbol, tool, parameter or setting the package lacks, and
   never presents `dumpretry` as accepted.
3. Accepted: semantic prose is unchecked — bounded by citing ADRs 0072, 0164, 0176; an
   allowlisted prose term is unchecked by definition.
4. Covered elsewhere: facade promotion — ADR 0118; tool signatures — `just tool-docs-check`.

## Success

- The page states each Scope bullet and is linked once from `docs/index.md`.
- Every backticked token on the page resolves or is an allowlisted prose term.

## Validation

- Page names resolve — `focused-test`: `tests/app/test_kdive_tier_a_contract.py`; red when a
  fully dotted function name is renamed; green with
  `uv run --no-sync pytest tests/app/test_kdive_tier_a_contract.py -q`.
- Index link — `focused-test`: same module asserts one link in `docs/index.md`.
- Semantic prose — `task-test-not-applicable`: no executable consumer reads prose meaning;
  review checks it against ADRs 0072, 0164, 0176 and #871/#872.
