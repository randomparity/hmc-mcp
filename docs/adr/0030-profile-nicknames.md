# ADR 0030: Resolve HMC Profile Nicknames (Friendly Names)

## Status

Accepted (2026-08-16)

## Context

Operators running several HMCs must remember the exact `config.toml` profile key
(`prod`, `stg-hmc-03`) for every `--profile` / `HMC_PROFILE` CLI call and every
MCP `profile=` argument. Issue #226 asks for a friendly nickname (`big-iron`,
`staging`) that maps to a profile, so both surfaces become friendlier without a
second selection mechanism.

Today a name that is not a profile key is a hard `ConfigError`
(`config.py`, the `if name not in profiles` branch of `load_profile`). The only
options are to name the profile memorably or duplicate the profile table.

Name selection is a single concern already: `load_profile` picks a name
(explicit arg → `HMC_PROFILE` → `default_profile`) and then looks it up in
`doc["profiles"]`. The CLI (`cli_commands/app.py` `--profile` → `client_from_env`) and
every MCP tool (`server_tools/systems.py` and siblings call `client_from_env(profile)`)
all funnel through `load_profile`, so resolving a nickname there reaches both
surfaces with no per-tool change. This builds on ADR 0006 (TOML profile loader),
ADR 0007 (CLI config commands), ADR 0008/0009 (per-call profile routing), and
ADR 0010 (`hmc_list_configured_hosts`).

## Decision

Add a top-level `nicknames` table that maps a friendly name to an existing
profile key:

```toml
nicknames = { "big-iron" = "prod", "staging" = "stg-hmc-03" }
```

1. **Resolution is a name-selection concern.** Inside `load_profile`, after
   name selection and before the not-found check, when `name` is not a profile
   key, look it up in `doc.get("nicknames", {})`. On a hit whose target is a
   real profile key, substitute the target (one level, no recursion). A profile
   key always wins over a same-named nickname (the lookup only runs when the
   name is *not* a profile key). Matching is case-sensitive.
2. **One level deep.** After substituting the target we do not re-consult
   `nicknames`, so chained nicknames and cycles are impossible by construction.
3. **Clear failures.** A nickname whose target is not a profile, an unknown
   name, or a malformed `nicknames` table raises a `ConfigError` naming the
   available profiles and nicknames.
4. **Surfaced, not hidden.** `config list`, `config show <nick>`, and
   `hmc_list_configured_hosts` show each nickname and the profile it resolves to
   (and flag a dangling target), without resolving any secret. A new secret-free
   inventory helper `list_nicknames(config_path)` returns the `nicknames`
   table as `dict[str, str]` (empty when absent); all three display surfaces
   use it. `load_profile` and `list_nicknames` share one malformed-table
   validation helper.
5. **Guardrail.** `scripts/check_nicknames.py` validates a committed fixture
   config: every nickname target exists, no nickname key collides with a profile
   key, no target is itself a nickname key (no chain), and a malformed table
   fails. It is wired into `just static` (hence `just verify`) and the
   pre-commit suite, mirroring `scripts/check_env_vars.py`.
6. **Docs.** `config init` scaffolds a commented `nicknames` example;
   `README.md` and `docs/environment-variables.md` document the schema,
   precedence, and collision/case rules.

## Consequences

- A nickname works identically through `--profile`, `HMC_PROFILE`,
   `default_profile`, the CLI, and every MCP tool, because they all resolve
   through `load_profile`.
- `list_profiles_with_default` and `list_profiles` keep their existing return
   arities; nickname surfacing flows through the new `list_nicknames` helper
   instead. This avoids a gratuitous breaking change to two stable internal
   signatures in exchange for one focused new function (see Considered &
   rejected).
- The public reusable API (`hmc_mcp.api.__all__`) is unchanged; nickname
   resolution is an internal config concern, not a new export.
- Dangling or colliding nicknames are caught at CI time by the guardrail and at
   runtime by `load_profile`; operators see the full name inventory via the
   display surfaces.

## Considered & rejected

**Extend `list_profiles_with_default` to return a 3-tuple.** Rejected: it is a
gratuitous breaking change to a stable internal signature with one caller and
three tests, for no expressiveness gain. A parallel secret-free
`list_nicknames` is clearer and lower-ripple.

**Allow chained nicknames / cycles.** Rejected as a non-goal (issue #226): one
level deep is the specified contract; recursion adds an unbounded failure mode
with no expressed benefit.

**Add a CLI command to create a nickname.** Rejected as a non-goal: operators
hand-edit the TOML; `config init` only scaffolds a commented example. A writer
would need credential-aware validation and a second selection mechanism.

**Validate the `nicknames` table only lazily on nickname lookup.** Rejected: a
malformed table is a config error regardless of which profile is selected, so
`load_profile` validates the structure whenever the key is present. No existing
config carries a `nicknames` key, so this cannot break current users.

**Validate only the `config init` starter TOML.** Rejected: its `nicknames`
example is intentionally commented out, so it has nothing parseable to check.
A dedicated, committed, parseable fixture is the guardrail target.
