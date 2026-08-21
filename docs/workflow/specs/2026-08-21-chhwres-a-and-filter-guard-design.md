# chhwres `-a` records and `--filter` selections — design

Issue: randomparity/hmc-mcp#285 · ADR: [0061](../../adr/0061-quoted-list-values-and-guarded-filter-grammar.md) (amends ADR 0045 in part) · Branch: `feat/a-record-guard-285`

## Outcome and completion criteria

Frozen in the `WORK:SCOPE` charter (token `q285-scope-7c41`, issue comment 5369324909):

1. `add_vnic_backing` builds its `-a` record via `build_attribute_record`.
2. Comma-carrying `backing_devices` values render in the IBM quoted-pair form.
3. The recurrence guard in `tests/unit/test_i_record_grammar.py` selects `chhwres -a` literals.
4. Every `--filter name=value` selection value is validated against the shared delimiter table before interpolation, in `src/` and `scripts/`.
5. `just verify` green.

## Design

### 1. Builder: quoted-pair support

`build_attribute_record(pairs, *, quoted: Collection[str] = ()) -> str` (ssh_commands.py).

- `quoted` names attributes whose value may be a comma-separated HMC list.
- Validation order per value, unchanged for unmarked values: attribute-name form, then the
  `_RECORD_DELIMITERS` table (`,` `=` `"` plus control characters), then duplicate refusal.
- A marked value containing `,` renders as `"name=v1,v2"` — literal double quotes around the
  whole pair. A marked value without `,` renders bare. `=`, `"`, control characters are
  refused even in marked values (nothing has verified their behaviour inside a quoted region).
- Duplicate detection compares attribute names after quote handling; a quoted pair is still a
  pair.

### 2. `add_vnic_backing`

```
payload = build_attribute_record(
    [("port_vlan_id", port_vlan_id), ("backing_devices", backing_device)],
    quoted=("backing_devices",),
)
```

Docstring updated: no longer "opaque string passed verbatim"; states the device-list grammar
and the quoted rendering. Callers pass a single `/`-delimited device or a comma-separated list;
both now parse correctly on the HMC.

### 3. Filter builder

`build_filter(pairs: Sequence[tuple[str, object]]) -> str` beside the record builder:

- validates each name against the same identifier form as record attribute names,
- refuses every `_RECORD_DELIMITERS` character in each value (`HMCCLIError`, naming field and
  character — identical wording convention to the record builder),
- returns `",".join(f"{n}={v}")`,
- and refuses a comma *inside* a value: IBM's multi-value list form
  (`--filter "lpar_names=a,b"`) has no probed encoding, so it is refused fail-closed (ADR 0061).

Single-pair sites call `build_filter([("lpar_names", lpar_name)])`; the one multi-pair site
(`read_sriov_profile_ports`) calls it with both pairs.

### 4. Site inventory (complete)

Record (`-a`) sites — ssh_commands.py:

| Site | Today | Change |
|---|---|---|
| `assign_sriov_logical_port_dynamic` :632 | builder | none |
| `add_vnic_backing` :821 | f-string | route through builder, `quoted=("backing_devices",)` |
| `remove_memory_pool` :908 | bare value `-a {pool_name}` | validate `pool_name` against `_RECORD_DELIMITERS` via the filter builder's value check; guard-exempt from record selection |

Filter sites — all become `build_filter(...)`:

- ssh_commands.py: `list_sriov_roce_port_rows` :548 (`adapter_ids`), `list_sriov_configured_logical_port_rows` :570 (`adapter_ids`), `read_sriov_lpar_state` :591 (`lpar_names`), `read_sriov_profile_ports` :604–605 (`lpar_names`+`profile_names`), `list_fc_ports` :665, `list_sea_adapters` :691, `list_vnic_rows` :768, `read_vios_identity` :800, the description probe :931, the msp probe :984, the `lpar_env` probe :1025, the proc-compat probe :1083 (all `lpar_names`)
- server_vios.py:390 (`vios_uuids`)
- scripts/live_test_runner.py :407, :1159, :1892 (`lpar_names`; script already imports from `hmc_mcp.ssh_commands`)

Note the two filter shapes: `--filter {shlex.quote(f'lpar_names={x}')}` (whole expression
quoted) and `--filter lpar_names={shlex.quote(x)}` (value-only). Both keep their shape; only
the interpolated text's provenance changes to the builder.

### 5. Recurrence guard

`tests/unit/test_i_record_grammar.py`:

- `RECORD_COMMANDS` selection widened: literals opening with `chsyscfg`/`mksyscfg` keyed on
  `-i` (unchanged), literals opening with `chhwres` keyed on `-a`.
- New selection: any scanned literal carrying `--filter` requires its value payload to trace to
  `build_filter` (same unwrap/trace machinery as `-i`).
- Explicit exemption set `_VALUE_FORM_A_PAYLOADS = {"pool_name"}` with a comment citing the
  mempool value form and ADR 0061; a pinning test asserts `remove_memory_pool` still emits the
  bare pool name.
- Known-site pin test extended to the new selections so silent narrowing is visible.

### 6. Threat model

**Boundary inventory.** One boundary, narrowed not added: MCP tool arguments / public Python
API strings → HMC command line (existing; ADR 0036 policy model decides who may call). This
design closes the `-a` mutation half and the `--filter` selection half of that boundary.

**Actor model.** Untrusted party: an authenticated MCP client (or direct Python API caller)
supplying arbitrary strings as names, ids, and device lists. Trust placement: the HMC CLI
parser is outside our control; everything on our side of the SSH channel must be grammar-clean
before dispatch.

**Controls per boundary.**

- Record payloads: `build_attribute_record` refusal (unmarked values) or conditional quoting
  (marked values) — this diff.
- Filter values: `build_filter` refusal — this diff.
- Shell word splitting: `shlex.quote` at every interpolation — existing, unchanged.
- Value-form mempool `-a`: delimiter validation via the shared table — this diff.

**Out of scope.** HMC parser behaviour beyond the verified grammar (backslash residual from
ADR 0045 stays open); REST-path tools (outside `-a`/`--filter` surface); authorization and
tenancy (ADR 0036–0040 unchanged); live-HMC probes.

### 7. Contract changes

- Refusals narrow accepted input fail-closed across the listed tools: a `,`/`=`/`"`/control
  character in any filtered name or id raises `HMCCLIError` before dispatch.
- `backing_devices` comma-carrying values change behaviour from misparse/inject to quoted
  render — the fix the issue asks for. No current caller can deliver one (ADR 0057's typed
  selector emits a single device); the quoted branch is forward-looking builder capability.
- Multi-value filter lists (`lpar_names=a,b`) are refused until their encoding is live-probed.
- Error type stays `HMCCLIError` at the builder layer, matching ADR 0045's split.

### 8. Test plan

- Builder: marked value without comma → bare; with comma → quoted pair; `"`, `=`, control in
  marked value → refused; duplicate across marked/unmarked → refused; unmarked behaviour
  byte-identical (existing tests already pin).
- Filters: single- and multi-pair joins; hostile value refused naming field; every inventoried
  site gets a hostile-value refusal test (parametrized, mirroring the existing `HOSTILE` tests).
- Guard: widened selection passes on the migrated tree; known-site pins updated; exemption
  pinned; a deliberately unguarded synthetic literal fails (keeps the scan honest).
- Command-shape fixtures: unchanged — conditional quoting keeps every well-formed command
  byte-identical.

## Non-goals

No new MCP tools; no REST-path changes; no changes to `set_sriov_adapter_mode` (builds no CLI
command on `main`) or `remove_vnic_slot` (`-s` form); no live-HMC probes; no encoder beyond the
verified quoted-pair form.
