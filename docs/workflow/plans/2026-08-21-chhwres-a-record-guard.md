# Plan: chhwres `-a` records and `--filter` selections through one grammar

**Goal**: every `chhwres -a` attribute record and every `--filter name=value` selection in
`src/` and `scripts/` is produced by a builder that owns the HMC delimiter grammar, with an
evidence-bounded quoted-pair form for comma-separated list attributes.

**Architecture**: `build_attribute_record` (ssh_commands.py) keeps sole ownership of the record
grammar and gains a `quoted=` parameter rendering `"name=v1,v2"` for marked list values that
contain a comma. A sibling `build_filter` produces `--filter` expressions from the same
`_validated_value` primitive. All filter call sites route through it; the AST scan in
`tests/unit/test_i_record_grammar.py` widens to `chhwres -a` and `--filter` literals.

**Stack**: Python ≥3.11 (CI matrix 3.11), pytest, ruff + ty + detect-secrets + zizmor via
`just` recipes. Spec: `docs/workflow/specs/2026-08-21-chhwres-a-and-filter-guard-design.md`;
ADR: `docs/adr/0061-quoted-list-values-and-guarded-filter-grammar.md`.

## Global Constraints

- Guardrails (run inside the worktree): `just test` (quiet suite + coverage gate), `just
  smoke`, `just verify` (full pre-push). `just verify` before the final push.
- 100-char lines; ruff and `ty` must stay clean; conventional commits, imperative, ≤72-char
  subject.
- Public contract changes are refusals only (fail-closed); error type at the builder layer is
  `HMCCLIError`.
- Byte stability: every well-formed `src/` command renders byte-identical to today. The three
  `scripts/live_test_runner.py` sites normalize quoting (double-quote wrapper dropped;
  `shlex.quote` gained) — no fixture pins them.
- Refusal precedence in the record builder is unchanged: duplicate pre-pass first, then
  per-value name-form → delimiter-table → control-character checks.
- Never route a credential attribute through the builders without redaction (existing
  docstring rule).

## Task 1 — Builder: quoted-pair support

**Files**: `src/hmc_mcp/ssh_commands.py` (modify `build_attribute_record`, `_validated_value`),
`tests/unit/test_i_record_grammar.py` (extend).

**Interfaces**: later tasks call `build_attribute_record(pairs, *, quoted=("backing_devices",))`
and `build_filter(pairs)`; `_validated_value` stays module-private.

1. Write the failing tests in `tests/unit/test_i_record_grammar.py`:

```python
def test_build_attribute_record_quotes_a_marked_list_value():
    """A marked value carrying a comma renders as the IBM quoted pair."""
    record = build_attribute_record(
        [("port_vlan_id", 0), ("backing_devices", "dev1,dev2")],
        quoted=("backing_devices",),
    )
    assert record == 'port_vlan_id=0,"backing_devices=dev1,dev2"'


def test_build_attribute_record_leaves_a_marked_value_without_commas_bare():
    """A marked value without a comma is byte-identical to the unmarked form."""
    record = build_attribute_record(
        [("backing_devices", "sriov/vios1/100/1/1/2")], quoted=("backing_devices",)
    )
    assert record == "backing_devices=sriov/vios1/100/1/1/2"


@pytest.mark.parametrize("bad", ['"', "="])
def test_build_attribute_record_refuses_structure_inside_marked_values(bad):
    """Only the comma is permitted inside a quoted region; the rest is refused."""
    with pytest.raises(HMCCLIError, match="backing_devices"):
        build_attribute_record(
            [("backing_devices", f"dev{bad}1")], quoted=("backing_devices",)
        )


def test_build_attribute_record_refuses_a_duplicate_across_marked_and_unmarked():
    """Duplicate detection compares attribute names regardless of quoting."""
    with pytest.raises(HMCCLIError, match="appears twice"):
        build_attribute_record(
            [("backing_devices", "a"), ("backing_devices", "b")],
            quoted=("backing_devices",),
        )


def test_duplicate_refusal_precedes_value_validation():
    """The duplicate pre-pass fires before any per-value check, as today."""
    with pytest.raises(HMCCLIError, match="appears twice"):
        build_attribute_record([("name", "a"), ("name", "b,x")])
```

2. Run `uv run --no-sync pytest tests/unit/test_i_record_grammar.py -q` — the new tests fail
   (`TypeError: unexpected keyword argument 'quoted'`), the existing ones pass.
3. Implement in `src/hmc_mcp/ssh_commands.py`:

```python
def build_attribute_record(
    pairs: Sequence[tuple[str, object]],
    *,
    quoted: Collection[str] = (),
) -> str:
    """...existing docstring, plus:

    *quoted* names list-valued attributes whose HMC-side grammar is a
    comma-separated list (ADR 0061).  A marked value containing a comma is
    rendered as the IBM quoted pair ``"name=v1,v2"``; without a comma it
    renders bare, byte-identical to the unmarked form.  Every other record
    delimiter is refused inside a marked value: only the comma's behaviour
    inside a quoted region is live-verified.
    """
```

   Keep the duplicate pre-pass exactly where it is. Replace the `return ",".join(...)` with:

```python
    quotable = frozenset(quoted)
    parts = []
    for attribute, value in pairs:
        text = _validated_value(attribute, value, allow_comma=attribute in quotable)
        if attribute in quotable and "," in text:
            parts.append(f'"{attribute}={text}"')
        else:
            parts.append(f"{attribute}={text}")
    return ",".join(parts)
```

   Change `_validated_value` to:

```python
def _validated_value(
    attribute: str,
    value: object,
    *,
    allow_comma: bool = False,
    surface: str = "-i",
) -> str:
```

   and guard the table loop with `if character in text and not (allow_comma and character == ","):`.
   The refusal messages interpolate *surface*: `"HMC CLI {surface} attribute {attribute!r} ..."`
   — the default reproduces today's wording byte-for-byte; Task 2 passes `surface="--filter"`,
   Task 4's mempool validation passes the `-a` form. Add `Collection` to the
   `collections.abc` import.

4. Run `uv run --no-sync pytest tests/unit/test_i_record_grammar.py -q` — all pass, including
   every pre-existing builder test (byte-identical unmarked behaviour).
5. Commit: `feat: quoted-pair support in the HMC attribute record builder`.

**Acceptance**: new tests green; `uv run --no-sync pytest tests/unit/test_i_record_grammar.py -q`
prints `N passed`; `uv run --no-sync ruff check .` clean.

## Task 2 — `build_filter` and the `ssh_commands.py` filter sites

**Files**: `src/hmc_mcp/ssh_commands.py` (add `build_filter` after `build_attribute_record`;
migrate 13 sites), `tests/unit/test_i_record_grammar.py` (filter unit tests), command-shape
tests that pin exact strings.

**Interfaces**: consumes Task 1's `_validated_value`; `build_filter` is exported for
`server_vios.py` and `scripts/live_test_runner.py` (Task 4) and the guard (Task 5).

1. Failing tests first:

```python
def test_build_filter_joins_pairs_in_order():
    assert build_filter(
        [("lpar_names", "lpar1"), ("profile_names", "default")]
    ) == "lpar_names=lpar1,profile_names=default"


def test_build_filter_refuses_a_delimiter_in_a_value():
    with pytest.raises(HMCCLIError, match="comma"):
        build_filter([("lpar_names", "lpar1,lpar2")])


def test_build_filter_refuses_duplicates_and_empty_input():
    with pytest.raises(HMCCLIError, match="twice"):
        build_filter([("lpar_names", "a"), ("lpar_names", "b")])
    with pytest.raises(HMCCLIError, match="at least one"):
        build_filter([])
```

2. Implement beside the record builder:

```python
def build_filter(pairs: Sequence[tuple[str, object]]) -> str:
    """Return the ``--filter`` expression for *pairs*, or raise.

    The ``--filter`` grammar is the same ``name=value`` comma-joined record
    grammar (ADR 0061): a delimiter inside a value adds or rewrites a filter
    pair, so a mutation would select a partition the caller did not name.
    Values are validated by the same ``_validated_value`` primitive the
    record builder uses; there is no second delimiter table.

    A comma *inside* one value — IBM's multi-value list form — is refused
    until its encoding is probed; every site here selects a single resolved
    object by name.
    """
    if not pairs:
        raise HMCCLIError(
            "cannot build an HMC CLI --filter expression with no pairs; "
            "at least one name=value pair is required"
        )
    seen: set[str] = set()
    for attribute, _value in pairs:
        if attribute in seen:
            raise HMCCLIError(
                f"HMC CLI --filter attribute {attribute!r} appears twice in "
                "one expression; the HMC's handling of a repeated filter "
                "attribute is undefined, so the expression is refused"
            )
        seen.add(attribute)
    return ",".join(
        f"{attribute}={_validated_value(attribute, value, surface='--filter')}"
        for attribute, value in pairs
    )
```

3. Migrate the sites. Whole-expression shapes — replace the inner f-string with a
   `build_filter` call, e.g. `read_sriov_lpar_state`:

```python
    command = f"lssyscfg -r lpar -m {shlex.quote(system_name)} --filter {shlex.quote(build_filter([('lpar_names', lpar_name)]))} -F {','.join(fields)} --header"
```

   Same treatment at `list_sriov_roce_port_rows` :548 and
   `list_sriov_configured_logical_port_rows` :570 (`adapter_ids`), `list_vnic_rows` :768 and
   `read_vios_identity` :800 (`lpar_names`). `read_sriov_profile_ports` :604–605 becomes:

```python
    filters = build_filter(
        [("lpar_names", lpar_name), ("profile_names", profile_name)]
    )
```

   Value-interpolation shapes (`list_fc_ports` :665, `list_sea_adapters` :691, `list_vnics`
   :717, the description :931, msp :984, `lpar_env` :1025, and proc-compat :1083 probes) keep
   their literal prefix and quote the value inside the builder call, so a clean name renders
   byte-identically:

```python
        cmd += f" --filter lpar_names={build_filter([('lpar_names', shlex.quote(lpar_name))])}"
```

   (The `shlex.quote` output is what the HMC parser actually receives, so it is the text the
   grammar must validate; `shlex.quote` leaves every grammar-clean value untouched.)

4. Run `uv run --no-sync pytest tests/unit -q` and `uv run --no-sync pytest tests/system -q`;
   fix any exact-string command pins the migrations touch (expected: tests asserting
   `--filter lpar_names=...` command strings keep passing because clean values render
   identically; any that fail are updated to the builder-produced string in this commit).
5. Commit: `feat: route ssh_commands filter selections through build_filter`.

**Acceptance**: new filter tests green; `grep -n "filter.*f'" src/hmc_mcp/ssh_commands.py`
returns no un-migrated interpolation (the guard in Task 5 enforces this structurally);
existing command-shape tests green.

## Task 3 — `add_vnic_backing` through the record builder

**Files**: `src/hmc_mcp/ssh_commands.py` (`add_vnic_backing`), `tests/unit/test_vnic_ssh_contract.py`.

1. Failing test:

```python
@pytest.mark.asyncio
async def test_add_vnic_backing_quotes_a_multi_device_list():
    """A comma-carrying device list renders as the IBM quoted pair."""
    ...
    result = await add_vnic_backing(
        config, "system-a", "client-a", "sriov/vios1/1/0/2,sriov/vios2/2/0/2", 7
    )
    ...
    assert "-a", 'port_vlan_id=7,"backing_devices=sriov/vios1/1/0/2,sriov/vios2/2/0/2"' in sent


@pytest.mark.asyncio
async def test_add_vnic_backing_refuses_record_structure_in_a_device():
    with pytest.raises(HMCCLIError, match="equals sign"):
        await add_vnic_backing(config, "system-a", "client-a", "sriov/dev=1", 7)
```

   (Follow the file's existing fake-`run_hmc_command` pattern; the existing
   `sriov/vios name/100/1/1/2; touch nope` test keeps passing — space and `;` are not record
   structure.)

2. Implement:

```python
    payload = build_attribute_record(
        [("port_vlan_id", port_vlan_id), ("backing_devices", backing_device)],
        quoted=("backing_devices",),
    )
```

   Docstring: replace "prevalidated backing-device payload" with the device-list grammar —
   `backing_devices` is a `/`-delimited SR-IOV device spec or a comma-separated list of them,
   rendered as an IBM quoted pair when a comma is present (ADR 0061).

3. Run `uv run --no-sync pytest tests/unit/test_vnic_ssh_contract.py tests/network -q`.
4. Commit: `feat: build the vNIC -a record through the attribute builder`.

**Acceptance**: new tests green; the recorded single-device command bytes are unchanged
(existing tests pin them).

## Task 4 — `server_vios.py` and `scripts/live_test_runner.py` filter sites

**Files**: `src/hmc_mcp/server_vios.py` :390, `scripts/live_test_runner.py` :407, :1159, :1892.

**Interfaces**: imports `build_filter` from `hmc_mcp.ssh_commands` (the script already imports
`validate_lpar_description` from there).

1. `server_vios.py`:

```python
                f"lsviosbk --filter {shlex.quote(build_filter([('vios_uuids', uuid)]))} "
                "-F name,type --header"
```

2. `live_test_runner.py` :407 and :1892 (the hard double-quote wrapper is dropped; the value
   gains `shlex.quote`):

```python
        cmd=f"lssyscfg -r lpar -m {context.system_name}"
        f" --filter lpar_names={build_filter([('lpar_names', shlex.quote(context.lp3_name))])}",
```

   :1159:

```python
        cmd=f"lssyscfg -r lpar -m {context.system_name}"
        f" --filter lpar_names={build_filter([('lpar_names', shlex.quote(context.lp3_name))])} -F lpar_env",
```

   Add `import shlex` if absent; extend the existing `hmc_mcp.ssh_commands` import with
   `build_filter`.

3. `uv run --no-sync python -c "import hmc_mcp.server_vios"` and
   `uv run --no-sync python -m py_compile scripts/live_test_runner.py` both succeed.
4. Commit: `feat: route remaining filter sites through build_filter`.

**Acceptance**: both modules import clean; `grep -rn "filter.*{f'" src/hmc_mcp scripts`
returns nothing.

## Task 5 — Widen the recurrence guard

**Files**: `tests/unit/test_i_record_grammar.py`.

**Interfaces**: consumes `build_filter` (Task 2); enforces Tasks 2–4's migrations.

1. Extend the scan:
   - `RECORD_COMMANDS = ("chsyscfg", "mksyscfg")` keeps keying on `-i`; add
     `A_RECORD_COMMANDS = ("chhwres",)` keying on `-a`.
   - Add `_VALUE_FORM_A_FUNCTIONS = {"remove_memory_pool"}` with a comment citing the mempool
     bare-value form and ADR 0061; the `-a` per-function check skips that function.
   - New `--filter` selection: a scanned literal whose static text contains `--filter` must
     carry a `build_filter` call (or a local name bound from one) in one of its interpolations;
     reuse the unwrap/trace machinery, including `shlex.quote` unwrapping.
   - Both new selections inherit `_docstring_nodes` exclusion and the
     outside-function-literal refusal (extend
     `test_no_record_command_literal_lives_outside_a_function` to `chhwres -a` and
     `--filter` literals).
   - Extend `test_the_scan_finds_every_known_record_site` to pin: the `-a` functions
     (`assign_sriov_logical_port_dynamic`, `add_vnic_backing`, `remove_memory_pool`) and the
     full filter-site function set from Tasks 2 and 4 (16 functions).
   - Add a guard test asserting the four prose docstrings (`:711`, `:924`, `:979`, `:1074`
     content) are not selected.
2. Run `uv run --no-sync pytest tests/unit/test_i_record_grammar.py -q` — green against the
   migrated tree. If any site was missed, the scan names it: migrate it (Tasks 2/4 pattern)
   in this commit.
3. Commit: `test: widen the record-grammar guard to chhwres -a and --filter`.

**Acceptance**: `uv run --no-sync pytest tests/unit/test_i_record_grammar.py -q` green;
temporarily reverting one `build_filter` migration makes the scan fail (verify manually,
then restore).

## Task 6 — Full guardrails

Run `just verify` in the worktree. Expected: static gates pass, suite green with the coverage
gate met, smoke reports the tool count (unchanged — no new tools), build + artifact validation
pass, all CLI groups load.

Commit anything the full suite surfaces as its own fix commit; then the branch is ready for
adversarial review.
