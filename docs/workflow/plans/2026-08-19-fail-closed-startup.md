# Implementation plan — fail-closed composition and the legacy-equivalent generator

**Goal.** No MCP application can be composed without an access policy; both transports, both
in-repo scripts, and the smoke path reach that one composer; and `hmc-mcp config
init-access-policy` writes a reviewable policy granting exactly what the unpolicied server
granted, so an existing deployment can migrate.

**Architecture.** `server.create_mcp(policy)` becomes the single composer and loses its default;
the module-level `server.mcp` is deleted. A new `hmc_mcp.legacy_policy` builds, renders, and
compiles the legacy-equivalent document; `cli_config` writes it; `cli_app.serve` refuses without
`--access-policy`; both scripts compose through the generator.

**Tech stack.** Python 3.11-3.14, `uv`, `pytest`, `typer`, `pydantic`, `fastmcp`, stdlib
`tomllib`. No new runtime dependency (epic #218 requirement 11).

**Design record.** [ADR 0041](../../adr/0041-fail-closed-startup-and-legacy-policy-generation.md).
**Spec.** [2026-08-19-fail-closed-startup-design.md](../specs/2026-08-19-fail-closed-startup-design.md)
— R- and G- identifiers below are its.

## Resume facts

- Branch `feat/fail-closed-startup-225`; `BASE_BRANCH` = `main` (branched at `318d120`).
- Guardrail: `just verify`, run **bare** — no pipes, no `|| true`. Its exit code is the truth.
- Focused loop while building: `uv run --no-sync pytest -q --no-cov <paths>`.
- ADR 0041 is assigned; the repo keeps **no** ADR index, so no index row exists to add.
- Stop at a green, mergeable PR. Do not merge. Do not delete any branch.

## Global constraints

- **Non-interactive shell only** (`AGENTS.md`): `GIT_EDITOR=true`, `git --no-pager`,
  `gh pr create` always with `--title`/`--body`.
- **Coverage gate**: `fail_under = 90` in `[tool.coverage.report]`. New modules need tests.
- **Line length 100**; `ruff`, `ty`, `detect-secrets`, `zizmor`, and the env-var and nicknames
  guards all run under `just static`.
- **Credential-free config in tests**: `HMCConfig(_env_file=None)` — `monkeypatch.delenv` cannot
  stop `pydantic_settings` reading `.env`.
- **CI matrix is Linux only**: `amd64`/`ubuntu-24.04` and `arm64`/`ubuntu-24.04-arm`, Python
  3.11-3.14. There is no macOS or Windows leg, so a POSIX-only test still runs on every leg.
- **`tests/conftest.py`'s `isolate_audit_logging` is autouse** and resets the `hmc_mcp.audit`
  logger at setup and teardown. Work around it; do not modify it casually.
- **Never squash-merge**; conventional commits; never write "closes/fixes/resolves" adjacent to a
  `#NNN` reference in a **commit message** — GitHub parses commit messages and would close the
  issue named. Write "the change addressing #NNN".

## File map

| File | Change | Answerable for |
|---|---|---|
| `src/hmc_mcp/legacy_policy.py` | **new** | Building, rendering, and compiling the legacy-equivalent document |
| `src/hmc_mcp/server.py` | modified | `create_mcp` requires a policy; `mcp` deleted; `_gates` non-optional; `_unselected_policy_file` deleted; warnings and docstrings |
| `src/hmc_mcp/cli_app.py` | modified | `serve`'s `--access-policy` requirement, the guarded path helper, the two refusals, markup escaping in `_fail`/`_usage_error`, help text |
| `src/hmc_mcp/cli_config.py` | modified | `config init-access-policy`; module docstring |
| `scripts/smoke_mcp.py` | modified | Compose through the generator |
| `scripts/live_test_runner.py` | modified | Compose through the generator with the escape hatch opted in, and pass the gates |
| `README.md`, `docs/authorization-audit.md` | modified | Migration section, examples, corrected claims, runnable commands |
| `tests/unit/test_legacy_policy.py` | **new** | R7-R10, R9a, R9b |
| `tests/app/test_fail_closed_startup.py` | **new** | R1-R5b, G1-G3, R12-R14, R16e, L1, L2 + the inventory guard |
| `tests/app/test_cli_config.py` | modified | R11, R11a |
| `tests/app/test_serve.py`, `test_capability_ceiling.py`, `test_connection_authorization.py`, `test_capabilities.py`, `test_tool_security.py`, `test_collection_limits.py`, `test_profile_routing.py`, `test_lifecycle_schema_descriptions.py`, `tests/test_live_runner.py` | modified | R2a, R2b, R2c and the three retired tests |

## Task 1 — `hmc_mcp.legacy_policy`

**Creates** `src/hmc_mcp/legacy_policy.py`. **Tests** `tests/unit/test_legacy_policy.py`.

**Interfaces this task provides** (later tasks rely on these exact signatures):

```python
LEGACY_POLICY_NAME: Final = "legacy-equivalent"
GENERATED_SOURCE: Final = "<generated legacy-equivalent policy>"

def legacy_tools(tool_security: Mapping[str, ToolSecurity], *,
                 include_arbitrary_command: bool = False) -> tuple[str, ...]
def legacy_connections() -> tuple[str, ...]
def legacy_document(tool_security: Mapping[str, ToolSecurity], connections: Sequence[str], *,
                    include_arbitrary_command: bool = False) -> dict[str, Any]
def render_legacy_policy(tool_security: Mapping[str, ToolSecurity],
                         connections: Sequence[str]) -> str
def compile_legacy_policy(tool_security: Mapping[str, ToolSecurity], connections: Sequence[str],
                          *, include_arbitrary_command: bool = False) -> AccessPolicy
```

**Consumes:** `access_policy.ALL_TARGETS_TOKEN`, `DEFAULT_CONNECTION_TOKEN`,
`compile_access_policy`, `AccessPolicy`; `config.list_profiles_and_nicknames`;
`tool_registry.ToolSecurity`. It imports **no** `server` module (R7).

Steps, in TDD order:

1. Write `tests/unit/test_legacy_policy.py::test_the_grant_names_every_ordinary_tool` asserting
   `set(legacy_tools(TOOL_SECURITY)) == set(TOOL_SECURITY) - {"hmc_run_command"}` and that the
   result is sorted. Run `uv run --no-sync pytest -q --no-cov tests/unit/test_legacy_policy.py`;
   expect `ModuleNotFoundError: No module named 'hmc_mcp.legacy_policy'`.
2. Create the module with `legacy_tools` and `legacy_document`. `legacy_document` returns
   `{"policies": {LEGACY_POLICY_NAME: {"grants": [{"tools": [...], "connections": [...],
   "targets": ALL_TARGETS_TOKEN}]}}}` — no `effects` key (R7). Re-run; expect pass.
3. Write `test_the_grant_omits_the_escape_hatch_unless_opted_in` (R7a/R14): absent from
   `legacy_tools(TOOL_SECURITY)`, present under `include_arbitrary_command=True`. Run; expect
   fail, then add the parameter; expect pass.
4. Write `test_connections_put_the_default_first_and_drop_a_colliding_key` (R8): with a
   `config.toml` holding `[profiles.lab]`, `[profiles.prod]`, and `[profiles."<default>"]`,
   `legacy_connections()` returns `("<default>", "lab", "prod")` — the sentinel appears exactly
   once. Steer `config_dir()` per the isolation recipe in Task 6. Implement
   `legacy_connections` over `config.list_profiles_and_nicknames()[0]`, filtering
   `DEFAULT_CONNECTION_TOKEN` out of the profile half.
5. Write `test_rendering_round_trips_through_the_real_loader` (R9): render, `tomllib.loads`,
   `compile_access_policy(..., LEGACY_POLICY_NAME, TOOL_SECURITY, "test")`, assert
   `policy.tools == frozenset(legacy_tools(TOOL_SECURITY))`.
6. Write `test_rendering_escapes_a_hostile_profile_key` (R9a) over the connection list
   `("<default>", 'a"b', "c\\d", "e\nf", "g\x7fh")`; assert the rendered text parses and the
   connections round-trip byte-identically. Implement `_escape` for `"`, `\`, U+0000-U+001F and
   U+007F, using `\"`, `\\`, `\n`/`\r`/`\t` where TOML names them and `\uXXXX` otherwise. C1 is
   deliberately **not** escaped — checked against this checkout's `tomllib`: raw U+007F is
   rejected, raw U+0085 parses.
7. Write `test_compile_legacy_policy_needs_no_filesystem` (R10): assert
   `compile_legacy_policy(TOOL_SECURITY, ("<default>",)).source == GENERATED_SOURCE` and that it
   holds no path separator.
8. Add the header comment to `render_legacy_policy`'s output (R9): what the policy grants; that
   it is a migration aid rather than a recommended posture; how to add `hmc_run_command`; the
   `--output`, diff, merge-by-hand regeneration procedure; and that a hand-added
   `hmc_run_command` line always shows as a deletion on regeneration.

**Acceptance:** `legacy_tools`, `legacy_connections`, `render_legacy_policy` and
`compile_legacy_policy` exist with the signatures above; the rendered document loads through the
real loader; a profile key holding a quote, a backslash, a newline, or U+007F round-trips.

## Task 2 — fail-closed composition in `server.py`

**Modifies** `src/hmc_mcp/server.py`. **Tests** `tests/app/test_fail_closed_startup.py`.

**Consumes:** Task 1's `compile_legacy_policy`. **Provides:** `create_mcp(policy: AccessPolicy)`.

1. Write `test_composing_without_a_policy_is_refused` (R1/G1): `pytest.raises(TypeError)` for
   `create_mcp()` **and** for `create_mcp(None)`, with the second's message naming
   `hmc-mcp config init-access-policy`. Run; expect both to fail (today `create_mcp()` succeeds).
2. Change the signature to `def create_mcp(policy: AccessPolicy) -> FastMCP:` and open the body
   with an explicit guard — an annotation does not refuse at runtime, and `None` would otherwise
   compose an unbounded application. Rewrite the docstring, which currently names #225 as future
   work. Re-run; expect pass.
3. Delete `mcp = create_mcp()`. Retype `_gates(policy: AccessPolicy) -> tuple[Callable[[str],
   bool], Authorize]` and drop its `None` arm, keeping its ADR 0038 "derived together" docstring.
4. Retype `_serve_application`, `main_stdio`, `main_http` to `access_policy: AccessPolicy` with
   no default (R4).
5. Delete `_unselected_policy_file` and the `access_policy is None` branch of
   `_startup_warnings`; retype its parameter to `AccessPolicy` and drop the two `is not None`
   guards that are now dead (R6).
6. Update the module docstring's `Run:` block so both invocations carry `--access-policy NAME`
   (R16c).
7. Write `test_the_legacy_policy_composes_the_registry_the_default_used_to` (G2):
   `_names(create_mcp(compile_legacy_policy(TOOL_SECURITY, ("<default>",)))) == set(TOOL_SECURITY)
   - {"hmc_run_command"}` — the exact assertion the retired `test_no_policy_applies_no_ceiling`
   made about `create_mcp()`.
8. Write `test_no_module_composes_an_application_at_import` (G3): walk `src/hmc_mcp/*.py` and
   `scripts/*.py` with `ast`, and assert no module-level `create_mcp(...)` call; assert
   `not hasattr(server, "mcp")`.

**Acceptance:** `create_mcp()` and `create_mcp(None)` both raise `TypeError`;
`from hmc_mcp.server import mcp` raises `ImportError`; the legacy-equivalent composition
registers exactly the 129 ordinary tools.

## Task 3 — `serve` refuses, and CLI text stops lying

**Modifies** `src/hmc_mcp/cli_app.py`.

1. Write `test_serve_without_a_policy_exits_2` and
   `test_serve_with_an_absent_policy_file_exits_1` (R5, R5a) using `typer.testing.CliRunner`,
   asserting the exit codes and that the output names `hmc-mcp config init-access-policy`.
2. Add the guarded resolver:

   ```python
   def _policy_file() -> tuple[str, bool] | None:
       """The access-policy path and whether it exists, or None if it cannot be resolved.

       `config_dir()` reaches `Path.home()`, which raises under a uid with no passwd entry
       and no HOME. A refusal that raises while rendering itself is worse than one that
       omits the path, so an unresolvable path returns None and falls through to
       `load_access_policy`, whose own guard reports it.
       """
       try:
           path = resolve_access_policy_path()
           return str(path), path.exists()
       except (RuntimeError, OSError, ValueError):
           return None
   ```

   `tuple[str, bool] | None`, not `str | None`: R5 needs the path exactly when the file is
   absent, which a collapsed `None` would hide (R5b).
3. In `serve`, before composing anything: if `access_policy is None`, `_usage_error(...)` (exit 2)
   with the migration text; else if `_policy_file()` returns `(path, False)`, `_fail(...)`
   (exit 1) with the same text. Both name the generator, the path when known,
   `--access-policy NAME`, and the README's narrower examples (R5, R5a).
4. Escape interpolated text in `_fail` and `_usage_error` with `rich.markup.escape`, keeping the
   styled `Error:` prefix (R16e). Write `test_error_text_survives_square_brackets` first: today
   a `[profiles." prod"]` token is silently deleted and a `'[/prod]'` token raises
   `rich.errors.MarkupError`.
5. Rewrite the `--access-policy` option help and the `serve` docstring: the option is required
   and names the generator (R16c).

**Acceptance:** no `--access-policy` → exit 2, nothing served; named-but-absent → exit 1 naming
the generator; a bracketed exception message reaches stderr intact.

## Task 4 — the generator command

**Modifies** `src/hmc_mcp/cli_config.py`. **Tests** `tests/app/test_cli_config.py`.

1. Write `test_generating_writes_a_loadable_policy_at_0600`,
   `test_generating_twice_refuses_and_leaves_the_file_byte_identical`, and
   `test_output_redirects_the_write` (R11, R11a).
2. Implement `config_init_access_policy` with one option, `--output PATH`. Order: resolve the
   destination through the same guard as Task 3 (R11); read connections (R8); render (R9);
   **parse and compile the rendered text before creating anything** (R9b), catching
   `AccessPolicyError` and re-raising with a clause naming `config.toml`, the offending key, and
   the two remedies; then `mkdir(parents=True, exist_ok=True)` and the `O_EXCL`/`0o600` create,
   mirroring `config_init`'s two-branch POSIX/win32 shape.
3. Wrap the write and close so a post-create failure **unlinks** the destination before
   reporting (R11): `O_EXCL` alone gives "no partial file" only for failures at `os.open`, and a
   truncated file here blocks its own regeneration and refuses to load.
4. Give `FileExistsError` a remedy as well as a path — regenerate to a scratch path with
   `--output` and merge by hand (R11).
5. Print the written path to stdout and the activation hint to stderr, both escaped (R16e).
6. Broaden `config_app`'s help beyond "Profile configuration commands." and add the command to
   `cli_config.py`'s module docstring (R16d).

**Acceptance:** the command writes a file that `load_access_policy` accepts; a second run exits 1
with the file unchanged byte-for-byte and a message naming the remedy; `--output` redirects.

## Task 5 — the two scripts, and every affected test

**Modifies** `scripts/smoke_mcp.py`, `scripts/live_test_runner.py`, and the test modules in the
file map.

1. `smoke_mcp.py`: `create_mcp(compile_legacy_policy(TOOL_SECURITY, (DEFAULT_CONNECTION_TOKEN,)))`
   inside `main()`, not at module scope (R15, G3).
2. `live_test_runner.py`: the same with `include_arbitrary_command=True`, and pass `permits` and
   `authorize` derived from that policy into `configure_arbitrary_command_tool` (R15a) — today it
   calls `(True, mcp)`, so `permits=None` registers regardless of policy and `authorize=None`
   leaves the handler unwrapped.
3. Update `tests/test_live_runner.py`'s `configure` double for the new call signature (R2b).
4. The five `server.mcp` importers each bind a module-level
   `create_mcp(compile_legacy_policy(TOOL_SECURITY, (DEFAULT_CONNECTION_TOKEN,)))` (R2a). No
   assertion changes: all five use it only for `list_tools()`, which the wrapper leaves
   untouched.
5. The five bare `create_mcp()` call sites compose through the legacy-equivalent policy (R2c).
6. `tests/app/test_serve.py`: invert every assertion to the new contract — a real `AccessPolicy`
   forwarded rather than `None`, the gate pair non-`None`, and new exit-code assertions for the
   two refusals (R2b).
7. Invert the three retired tests named in the spec's *Retired tests* section rather than
   deleting them.

**Acceptance:** `just verify` runs green; `uv run --no-sync python scripts/smoke_mcp.py` prints
the tool count.

## Task 6 — proof that bites, and the live checks

**Tests** `tests/app/test_fail_closed_startup.py`.

1. `test_the_generated_policy_authorizes_an_omitted_optional_selector` (R12) — drive a tool with
   an optional target selector left unset through the compiled legacy policy's authorizer and
   assert it is permitted. This is the property that makes the generator legacy-*equivalent*:
   `all-targets` covers `target_scope.ABSENT` and no `targets` table does.
2. `test_the_generated_policy_authorizes_a_vios_partition_id_call` (R13) — the surface's only
   `int` selector, carried by three live tools, all non-exhaustive.
3. **L1** — `subprocess.run([sys.executable, "-m", "hmc_mcp", "serve"])` (or the console script)
   asserting exit 2 and that stderr names the generator. Portable: no shell redirection, no
   `HOME` steering.
4. **L2** — the migration end to end, POSIX-only. Isolation, stated because L2 is the only check
   that **writes**: set `HOME` to a `tmp_path`, **remove `XDG_CONFIG_HOME` and `APPDATA` from
   both this process and the child environment**, patch `pathlib.Path.home`, and derive the
   target from `config_dir()` after that steering. Setting `HOME` alone does not steer
   `config_dir()` on Linux — it reads `XDG_CONFIG_HOME` first — and the failure mode is writing
   a real `access-policy.toml` into the developer's own config directory.
5. Add `test_every_spec_numbered_test_named_in_the_header_still_exists`, copying the inventory
   guard in `tests/unit/test_audit.py`: the module header maps each R/G/L identifier to a node
   id, and the guard fails when one stops existing. On #224 a slice-replacement refactor deleted
   the only test that could see a regression and left 2011 tests green.
6. **Mutation-check every pinned claim**, both directions: break the production line, run the
   named test, confirm it reddens, restore, confirm it passes. A mutation that reddens nothing is
   a finding about the test, not a pass. At minimum: the `create_mcp` `None` guard, the
   `<default>` filter in `legacy_connections`, the `hmc_run_command` exclusion, the `O_EXCL`
   flag, the escape function, and the pre-write compile.

**Acceptance:** every R/G/L identifier in the spec has a test; each pinned claim reddens when its
production line is broken and passes when restored; both directions reported.

## Task 7 — documentation

**Modifies** `README.md`, `docs/authorization-audit.md`.

1. Every runnable `hmc-mcp serve` invocation gains `--access-policy NAME`, and the quick-start
   block is ordered so the generator command comes first — including the `hermes mcp add`
   client-registration line, which is the one whose failure a client reports as "server failed to
   start" rather than surfacing the refusal text (R16a).
2. A migration section: the refusal, the generator, the two exit codes, `--output` regenerate-and-
   diff over **both** the `tools` and `connections` arrays, the recovery for a file that exists
   but will not compile, the generator-identity requirement, the resolvable-`HOME` requirement,
   and the loss of `hmc_run_command` for a deployment that ran with `--enable-arbitrary-command`
   (R16).
3. One sentence keeping the two files distinct: `config.toml` holds HMC **connection profiles**,
   `access-policy.toml` holds **server access policies**, separate lifecycles, and `connections`
   entries are profile *keys* (R16b).
4. Drop the "`access-policy.toml` exists but `--access-policy` was not passed" startup-warning
   row (R6).
5. Correct "Without `--access-policy NAME` there is no authorizer, so no *authorization* record is
   written" in both files: every deployment now writes one record per decision, and something
   must drain fd 2 on **both** transports (#269) (R16).
6. Keep the read-only example, keep the limited-mutation example, add an abbreviated
   legacy-equivalent one, and say the legacy-equivalent policy is a migration aid rather than a
   recommended posture for a fresh install.

**Acceptance:** no command in the docs exits non-zero when followed; both corrected claims are
gone; the three examples are present.

## Task 8 — guardrails and hand-off

1. `just verify` — **bare**. Expect the full suite green, the smoke handshake printing its tool
   count, the build producing a wheel and sdist, and `verify: all groups load OK`.
2. Commit per logical change; never `git add -A`.
3. `$trial-loop --base main` over the branch, then `$detect-evil` (the diff changes an
   authorization boundary, a CLI entry point, and file-writing behaviour, so the security pass is
   triggered), then `$dispel`, then `$deliver 225`.
4. PR body references `Closes #225` so `closingIssuesReferences` is exactly `[225]`. **No commit
   message** puts "closes"/"fixes"/"resolves" next to a `#NNN`.
