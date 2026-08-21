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

   Give `build_attribute_record` a `surface: str = "-i record"` parameter and thread it into
   every `HMCCLIError` message it raises itself (empty record, duplicate attribute) and into
   each `_validated_value` call. Task 3 passes `surface="chhwres -a record"` from
   `add_vnic_backing` and Task 2's mempool bare-value validation passes the `-a` value form,
   so those refusals name their true command surface; every other caller keeps today's wording
   byte-for-byte via the default.

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
   Every refusal message interpolates *surface* — the delimiter-table message, the control-
   character message, **and** the attribute-name-form message (`"invalid HMC CLI {surface}
   attribute name ..."`) — so no refusal blames the wrong surface. The default reproduces
   today's wording byte-for-byte; Task 2 passes `surface="--filter"`,
   and Task 2's mempool bare-value validation passes the `-a` value form. Add `Collection` to
   the `collections.abc` import.

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

   Per-site hostile-value refusal tests, mirroring the existing ``HOSTILE`` block — one
   parametrized test per migrated ssh_commands filter function (17 minus server_vios and
   scripts sites are Task 4):

```python
HOSTILE_FILTER = "x,injected=1"


@pytest.mark.parametrize(
    "fn",
    [
        "list_sriov_physical_port_rows",
        "list_sriov_configured_logical_port_rows",
        "read_sriov_lpar_state",
        "read_sriov_profile_ports",
        "list_fc_ports",
        "list_sea_adapters",
        "list_vnics",
        "list_vnic_rows",
        "read_vios_identity",
        "get_lpar_description",
        "get_lpar_msp",
        "set_lpar_msp",
        "get_lpar_proc_compat",
    ],
)
def test_filter_site_refuses_a_hostile_name(fn):
    import hmc_mcp.ssh_commands as mod
    f = getattr(mod, fn)
    with pytest.raises(HMCCLIError, match="comma"):
        asyncio.run(f(_config(), "sys", HOSTILE_FILTER))
```

   (Adjust per-signature extras: `read_sriov_profile_ports` takes a fourth positional arg;
   `set_lpar_msp` requires `enabled` — pass `True`. The other listed functions take exactly
   `(config, system_name, name)`.) Plus the spec's shape pin:

```python
def test_list_fc_ports_renders_the_whole_expression_quoted():
    """A space-carrying name quotes the whole expression (normalized shape)."""
    sent = []

    async def fake_run(config, command):
        sent.append(command)
        return ""

    ...monkeypatch run_hmc_command...
    asyncio.run(list_fc_ports(_config(), "system-a", "my name"))
    assert "--filter 'lpar_names=my name'" in sent[0]
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

3. Validate the mempool bare value at the **top** of `remove_memory_pool`, before the
   `list_memory_pools` call (:884) — otherwise a delimiter-carrying pool name matches no real
   pool and dies in the not-found branch before validation runs:

```python
    _validated_value("pool_name", pool_name, surface="chhwres -a value")
```

   plus a failing-first unit test:

```python
def test_remove_memory_pool_refuses_a_delimiter_in_the_pool_name():
    with pytest.raises(HMCCLIError, match="comma"):
        asyncio.run(remove_memory_pool(_config(), "sys", "pool,extra=1"))
```

   The exists/lpar-assignment pre-checks run after validation; a bad name fails without an HMC
   round trip.
4. Migrate the sites. Every site normalizes to one whole-expression shape — validate the raw
   value inside `build_filter`, then let `shlex.quote` wrap the result, e.g.
   `read_sriov_lpar_state`:

```python
    command = f"lssyscfg -r lpar -m {shlex.quote(system_name)} --filter {shlex.quote(build_filter([('lpar_names', lpar_name)]))} -F {','.join(fields)} --header"
```

   Same treatment at `list_sriov_physical_port_rows` :548 and
   `list_sriov_configured_logical_port_rows` :570 (`adapter_ids`), `list_vnic_rows` :768,
   `read_vios_identity` :800, and `list_vnics` :717 (`lpar_names`).
   `read_sriov_profile_ports` :604–605 becomes:

```python
    filters = build_filter(
        [("lpar_names", lpar_name), ("profile_names", profile_name)]
    )
```

   The value-only fragments (`list_fc_ports` :665, `list_sea_adapters` :691, the description
   :931, msp :984, `lpar_env` :1025, and proc-compat :1083 probes) normalize to the same shape:

```python
        cmd += f" --filter {shlex.quote(build_filter([('lpar_names', lpar_name)]))}"
```

   Never interpolate the pair into a literal that already carries `<name>=` — `build_filter`
   returns the full `name=value` text, so a kept prefix would double the attribute name. For a
   clean single-pair value `shlex.quote` returns the text bare, rendering byte-identically to
   today; a space-carrying name quotes the whole expression instead of the bare value, with
   identical post-shell argv.

5. Run `uv run --no-sync pytest tests/unit tests/network tests/system -q` (tests/network
   directly covers `list_fc_ports` and `list_sea_adapters`);
   fix any exact-string command pins the migrations touch (expected: tests asserting
   `--filter lpar_names=...` command strings keep passing because clean values render
   identically; any that fail are updated to the builder-produced string in this commit).
6. Commit: `feat: route ssh_commands filter selections through build_filter`.

**Acceptance**: new filter tests green; every per-site hostile refusal green; shape pin green;
existing command-shape tests green. Structural enforcement of "no un-migrated site" is
deliberately deferred to Task 5's widened scan — this task's acceptance is behavioural only,
and the mid-sequence tree may still contain a missed site until then.

## Task 3 — `add_vnic_backing` through the record builder

**Files**: `src/hmc_mcp/ssh_commands.py` (`add_vnic_backing`), `tests/unit/test_vnic_ssh_contract.py`.

1. Failing test:

```python
@pytest.mark.asyncio
async def test_add_vnic_backing_quotes_a_multi_device_list():
    """A comma-carrying device list renders as the IBM quoted pair."""
    sent = []

    async def fake_run(config, command):
        sent.append(command)
        return ""

    ...monkeypatch hmc_mcp.ssh_commands.run_hmc_command to fake_run...
    await add_vnic_backing(
        config, "system-a", "client-a", "sriov/vios1/1/0/2,sriov/vios2/2/0/2", 7
    )
    # Every assertion here must be capable of failing.
    assert len(sent) == 1
    assert "-a 'port_vlan_id=7,\"backing_devices=sriov/vios1/1/0/2,sriov/vios2/2/0/2\"'" in sent[0]


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
        surface="chhwres -a record",
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
        f" --filter {shlex.quote(build_filter([('lpar_names', context.lp3_name)]))}",
```

   :1159:

```python
        cmd=f"lssyscfg -r lpar -m {context.system_name}"
        f" --filter {shlex.quote(build_filter([('lpar_names', context.lp3_name)]))} -F lpar_env",
```

   Imports, per file: `scripts/live_test_runner.py` — add `import shlex` if absent and extend
   its existing `hmc_mcp.ssh_commands` import with `build_filter`; `src/hmc_mcp/server_vios.py`
   — `shlex` is already imported (:9), add a new `from .ssh_commands import build_filter`
   (no cycle: ssh_commands imports neither server_vios nor anything that reaches it).

3. `uv run --no-sync python -c "import hmc_mcp.server_vios"` and
   `uv run --no-sync python -m py_compile scripts/live_test_runner.py` both succeed.
4. Commit: `feat: route remaining filter sites through build_filter`.

Per-site hostile-value runtime tests are not written for the server_vios or live_test_runner
sites: the script functions drive live MCP clients and cannot be exercised unit-side, and
server_vios's site sits inside a lambda handed to `_run_vios_backup_list_command` — these
sites are covered by the builder's unit tests and by the Task 5 structural scan. Recorded as a
deliberate narrowing of the spec's per-site-test promise to the ssh_commands surface.

**Acceptance**: both modules import clean; no raw `--filter {f'` interpolation remains under
`src/hmc_mcp/` or `scripts/`.

## Task 5 — Widen the recurrence guard

**Files**: `tests/unit/test_i_record_grammar.py`.

**Interfaces**: consumes `build_filter` (Task 2); enforces Tasks 2–4's migrations.

1. Extend the scan:
   - `RECORD_COMMANDS = ("chsyscfg", "mksyscfg")` keeps keying on `-i`; add
     `A_RECORD_COMMANDS = ("chhwres",)` keying on `-a`.
   - Add `_VALUE_FORM_A_FUNCTIONS = {"remove_memory_pool"}` with a comment citing the mempool
     bare-value form and ADR 0061; the `-a` per-function check skips that function. The spec's
     bare-emission pin is already discharged by
     `tests/unit/test_ssh_quoting.py::test_remove_memory_pool_quotes_hostile_pool_name` — cite
     it in the exemption comment rather than writing a duplicate test.
   - New `--filter` selection, stated as a predicate: any Constant/JoinedStr literal whose
     static text has a segment ending with `--filter`; the next FormattedValue must trace to
     `build_filter` or a local bound name, unwrapping `shlex.quote`. All migrated sites share
     the whole-expression shape, so this one rule covers them. The
     selected-but-nothing-examined tripwire applies, and the synthetic-violation test uses a
     whole-expression literal.
   - Known-site enumeration for the filter selection — 17 enclosing functions:
     `list_sriov_physical_port_rows`, `list_sriov_configured_logical_port_rows`,
     `read_sriov_lpar_state`, `read_sriov_profile_ports`, `list_fc_ports`, `list_sea_adapters`,
     `list_vnics`, `list_vnic_rows`, `read_vios_identity`, and the description, msp,
     `lpar_env`, and proc-compat probe functions (13 in ssh_commands.py);
     `hmc_list_vios_backups` in server_vios.py :390; and the three live_test_runner
     functions at :407/:1159/:1892.
   - Both new selections inherit `_docstring_nodes` exclusion and the
     outside-function-literal refusal (extend
     `test_no_record_command_literal_lives_outside_a_function` to `chhwres -a` and
     `--filter` literals).
   - The `-i` known set gains its seventh member the scan already finds:
     `unassign_sriov_logical_port_profile` (:646). Restate every category pin — `-i`, `-a`,
     and `--filter` — as per-category set equality, so an extra unknown site in any category
     surfaces; extend `test_the_scan_finds_every_known_record_site` accordingly.
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
