# Plan: Caller Token in the LPAR Description (issue #358)

**Goal:** callers of `hmc_create_lpar` / `hmc_provision_lpar` may pass an optional
`caller_token` written into the created LPAR's description as
`[hmc-mcp owner:<agent> created:<date>] [caller <token>]`.

**Architecture:** the token composes into ADR 0011's single best-effort SSH stamp
write (`set_lpar_description` over `chsyscfg -i`). Validation runs at tool entry,
at the CLI entry, and as the first statement of `create_and_stamp_lpar` — always
outside the stamp's transport-failure swallow and before any HMC traffic on that
surface. A dedicated anchored extractor yields the segment only when exactly one
well-formed caller segment follows a well-formed ownership stamp.

**Stack:** Python ≥3.11 (`uv`), pytest, respx for HTTP mocks, ruff + ty gates.

## Global Constraints

- Spec: `docs/workflow/specs/2026-08-21-lpar-caller-token-design.md`; decision:
  `docs/adr/0064-caller-token-in-lpar-description.md`. Both bind verbatim.
- Grammar: 1–64 printable ASCII; forbid whitespace, control chars, non-ASCII,
  `,` `=` `"` `[` `]` `\`. Empty string and non-`str` raise `ValueError`.
- Description format: exactly `[hmc-mcp owner:<agent> created:<date>] [caller <token>]`.
- Omission preserves byte-for-byte current behavior (existing pinned tests stay green).
- Stamp stays best-effort: transport failure warns, never fails the create; a malformed
  token raises before any HMC traffic on every surface (MCP tools, CLI) and outside the
  swallow.
- Extractor: literal case-sensitive `[caller ` prefix, anchored to a well-formed
  ownership stamp followed by one space; `None` on absent/spoofed/duplicated/misordered.
- Repo conventions: relative intra-package imports, Google-style docstrings with an
  `Args:` entry for every public tool parameter (ADR 0016), dataclass
  `metadata={"description": ...}` on result fields.
- Guardrails: `just lint`, `just typecheck`, `just test`, `just smoke`, then `just verify`
  before push. Setup once per clone: `just setup`.
- Every task ends with the changed-file tests passing and a commit.

## File map

| File | Responsibility |
|---|---|
| `src/hmc_mcp/ssh_commands.py` | Token grammar validator; composed description write. |
| `src/hmc_mcp/operations_lpar.py` | `LparCreation.caller_token`; anchored extractor; creation-path validation; threading. |
| `src/hmc_mcp/server_lpars.py` | `hmc_create_lpar` parameter, docstring, entry validation. |
| `src/hmc_mcp/server_provision.py`, `src/hmc_mcp/operations_provision.py` | `hmc_provision_lpar` parameter plumbing. |
| `src/hmc_mcp/cli_lpars.py` | `lpars create --caller-token` with entry validation. |
| `README.md` | Two tool-table rows. |
| `tests/unit/test_ownership.py`, `tests/app/test_ownership_tools.py`, `tests/lpar/test_provision_tool.py`, `tests/app/test_lifecycle_schema_descriptions.py` | Contracts above. |

Note: the MCP-tool-level create tests (`_env`, `_setup_mock`, respx `BASE`,
`SYSTEM_UUID`, the `hmc_create_lpar` import) live in **`tests/app/test_ownership_tools.py`**;
there is no `tests/lpar/test_ownership_tools.py`. All Task 3–4 additions to that file
target the `tests/app/` path.

---

## Task 1 — Grammar validator `validate_caller_token`

**File:** `src/hmc_mcp/ssh_commands.py`; **Tests:** `tests/unit/test_ownership.py`.

**Interfaces produced:** `validate_caller_token(token: str) -> None` — raises
`ValueError` naming the violation; imported later by Tasks 2–5.

**Step 1 — failing tests.** Append to `tests/unit/test_ownership.py` (module already
imports `pytest`, `HMCConfig`, `patch`, `AsyncMock`):

```python
from hmc_mcp.ssh_commands import validate_caller_token  # noqa: E402


def test_validate_caller_token_accepts_tracker_ids():
    validate_caller_token("CHG12345")          # ticket key
    validate_caller_token("2026/08/batch-7")   # slashes, digits
    validate_caller_token("owner@team:42")     # colon round-trips (spec guarantee 6)
    validate_caller_token("a" * 64)            # length boundary


@pytest.mark.parametrize(
    "bad",
    [
        "",               # empty string is a violation, not an omission
        "a" * 65,         # too long
        "a,b",            # comma: -i record delimiter
        "a=b",            # equals: -i record delimiter
        'a"b',            # double quote: -i record escape
        "a[b",            # bracket: breaks the [caller ...] framing
        "a]b",
        "a\\b",           # backslash: unverified -i behaviour (ADR 0045)
        "a b",            # whitespace
        "alicé",          # non-ASCII
        "a\nb",           # control character
    ],
)
def test_validate_caller_token_rejects(bad):
    with pytest.raises(ValueError, match="caller_token"):
        validate_caller_token(bad)


def test_validate_caller_token_rejects_non_string():
    with pytest.raises(ValueError, match="string"):
        validate_caller_token(42)  # type: ignore[arg-type]
```

Run `uv run --no-sync pytest tests/unit/test_ownership.py -q -k caller_token` — expect
collection error (`ImportError: cannot import name 'validate_caller_token'`).

**Step 2 — implement.** In `src/hmc_mcp/ssh_commands.py`, directly below
`validate_lpar_description` (which ends at line 284), insert:

```python
def validate_caller_token(token: str) -> None:
    """Raise ``ValueError`` if *token* cannot be embedded as ``[caller <token>]``.

    The grammar is server-defined (ADR 0064): 1–64 printable ASCII characters,
    forbidding whitespace, control characters, non-ASCII, and the characters
    ``,` ``=```"``[``]`` and ``\\``.  Commas, equals signs, and quotes are the
    HMC CLI ``-i`` record structure (ADR 0045); brackets break the caller
    segment's own framing; the backslash is refused because ADR 0045 records
    its ``-i`` behaviour as unverified.  The empty string is a violation, not
    an omission, and a non-``str`` value is rejected before any character
    check because the second validation site serves callers that bypass MCP
    tool typing.
    """
    if not isinstance(token, str):
        raise ValueError(
            f"caller_token must be a string, got {type(token).__name__}"
        )
    if not token:
        raise ValueError("caller_token must not be empty")
    if len(token) > 64:
        raise ValueError(f"caller_token is {len(token)} characters; maximum is 64")
    if not token.isascii() or any(
        ord(character) < 0x20 or ord(character) == 0x7F for character in token
    ):
        raise ValueError(
            "caller_token contains non-ASCII or non-printable characters; "
            "only printable ASCII is accepted"
        )
    if any(character.isspace() for character in token):
        raise ValueError(
            "caller_token contains whitespace; it must be a single word"
        )
    forbidden = {
        ",": "commas corrupt the HMC CLI -i parser",
        "=": "equals signs corrupt the HMC CLI -i parser",
        '"': "double quotes are the HMC CLI -i record escape",
        "[": "brackets break the [caller <token>] segment format",
        "]": "brackets break the [caller <token>] segment format",
        "\\": "backslash behaviour inside an HMC CLI -i record is unverified "
        "(ADR 0045)",
    }
    for character, reason in forbidden.items():
        if character in token:
            raise ValueError(f"caller_token contains {character!r}; {reason}")
```

**Step 3 — verify:** same pytest command passes (13 items). Run
`uv run --no-sync ruff check src/hmc_mcp/ssh_commands.py tests/unit/test_ownership.py`.
Commit `feat: validate_caller_token grammar (issue #358)`.

## Task 2 — Composed best-effort write

**File:** `src/hmc_mcp/ssh_commands.py` (`stamp_lpar_ownership`, lines 286–321);
**Tests:** `tests/unit/test_ownership.py`.

**Interfaces consumed:** `validate_caller_token` (Task 1).
**Interfaces produced:** `stamp_lpar_ownership(config, system_name, lpar_name, *,
agent_id=None, caller_token=None)` — unchanged return contract.

**Step 1 — failing tests.** Append:

```python
def test_stamp_composes_caller_segment():
    config = _config()
    today = datetime.date.today().isoformat()
    with patch(
        "hmc_mcp.ssh_commands.set_lpar_description", new=AsyncMock(return_value="")
    ) as mock_set:
        token = asyncio.run(
            stamp_lpar_ownership(
                config, "sys1", "lpar1", agent_id="alice", caller_token="CHG-1"
            )
        )
    assert token == f"[hmc-mcp owner:alice created:{today}] [caller CHG-1]"
    assert mock_set.call_args.args[3] == token
    # still a valid HMC description
    from hmc_mcp.ssh_commands import validate_lpar_description

    validate_lpar_description(token)


def test_stamp_without_caller_token_unchanged():
    config = _config()
    today = datetime.date.today().isoformat()
    with patch(
        "hmc_mcp.ssh_commands.set_lpar_description", new=AsyncMock(return_value="")
    ):
        token = asyncio.run(stamp_lpar_ownership(config, "sys1", "lpar1"))
    assert token == f"[hmc-mcp owner:hmc-mcp created:{today}]"


def test_stamp_bad_caller_token_raises_unswallowed():
    config = _config()
    with patch(
        "hmc_mcp.ssh_commands.set_lpar_description", new=AsyncMock(return_value="")
    ) as mock_set:
        with pytest.raises(ValueError, match="caller_token"):
            asyncio.run(
                stamp_lpar_ownership(
                    config, "sys1", "lpar1", caller_token=""
                )
            )
    mock_set.assert_not_awaited()  # rejected before any SSH traffic
```

Run `-k "stamp"` — new tests fail (unexpected keyword / no raise).

**Step 2 — implement.** Replace the body of `stamp_lpar_ownership`
(`PUT` lines 286–321 of `src/hmc_mcp/ssh_commands.py`):

```python
async def stamp_lpar_ownership(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
    *,
    agent_id: str | None = None,
    caller_token: str | None = None,
) -> str | None:
    """Write an ownership token, plus an optional caller token, to *lpar_name*.

    Builds ``[hmc-mcp owner:<agent_id> created:<YYYY-MM-DD>]`` and, when
    *caller_token* is given, appends `` [caller <token>]`` (ADR 0064), then
    writes the combined description with :func:`set_lpar_description` over SSH
    in one call.

    Returns the description on success; returns ``None`` (without raising) on
    SSH/network failure — a best-effort post-create call that must not fail
    the LPAR creation itself.  A malformed *caller_token* raises ``ValueError``
    before any SSH traffic instead of being swallowed, so it can never discard
    the ownership stamp.

    *agent_id* defaults to ``"hmc-mcp"`` when ``None`` or empty.
    """
    import datetime

    if caller_token is not None:
        validate_caller_token(caller_token)
    effective_id = agent_id if agent_id else "hmc-mcp"
    today = datetime.date.today().isoformat()
    description = f"[hmc-mcp owner:{effective_id} created:{today}]"
    if caller_token is not None:
        description = f"{description} [caller {caller_token}]"
    try:
        await set_lpar_description(config, system_name, lpar_name, description)
        return description
    except (HMCCLIError, OSError):
        # Transport, network failures are best-effort here: none of these
        # should fail the owning create call.  Grammar errors cannot reach
        # this point — both tokens are validated before the try.
        return None
```

(The old pre-write `validate_lpar_description(token)` inside the `try` moves out of
the swallow path implicitly: both segments are ASCII-safe by construction —
`validate_agent_id` bounds `agent_id`, `validate_caller_token` bounds the token —
so the defensive re-validation is dropped; `set_lpar_description` still re-validates
internally.)

**Step 3 — verify:** full `uv run --no-sync pytest tests/unit/test_ownership.py -q`
passes including pre-existing pins (`test_stamp_returns_token_on_success`,
`test_token_format`). Commit `feat: compose caller segment into ownership stamp (issue #358)`.

## Task 3 — Creation-path plumbing and anchored extractor

**Files:** `src/hmc_mcp/operations_lpar.py`; **Tests:** `tests/unit/test_ownership.py`,
`tests/app/test_ownership_tools.py`.

**Interfaces consumed:** `validate_caller_token` (Task 1).
**Interfaces produced:** `LparCreation.caller_token: str | None = None`;
`parse_lpar_ownership_caller_token(description: str) -> str | None`;
`create_and_stamp_lpar(hmc, system_name_or_uuid, creation)` validating as its first
statement; `stamp_created_lpar_ownership(..., caller_token=None)`.

**Step 1 — failing tests.**

In `tests/unit/test_ownership.py` append:

```python
from hmc_mcp.operations_lpar import parse_lpar_ownership_caller_token  # noqa: E402


def test_parse_caller_token_round_trip():
    description = (
        "[hmc-mcp owner:alice created:2026-08-21] [caller JIRA-1:x/y]"
    )
    assert parse_lpar_ownership_caller_token(description) == "JIRA-1:x/y"


def test_parse_caller_token_absent():
    assert parse_lpar_ownership_caller_token("[hmc-mcp owner:a created:2026-08-21]") is None
    assert parse_lpar_ownership_caller_token("plain legacy description") is None


@pytest.mark.parametrize(
    "description",
    [
        "[caller JIRA-1] [hmc-mcp owner:a created:2026-08-21]",   # misordered
        "[hmc-mcp owner:a created:2026-08-21] [caller X] [caller Y]",  # duplicated
        "[hmc-mcp owner:a created:2026-08-21][caller X]",         # missing space
        "[hmc-mcp owner:a created:2026-08-21] [caller ]",         # empty segment
        "[hmc-mcp owner:bogus created:x] [caller X]",             # malformed anchor
    ],
)
def test_parse_caller_token_spoofed_yields_none(description):
    assert parse_lpar_ownership_caller_token(description) is None
```

In `tests/app/test_ownership_tools.py` append (file already has `_env`, `_setup_mock`,
respx `BASE`, `SYSTEM_UUID`, and the `hmc_create_lpar` import):

```python
def test_create_lpar_invalid_caller_token_zero_routes(monkeypatch):
    """A malformed token fails before any HMC traffic (spec guarantee 3)."""
    _env(monkeypatch)
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        _setup_mock(router)
        with pytest.raises(ValueError, match="caller_token"):
            hmc_create_lpar(system_name_or_uuid=SYSTEM_UUID, name="test-lpar",
                            caller_token="a=b")
        assert all(not route.called for route in router.routes)


def test_create_lpar_valid_caller_token_stamped(monkeypatch):
    """A valid token threads through to the composed ownership stamp."""
    captured: dict[str, str] = {}

    async def capture_stamp(config, system_name, lpar_name, *, agent_id=None,
                            caller_token=None):
        captured["description"] = (
            f"[hmc-mcp owner:hmc-mcp created:2026-08-21] [caller {caller_token}]"
        )
        return captured["description"]

    _env(monkeypatch)
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        _setup_mock(router)
        with patch(
            "hmc_mcp.operations_lpar.stamp_lpar_ownership", new=capture_stamp
        ):
            result = hmc_create_lpar(
                system_name_or_uuid=SYSTEM_UUID, name="test-lpar",
                caller_token="CHG-9",
            )
    assert result.resource_created is True
    assert result.ownership_stamped is True
    assert result.warnings == ()
    assert captured["description"].endswith("[caller CHG-9]")
```

Match the file's existing import block for `patch`, `pytest`, and `respx` — mirror
whatever the neighboring tests import rather than inventing new imports.

Run both files — ImportError/failure expected.

**Step 2 — implement.** In `src/hmc_mcp/operations_lpar.py`:

(a) Extend the `.ssh_commands` import (line 28 area):

```python
from .ssh_commands import (
    _ssh_system_name,
    create_lpar_via_cli,
    stamp_lpar_ownership,
    validate_caller_token,
)
```

(b) Below `_OWNERSHIP_TOKEN` (lines 48–50) add:

```python
_CALLER_TOKEN = re.compile(
    r"\[hmc-mcp owner:[^\s\[\]:]+ created:\d{4}-\d{2}-\d{2}\] "
    r"\[caller (?P<token>[^\s\[\]]+)\]"
)
```

(c) After `parse_lpar_ownership_owner` (lines 106–109) add:

```python
def parse_lpar_ownership_caller_token(description: str) -> str | None:
    """Return the caller tracking token following a well-formed ownership stamp.

    Matches the literal ``[caller <token>]`` segment only when it directly
    follows a well-formed ADR 0011 ownership stamp and one space, and only
    when exactly one such segment exists, so spoofed, duplicated, or
    misordered segments yield ``None`` (ADR 0064).
    """
    matches = _CALLER_TOKEN.findall(description)
    return matches[0] if len(matches) == 1 else None
```

(d) Add the dataclass field after `max_virtual_slots` (line 63):

```python
    caller_token: str | None = None
```

(e) `stamp_created_lpar_ownership` (lines 236–261): add keyword parameter and pass it
through:

```python
async def stamp_created_lpar_ownership(
    hmc: HMCClient,
    system_uuid: str,
    system_fallback: str,
    created_lpar: dict[str, Any],
    caller_token: str | None = None,
) -> tuple[bool | None, list[str]]:
```

and change the `stamp_lpar_ownership` call (line 253) to:

```python
    token = await stamp_lpar_ownership(
        hmc.config,
        system_name,
        confirmed_name,
        agent_id=hmc.config.agent_id,
        caller_token=caller_token,
    )
```

(f) `create_and_stamp_lpar` (line 264): make validation the first statement:

```python
async def create_and_stamp_lpar(
    hmc: HMCClient,
    system_name_or_uuid: str,
    creation: LparCreation,
) -> LparCreationResult:
    """Validate and create an LPAR with fallback and ownership stamping."""
    if creation.caller_token is not None:
        # First statement, before find_partition_by_name and outside the
        # stamp's best-effort catch: no create can precede rejection, and a
        # malformed token can never discard the ownership stamp (ADR 0064).
        validate_caller_token(creation.caller_token)
    existing = await hmc.find_partition_by_name(creation.name)
```

and thread the token into the final stamp call (line 318):

```python
    ownership_stamped, warnings = await stamp_created_lpar_ownership(
        hmc,
        system_uuid,
        system_name or system_name_or_uuid,
        created_lpar,
        caller_token=creation.caller_token,
    )
```

**Step 3 — verify:** `uv run --no-sync pytest tests/unit/test_ownership.py
tests/app/test_ownership_tools.py tests/lpar/test_lpar_http406.py -q` all pass;
existing pins untouched. Commit `feat: thread caller token through LPAR creation (issue #358)`.

## Task 4 — MCP tool surfaces

**Files:** `src/hmc_mcp/server_lpars.py`, `src/hmc_mcp/server_provision.py`,
`src/hmc_mcp/operations_provision.py`;
**Tests:** `tests/lpar/test_provision_tool.py`,
`tests/app/test_lifecycle_schema_descriptions.py`.

**Interfaces consumed:** `validate_caller_token`, `LparCreation.caller_token`.
**Interfaces produced:** `hmc_create_lpar(..., caller_token: str | None = None)` and
`hmc_provision_lpar(..., caller_token: str | None = None)`; rendered parameter
descriptions name the grammar.

**Step 1 — failing tests.**

In `tests/lpar/test_provision_tool.py` append (mirror that file's existing fixtures
and helpers for `_network` / `_storage` / `SYSTEM_UUID` — use whatever names the
neighboring provision tests use, matching them exactly rather than inventing):

```python
def test_provision_invalid_caller_token_fails_before_preconditions():
    """dry_run=True still fails fast on a bad token (spec guarantee 3)."""
    with pytest.raises(ValueError, match="caller_token"):
        hmc_provision_lpar(
            system_name_or_uuid=SYSTEM_UUID,
            name="p-lpar",
            network=_network(),
            storage=_storage(),
            dry_run=True,
            caller_token="a=b",
        )


def test_provision_passes_caller_token_to_creation():
    result = hmc_provision_lpar(
        system_name_or_uuid=SYSTEM_UUID,
        name="p-lpar",
        network=_network(),
        storage=_storage(),
        power_on=False,
        caller_token="CHG-9",
    )
    assert result.resource_created is True
    assert result.ownership_stamped is True
```

In `tests/app/test_lifecycle_schema_descriptions.py` append:

```python
def test_caller_token_parameter_documents_grammar():
    tools = _tools_by_name()
    for name in ("hmc_create_lpar", "hmc_provision_lpar"):
        description = tools[name].parameters["properties"]["caller_token"][
            "description"
        ]
        assert "[caller " in description
        assert "64" in description
```

Run — failures expected (parameter absent).

**Step 2 — implement.**
Landing-outcome docs (spec guarantee 7 / audit finding 2): extend both tools'
result documentation — the `Returns:` prose of `hmc_create_lpar`'s docstring
(after the ``ownership_stamped`` bullet) and `hmc_provision_lpar`'s
`Returns:` paragraph — with one sentence:

```python
    With ``caller_token``, ``ownership_stamped=True`` confirms both the ownership
    stamp and the caller segment landed (one combined write); ``False`` means both
    were lost; ``None`` means the stamp was skipped — the reason is in ``warnings``.
```

`src/hmc_mcp/server_lpars.py`: add below the existing `.operations_lpar` import block:

```python
from .ssh_commands import validate_caller_token
```

Extend the signature after `max_virtual_slots: int | None = None,` (line 70):

```python
    caller_token: str | None = None,
```

Extend the docstring `Args:` section (after the `max_virtual_slots` line, line 114):

```python
        caller_token: Optional caller tracking reference embedded in the partition
            description as ``[caller <token>]`` after the ownership stamp (ADR 0064);
            1–64 printable ASCII characters, no whitespace or , = " [ ] \\.
```

At the top of the function body (first statement, before `async def _go()`):

```python
    if caller_token is not None:
        validate_caller_token(caller_token)
```

and pass it into the `LparCreation(...)` construction (after `max_virtual_slots,`):

```python
                        caller_token=caller_token,
```

`src/hmc_mcp/server_provision.py`: same signature addition after
`assignments: LparPcieAssignments = LparPcieAssignments(),` (line 56), same docstring
wording in its `Args:` section, same first-statement validation (import
`validate_caller_token` from `.ssh_commands`), and pass
`caller_token=caller_token` into the `provision_lpar(...)` call (lines 80–91).

`src/hmc_mcp/operations_provision.py`: add `caller_token: str | None = None` keyword
to `provision_lpar`'s signature (documented in its Returns-docstring list like the
other parameters) and change the construction at line 465 to
`LparCreation(name, partition_type, resources, caller_token=caller_token)`.
Dry-run exits happen after tool-entry validation by construction, satisfying the
dry-run contract test.

**Step 3 — verify:** `uv run --no-sync pytest tests/lpar/test_provision_tool.py
tests/app/test_ownership_tools.py tests/app/test_lifecycle_schema_descriptions.py -q`
passes. Commit `feat: expose caller_token on hmc_create_lpar and hmc_provision_lpar (issue #358)`.

## Task 5 — CLI option, README, full guardrails

**Files:** `src/hmc_mcp/cli_lpars.py` (`lpars_create`, lines 465–570), `README.md`.

**Step 1 — implement.**

CLI: add option after the `pcie_assignments` option (lines 504–508):

```python
    caller_token: str | None = typer.Option(
        None,
        "--caller-token",
        help="Optional tracking reference embedded in the partition description "
        "as '[caller <token>]' (ADR 0064); 1–64 printable ASCII characters, "
        "no whitespace or , = \" [ ] \\",
    ),
```

Validate at CLI entry — first statement of `lpars_create`, before the
partition-type check, the confirmation prompt, and the PCIe prevalidation (which
performs HMC REST round trips whenever SR-IOV/vNIC assignments are present):

```python
    if caller_token is not None:
        from .ssh_commands import validate_caller_token

        validate_caller_token(caller_token)
```

(Hoist the import to the module's existing `.ssh_commands` import block at lines
61–69 instead of importing inline, if ruff prefers.)

Pass it into `LparCreation(...)` at lines 546–551:

```python
                LparCreation(
                    name,
                    partition_type,
                    resources,
                    partition_id=partition_id,
                    caller_token=caller_token,
                ),
```

README: in the lifecycle tool table, extend the `hmc_create_lpar` row (~line 672) and
the `hmc_provision_lpar` row (~line 670) with: "; optional `caller_token` embeds
`[caller <token>]` in the partition description (ADR 0064)".

**Step 2 — verify (full suite):**

```
just test      # expect: all tests pass, coverage gate green
just smoke     # expect: MCP handshake OK, tool count unchanged
just lint && just typecheck   # expect: clean
just verify    # expect: full pre-push guardrail green
```

Commit `feat: caller token CLI option and docs (issue #358)`.

## Rollback

Tasks are ordered by dependency (each consumes the previous task's interface), so a
partial rollback reverts in strict reverse commit order; reverting any single commit
except the newest leaves later tasks' imports dangling. No migrations, no persisted
state, no external contracts beyond tool signatures.

## Plan-review record

The `$trial-loop` run on this plan (2026-08-21, 1 iteration) returned six findings, all
dispositioned `accepted-fixed` in this revision: extractor uniqueness guard
(`findall` + single-match rule) added so the pinned duplicated-segment test passes;
all five `tests/lpar/test_ownership_tools.py` citations corrected to
`tests/app/test_ownership_tools.py`; CLI entry validation moved before the PCIe
prevalidation round trips; a valid-token `hmc_create_lpar` pass-through test added;
Task 1 count corrected to 13; rollback section rewritten for reverse-order dependence.
Nothing outstanding.

## Scope-audit record

The `$oathbind` audit (2026-08-21, report at
`.agent/oathbind/2026-08-21-issue-358-oathbind.md`) returned **needs-attention**
with two low findings, both dispositioned: (1) CLI `--caller-token` exceeds the
charter's surface hedge — accepted via a `WORK:SCOPE` amendment on issue #358
ratifying CLI/MCP parity per repo convention; (2) guarantee-7 landing-outcome
documentation absent from tasks — accepted-fixed as the Task 4 step above.

## Branch-review record

The branch `$trial-loop` (2026-08-21, 3 iterations) returned **approve** with two fix
commits: 1285f88 restores the composed-description grammar check inside
`stamp_lpar_ownership`'s best-effort `try` with catch `(HMCCLIError, OSError,
ValueError)` — a config-legal `agent_id` containing `"` otherwise failed the create
tools after LPAR creation — and 2f1d7a2 hoists caller-token validation to the first
statement of `provision_lpar` so direct API callers keep before-any-HMC-traffic
semantics. The candidate approved surface's `stamp_lpar_ownership` clause is amended
accordingly in the oathbind report; no deferrals; ADR 0011 residuals disclosed as
suppressions (post-create stamping window; non-atomic caller-segment write; advisory,
spoofable token). Pre-existing adjacent gap noted for a tracker issue:
`validate_agent_id` permits `"` and `\`, so such an `agent_id` silently skips
stamping (degrades to a warning by design).
