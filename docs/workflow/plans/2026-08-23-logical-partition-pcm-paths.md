# Logical-partition PCM paths implementation plan

Goal: route logical-partition processed and aggregated PCM requests through
their owning managed system and reject unsupported combinations. The Python
3.13 implementation extends the existing PCM boundary without dependencies.

## Global constraints

- Preserve managed-system paths and behavior.
- Require an owning-system selector for logical-partition metrics.
- Reject logical-partition preferences and Long Term Monitor before I/O.
- Do not add SSP support or live-HMC fixtures.
- Run `just test`, `just smoke`, and `just verify`.

## Task 1: Implement the owned PCM target atomically

Files: `src/hmc_mcp/client_pcm.py`, `src/hmc_mcp/client_contracts.py`,
`src/hmc_mcp/operations_pcm.py`, `src/hmc_mcp/server_metrics.py`,
`src/hmc_mcp/cli_metrics.py`, `tests/unit/test_pcm.py`, and
`tests/app/test_cli_commands.py`, and `README.md`.

This is one task because the client, operation, MCP, and CLI signature changes
must land together: no intermediate commit can both require an LPAR owner and
keep the current public LogicalPartition tests green.

Interfaces after the change:

```python
@dataclass(frozen=True)
class PcmResource:
    resource_uuid: str
    system_uuid: str | None = None

async def resolve_pcm_resource(
    hmc: HMCClient,
    category: PcmCategory,
    resource: str,
    system_name_or_uuid: str | None = None,
) -> PcmResource: ...

async def metric_links(
    hmc: HMCClient,
    category: PcmCategory,
    resource: str,
    kind: MetricKind,
    start_ts: str,
    end_ts: str | None,
    no_of_samples: int | None,
    system_name_or_uuid: str | None = None,
) -> list[dict[str, str]]: ...

async def metric_data(
    hmc: HMCClient,
    category: PcmCategory,
    resource: str,
    kind: MetricKind,
    start_ts: str,
    end_ts: str | None,
    no_of_samples: int | None,
    system_name_or_uuid: str | None = None,
) -> dict[str, Any]: ...

async def get_processed_metric_links(
    self: PcmClient, category: str, resource_uuid: str, start_ts: str,
    end_ts: str | None = None, no_of_samples: int | None = None, *,
    system_uuid: str | None = None,
) -> list[dict[str, str]]: ...

async def get_aggregated_metric_links(
    self: PcmClient, category: str, resource_uuid: str, start_ts: str,
    end_ts: str | None = None, no_of_samples: int | None = None, *,
    system_uuid: str | None = None,
) -> list[dict[str, str]]: ...

async def _metrics_links(
    self: PcmClient, category: str, resource_uuid: str, kind: str,
    start_ts: str, end_ts: str | None, no_of_samples: int | None, *,
    system_uuid: str | None = None,
) -> list[dict[str, str]]: ...
```

The four MCP tools append `system_name_or_uuid: str | None = None` immediately
before `profile`; `_metrics_links` and `_metrics_fetch` append the same argument
after `profile` and forward it by keyword. `metrics_show` appends
`system_name_or_uuid: str | None = typer.Option(None, "--system", ...)` after
`fetch`. Client calls use `system_uuid=target.system_uuid`. LogicalPartition
paths use the exact template `/rest/api/pcm/ManagedSystem/{system_uuid}/`
`LogicalPartition/{resource_uuid}/{kind}`; ManagedSystem paths remain
`/rest/api/pcm/ManagedSystem/{resource_uuid}/{kind}`.

1. Add failing unit tests for named and UUID LPAR owner resolution, missing
   owners, nested processed and aggregated routes, preference and LTM rejection,
   managed-system owner misuse, and unchanged managed-system routes. Update the
   existing LogicalPartition success tests to pass the owner selector. Assert
   every rejected case performs no resolver/client request.
2. Add failing CLI tests invoking `metrics show LogicalPartition <lpar>
   --system <owner> --start <timestamp>`, missing-owner and ManagedSystem-misuse
   cases, and `--help` spelling. Add MCP schema and CLI help assertions proving
   preference descriptions for `hmc_get_pcm_preferences`,
   `hmc_set_pcm_preferences`, `metrics prefs`, and `metrics set-prefs` advertise
   only `ManagedSystem`. Update any fake-client method signatures with the
   keyword-only `system_uuid` argument.
3. Run `uv run pytest -q --no-cov tests/unit/test_pcm.py
   tests/app/test_cli_commands.py`; expect failures in the new assertions.
4. Implement the complete signatures and validation above. Resolve the system
   before the partition, scope named LPAR lookup with that system UUID, retain
   both UUIDs in `PcmResource`, and reject unsupported combinations before I/O.
   Update both preference MCP docstrings/argument descriptions and both CLI
   preference command help strings to remove LogicalPartition as a supported
   category.
5. Run the same focused command and `just typecheck`; expect success.
6. Update README PCM guidance to show the LogicalPartition owner selector,
   nested processed/aggregated support, and ManagedSystem-only preferences and
   Long Term Monitor behavior. Compare every documented invocation with the
   final function and CLI signatures.
7. Commit the complete coherent change.

Acceptance: exact documented URLs are requested, every invalid combination is
actionable and side-effect-free, managed-system behavior is unchanged, and all
typed protocol and public adapter signatures agree. README examples follow the
new owner and category restrictions.

## Task 2: Verify and ship

Run `just test`, `just smoke`, and `just verify`, expecting zero failures and
warnings. Review the diff for accidental contract changes, then push and open a
PR closing #400. Rollback is a normal revert; no persisted state is introduced.
