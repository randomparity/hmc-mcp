# LPAR memory-optimization score design

**Branch:** `lpar_score` from `main`
**Guardrails:** `just verify`; `UV_NO_SYNC=1 uv run prek run --all-files`

## Outcome and boundaries

Add two read-only SSH/CLI tools that report the HMC memory-optimization
(affinity) score using `lsmemopt`:

- `hmc_get_lpar_memopt_score(system_name_or_uuid, lpar_name_or_uuid,
  profile=None) -> dict[str, str]` — the current score of one LPAR.
- `hmc_list_lpar_memopt_scores(system_name_or_uuid,
  lpar_name_or_uuid=None, profile=None) -> list[dict[str, str]]` — the
  current scores of all LPARs on a system, optionally filtered to a single LPAR.

Both are mirrored in the CLI as `hmc-mcp lpars memopt-score LPAR SYSTEM
[--json]` and `hmc-mcp lpars memopt-scores SYSTEM [--lpar NAME] [--json]`.

In scope: `lsmemopt -r lpar -o currscore` only. Out of scope (future
follow-up): `calcscore` (potential scores with `-p`/`--id` prioritize and
`-x`/`--xid` exclude options), multi-value `--filter` (its HMC CLI syntax
requires literal embedded double quotes), resource-group scores
(`-r resgroup`), system-wide scores (`-r sys`), and unrelated `hmc_mcp.api`
facade work. The two authorized score-operation exports and their ADR 0029
contract evidence are in scope.

## Commands and parsing (verified on hmc5.labda.sva.de, P9 9009 systems)

- No filter:
  `lsmemopt -m <system> -r lpar -o currscore`
- Single-LPAR filter:
  `lsmemopt -m <system> -r lpar -o currscore --filter lpar_names=<lpar>`

The default (no `-F`) output is one `key=value` line per LPAR:

```
lpar_name=p9da10v1t,lpar_id=1,curr_lpar_score=100
lpar_name=dalpar2rrd1t,lpar_id=17,curr_lpar_score=none
```

`curr_lpar_score` is a 0–100 decimal string, or the literal string `none`
when the partition is not subject to memory optimization. `-F` output is
bare values (`daneta1t,20,100`) and must **not** be used; the default
key=value rows are parsed with the existing `_parse_lshwres_output` helper
unchanged.

The public payload keeps the raw HMC keys, string values, exactly as the
CLI emits them: `{"lpar_name": str, "lpar_id": str, "curr_lpar_score":
str}` — consistent with `hmc_list_fc_ports` / `hmc_list_vnics` results.

## Architecture and data flow

- `ssh_commands.py` owns the two fixed-verb commands:
  `get_lpar_memopt_score(config, system_name, lpar_name)` and
  `list_lpar_memopt_scores(config, system_name, lpar_name=None)`.
  Both run through `run_hmc_command` (one SSH round-trip, `check=True`,
  `ssh_timeout`), quote interpolated selectors with `shlex.quote`, and reject
  an empty *lpar_name* with `ValueError` before any I/O. `get` raises
  `HMCCLIError` when the HMC exits 0 but reports no row (anomalous); a
  non-zero exit (e.g. `The partition named X was not found.`) surfaces as
  `HMCCLIError` from the transport, as with every SSH tool.
- `operations_ssh_network.py` owns the shared, presentation-neutral workflows.
  Each resolves name-or-UUID selectors through `resolve_ssh_names` and then
  calls the corresponding `ssh_commands` primitive, following ADR 0013.
- `server_lpar_config.py` hosts the two MCP tools tagged `_READ_ONLY`; both
  delegate to the shared operation through the existing profile-aware client
  boundary.
- `cli_lpars.py` hosts the two CLI commands and delegates to the same shared
  operations after loading the selected SSH profile.
- Registration: both tool names are added to `READ_ONLY_TOOLS` in `_app.py`
  and re-exported from `server.py`. Because ADR 0029 selects every public
  asynchronous operation for the supported reusable API, both operations are
  exported from `hmc_mcp.api`, with the facade inventory and contract tests
  updated in the same change. The operator explicitly authorized that additive
  contract expansion for issue #310. No new ADR is required: the change applies
  accepted ADRs 0013, 0015, 0025, and 0029 without changing their decisions.

## Testing

`tests/lpar/test_memopt_score.py` (new) drives the full stack through the
public tools with the established fixtures (`mock_hmc`,
`mock_uuid_resolution`, patched `hmc_mcp.ssh.asyncssh.connect`):

- exact command strings with and without `--filter`, including resolved
  CLI names after UUID resolution and name pass-through without extra I/O;
- parsing of multi-line output, the literal `none` score, and quoted
  (shlex) selectors;
- `get`: no-row stdout raises `HMCCLIError`; empty filter name raises
  `ValueError`; unknown LPAR (non-zero exit / `ProcessError`) raises
  `HMCCLIError`;
- `list`: empty output returns `[]`; `--filter` only present when an LPAR
  is supplied.

`tests/app/test_cli_commands.py` adds CLI wiring cases (transport
monkeypatched at `ssh_commands.run_hmc_command`): exit 0 + JSON/text
output, exit 1 on HMC failure, exit 2 on missing arguments.

## Known environment limitations (documented, not fixed here)

- On the observed HMC firmware (P9, HMC 2.63-CR1 era) `lssyscfg -r lpar
  -F UUID,...` reports `UUID` as an invalid attribute, so the *SSH
  fallback* of LPAR UUID resolution may fail on such systems. REST
  resolution is primary, so this feature works; the resolver behaviour is
  pre-existing and out of scope.
- Multi-value `--filter lpar_names=a,b` requires the HMC CLI literal-
  double-quote form (`--filter "\"lpar_names=a,b\""`) and is deliberately
  not exposed; v1 filters to at most one LPAR.
