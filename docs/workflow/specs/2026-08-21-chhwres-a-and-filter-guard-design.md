# chhwres `-a` records and `--filter` selections — design

Issue: randomparity/hmc-mcp#285 · ADR: [0061](../../adr/0061-quoted-list-values-and-guarded-filter-grammar.md) (amends ADR 0045 in part) · Branch: `feat/a-record-guard-285`

## Outcome and completion criteria

Frozen in the `WORK:SCOPE` charter (token `q285-scope-7c41`, issue comment 5369324909):

1. `add_vnic_backing` builds its `-a` record via `build_attribute_record`.
2. Comma-carrying `backing_devices` values render in the IBM quoted-pair form whose post-shell HMC-argument bytes match what the 2026-08-21 live probes accepted (ADR 0061 Context); the pinning test asserts the builder output before `shlex.quote`, e.g. `port_vlan_id=0,"backing_devices=dev1,dev2"`. No new live-HMC probing is in scope.
3. The recurrence guard in `tests/unit/test_i_record_grammar.py` selects `chhwres -a` literals.
4. Every `--filter name=value` selection value is validated against the shared delimiter table before interpolation, in `src/` and `scripts/`.
5. `just verify` green.

## Design

### 1. Builder: quoted-pair support

`build_attribute_record(pairs, *, quoted: Collection[str] = ()) -> str` (ssh_commands.py).

- `quoted` names attributes whose value may be a comma-separated HMC list.
- Refusal precedence unchanged for unmarked values: the duplicate pre-pass runs first over all
  pairs, then each value passes name-form → delimiter-table → control-character checks inside
  `_validated_value`. A record with both a duplicate name and a bad value keeps raising the
  duplicate error; a test pins this precedence.
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

- validates each pair through the module-private `_validated_value(attribute, value)` the
  record builder already uses — attribute-name form, then the `_RECORD_DELIMITERS` table, then
  control characters (`HMCCLIError`, naming field and character; no second table).
  `_validated_value` gains an optional `surface` label (default preserving today's `-i`
  wording byte-for-byte): `build_filter` passes `--filter`, and the mempool bare-value call
  passes the `-a` value form, so every refusal names the command surface it protects,
- for a bare-value site that is not a filter (`remove_memory_pool`'s `-a <pool_name>`),
  callers invoke `_validated_value("pool_name", pool_name, surface="chhwres -a value")`
  directly, before the pool-list lookup so a bad name fails without an HMC round trip; the
  guard exempts that site by enclosing-function name (section 5), not by builder tracing,
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

- ssh_commands.py: `list_sriov_physical_port_rows` :548 (`adapter_ids`), `list_sriov_configured_logical_port_rows` :570 (`adapter_ids`), `read_sriov_lpar_state` :591 (`lpar_names`), `read_sriov_profile_ports` :604–605 (`lpar_names`+`profile_names`), `list_fc_ports` :665, `list_sea_adapters` :691, `list_vnics` :717, `list_vnic_rows` :768, `read_vios_identity` :800, the description probe :931, the msp probe :984, the `lpar_env` probe :1025, the proc-compat probe :1083 (all `lpar_names`)
- server_vios.py:390 (`vios_uuids`)
- scripts/live_test_runner.py :407, :1159, :1892 (`lpar_names`; raw f-string interpolation
  today, no `shlex.quote`; script already imports from `hmc_mcp.ssh_commands`) — these
  normalize to the same whole-expression shape (:407/:1892 drop their hard double-quote
  wrapper)

All sites normalize to one whole-expression shape — validate the raw value inside
`build_filter`, then wrap the result: `--filter {shlex.quote(build_filter([...]))}`. The
value-only fragments drop their `<name>=` literal prefix (`build_filter` returns the full
`name=value` text, so a kept prefix would double the attribute name). For a clean single-pair
value `shlex.quote` returns the text bare, rendering byte-identically to today; a
space-carrying name quotes the whole expression instead of the bare value, with identical
post-shell argv. Validation runs on the raw value, before quoting.

The guard's trace rule therefore accepts payloads that are a `build_filter` call, a local name
bound from one, or either wrapped in `shlex.quote` — nothing else.
### 5. Recurrence guard

`tests/unit/test_i_record_grammar.py`:

- `RECORD_COMMANDS` selection widened: literals opening with `chsyscfg`/`mksyscfg` keyed on
  `-i` (unchanged), literals opening with `chhwres` keyed on `-a`.
- New selection, stated as a predicate: any Constant/JoinedStr literal whose static text has a
  segment ending with `--filter` — all migrated sites share the whole-expression shape, so one
  rule covers them. The payload is the next FormattedValue after that segment, which must trace
  to `build_filter` or a local bound name, unwrapping `shlex.quote`; same unwrap/trace
  machinery as `-i`. The tradeoff of matching on containment rather than on the opening command
  is deliberate: a prose string spelling `--filter name=value` will be selected and must either
  drop that flag spelling or gain a traced payload. The selected-but-nothing-examined tripwire
  applies to this selection too, and the section 8 synthetic-violation test uses a migrated
  whole-expression literal so the vacuous-pass shape is covered.
- Explicit exemption keyed on the qualified enclosing function —
  `_VALUE_FORM_A_FUNCTIONS = {"remove_memory_pool"}` — with a comment citing the mempool value
  form and ADR 0061; a pinning test asserts `remove_memory_pool` still emits the bare pool
  name.
- The new selections inherit the existing docstring exclusion (`_docstring_nodes`): the four
  prose docstrings carrying `--filter lpar_names=...` (:711, :924, :979, :1074) must not be
  selected — a guard test pins that.
- The outside-function literal refusal (`test_no_record_command_literal_lives_outside_a_function`)
  extends to the `chhwres -a` and `--filter` selections alongside the payload trace, closing the
  hoist-the-template escape hatch for both.
- Known-site pin test extended to the new selections so silent narrowing is visible.

### 6. Threat model

**Boundary inventory.** One boundary, narrowed not added: MCP tool arguments / public Python
API strings → HMC command line (existing; ADR 0036 policy model decides who may call). This
design closes the `-a` mutation half and the `--filter` selection half of that boundary. The
grammar controls govern the structured tool surface only: the opt-in, policy-gated
`hmc_run_command` escape hatch (`server_command.py`, gated by `enable_arbitrary_command` and
the access policy per ADR 0036/0044) bypasses them by design and remains an authorization
concern outside this record.

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
- Shape pin: `list_fc_ports` asserts a space-carrying name renders
  `--filter 'lpar_names=my name'` (whole expression quoted; post-shell argv identical to the
  pre-migration `--filter lpar_names='my name'`), pinning the normalized composition against a
  silent revert to a kept literal prefix.
- Command-shape fixtures: unchanged. Conditional quoting keeps every well-formed `src/`
  command byte-identical. Exception, deliberate: the three `scripts/live_test_runner.py`
  sites normalize their quoting shape (:407/:1892 drop their hard double-quote wrapper,
  :1159 gains `shlex.quote`); no fixture pins those commands.

## Non-goals

No new MCP tools; no REST-path changes; no changes to `set_sriov_adapter_mode` (builds no CLI
command on `main`) or `remove_vnic_slot` (`-s` form); no live-HMC probes; no encoder beyond the
verified quoted-pair form.
