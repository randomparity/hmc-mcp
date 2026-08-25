# ADR 0096: Environment-isolated HMCConfig construction

## Status

Accepted (2026-08-25)

## Context

`HMCConfig` (`src/hmc_mcp/config.py:121`) is a pydantic-settings `BaseSettings`
with `env_prefix="HMC_"`. Constructor arguments win, but **every field left unset
resolves from the ambient process environment**. That is exactly right for the CLI
and the MCP server, which are single-connection processes an operator configures
through `HMC_*` and a TOML profile.

It is wrong for a library consumer. A multi-backend server that builds one
`HMCConfig` per HMC from database rows inherits the ambient environment for every
column a row omits. A stray `HMC_HOST` points a backend at a different HMC than
its row names. A stray `HMC_SSH_KEY_FILE` offers the wrong private key. A stray
`HMC_AGENT_ID` corrupts ADR 0011 ownership attribution on every LPAR the process
stamps. None of it raises; it produces plausible, wrong behaviour.

The package's own multi-connection path already sits on this.
`_load_profile_from_document` builds a per-profile config as
`HMCConfig(_env_file=None, **filtered_entry)` (`config.py:557`), and any field
absent from the profile entry still resolves from `HMC_*`.

`_env_file=None` is the trap that makes this worse. It is the obvious thing a
consumer reaches for, `AGENTS.md` currently teaches it as *the* credential-free
idiom, and it does not do what its use sites imply: pydantic-settings builds four
sources — init kwargs, `EnvSettingsSource`, `DotEnvSettingsSource`, and the
secrets directory — and `_env_file=None` disables only the third. Environment
variables are unaffected.

For `HMCConfig` it does even less than that. `_env_file` defaults to a sentinel
that resolves to `model_config["env_file"]`, and `HMCConfig`'s `model_config`
declares no `env_file` — so no dotenv source is ever configured and **passing
`_env_file=None` changes nothing at all**. `AGENTS.md:85-92` asserts the opposite
("`pydantic_settings` reads `env_file=".env"` at construction time") and, on that
basis, instructs maintainers to *delete* the `monkeypatch.delenv` calls that are
the only thing actually isolating those tests. Both halves of that guidance are
wrong, and the second one removes real isolation in exchange for none.

Documentation alone would leave the trap live for every consumer who does not read
the document, and the mistake is silent, so nothing teaches them afterwards.

## Decision

### 1. `HMCConfig.from_mapping` is the environment-isolated construction path

```python
@classmethod
def from_mapping(cls, values: Mapping[str, Any]) -> HMCConfig
```

`from_mapping` reads no environment variable, no `.env` file, and no secrets
directory. Every key in *values* that names a field is applied; every field
*values* omits takes its declared field default and nothing else.

Isolation is by construction rather than by configuration: `from_mapping`
enumerates `cls.model_fields` and passes **every** field as an init kwarg, filling
omissions from `FieldInfo.get_default()`. Init kwargs are pydantic-settings' own
highest-priority source, so no lower-priority source has a field left to supply.
It therefore does not pass `_env_file=None`, does not name any source, and does
not depend on a `settings_customise_sources` override — it holds even if a future
`model_config` adds `env_file`, a secrets directory, or another source entirely.

Validation is unchanged: `from_mapping` calls the normal constructor, so field
validators, the `agent_id` grammar check, and the audit-memento model validator
all run exactly as they do for `HMCConfig(...)`.

Keys in *values* that name no field are ignored, matching the `extra="ignore"`
already declared in `model_config`. `from_mapping` differs from `HMCConfig(...)`
in exactly one dimension — environment isolation — and in no other.

A field that is required and absent from *values* raises `ValueError` naming it.
Every field is optional today, so this cannot fire; it exists because the
alternative failure is the bug this ADR removes. A future required field, silently
omitted from the explicit kwargs, would fall back to `EnvSettingsSource` and
reopen the leak on the one path that promises it is closed.

### 2. `load_profile` is unchanged

Environment-over-TOML precedence on the profile path is deliberate: an operator
overriding a committed profile with `HMC_HOST` for one invocation is the CLI's
documented behaviour (`config.py:546-556`, `docs/environment-variables.md`).
`from_mapping` is **additive**. `load_profile` and `_load_profile_from_document`
keep their current precedence and keep using the ordinary constructor.

A consumer who wants a TOML profile *without* environment precedence composes it:
read the profile table, then hand it to `from_mapping`.

### 3. ADR 0029 surface and release class

`HMCConfig` is already in `hmc_mcp.api.__all__`. ADR 0029 declares "the fields and
constructor of an exported package-owned model" supported; a classmethod is
neither, so this ADR extends that model's supported surface by one named member:
`HMCConfig.from_mapping`, with the signature and isolation guarantee above.

Consequences of choosing a classmethod on an already-exported model over a new
module-level export:

- `api.__all__` does not grow, so the ADR 0029 manifest test is untouched.
- The frozen signature digest in `tests/unit/test_public_api.py` does **not**
  move. That digest hashes `inspect.signature` of each exported name; for a
  pydantic model that is the `__init__` signature, which is derived from fields.
  Adding a method changes no field.
- It is still additive to the supported surface, so it is still a minor release
  under ADR 0029's `0.x` rule and rides the `0.2.0` of #361.

`tests/unit/test_public_api.py` pins `from_mapping`'s presence and signature so
the promise in §1 is enforced where the rest of the facade contract is.

### 4. Documented precedence

`docs/environment-variables.md` gains a "Library consumers" section stating the
resolution order — init kwargs > `HMC_*` environment > `.env` > field default —
naming `from_mapping` as the isolated path, and stating explicitly that
`_env_file=None` suppresses dotenv only.

The exhaustive list of `HMC_*`-backed fields is the existing `## Reference` table.
It is not restated: a second copy is a second thing to go stale, and this document
is where the trap gets set. `scripts/check_env_vars.py` (via `just env-vars`) and
`tests/test_env_var_guard.py` already fail when a field is missing from that
table; this change adds the opposite direction, so a row that no longer names a
field also fails.

`AGENTS.md` stops teaching `_env_file=None` as the credential-free idiom and
teaches `from_mapping` instead, and stops instructing maintainers to delete the
`monkeypatch.delenv` calls that are doing the actual isolating.

`_load_profile_from_document` keeps its `_env_file=None` argument — removing an
inert argument on the operator path buys nothing and would change behaviour if a
future `model_config` did declare `env_file`. Its comment is corrected to say what
the argument does rather than what it looks like it does. The same inert argument
at `common.py:63`, `common.py:76`, and `tests/conftest.py:285` is out of this
change's scope and tracked separately.

## Consequences

- A consumer building configs from rows, a dict, or JSON has one call that cannot
  inherit ambient state, and the package documents which one it is.
- Two construction paths exist with different semantics. That is the point — the
  operator path *should* read the environment — but the difference has to be
  documented rather than discovered, which §4 covers.
- `from_mapping` iterates `model_fields` per call. `HMCConfig` has twelve fields
  and construction already runs full pydantic validation; the enumeration is not
  measurable next to it.
- `0.2.0` carries one more supported member. No existing call changes.

## Alternatives considered

**Documentation only (issue #368 option B).** Changes no contract and moves no
digest, but leaves the trap armed: `_env_file=None` still looks like isolation at
every call site, and a consumer who gets it wrong gets no error. Rejected as the
whole fix; adopted as part of it (§4).

**`settings_customise_sources` on an isolated subclass.** Returning
`(init_settings,)` from a `HMCConfig` subclass is the pydantic-settings-blessed
way to drop sources. It makes `from_mapping` return an instance of a private
subclass, so `type(cfg)` is not `HMCConfig` and pydantic's `__eq__` — which
compares classes — reports two configs with identical fields as unequal. Building
the subclass and then re-validating into a real `HMCConfig` avoids that but runs
every validator twice, firing the audit-memento `UserWarning` twice for one
construction. Rejected.

**Changing `HMCConfig` to not read the environment by default.** Correct for
consumers, breaking for every operator and for the CLI, the MCP server, and
`load_profile`. Rejected.

**A module-level `hmc_mcp.api.config_from_mapping`.** Equivalent behaviour, but
grows `api.__all__`, moves the frozen digest, and separates the isolated
constructor from the model it constructs. Rejected.
