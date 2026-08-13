# ADR 0006: Platform-Native TOML Profile Loader for HMC Connections

## Status

Accepted

## Context

`hmc-mcp` reads one HMC connection from environment variables and a
checkout-local `.env` file. Operators who manage several HMC environments must
export and swap variables manually; the `.env` approach leaks credentials into
the source checkout and does not follow OS convention for user-level
configuration. Issue #124 asks for a multi-profile TOML file at the
platform-native location.

## Decision

Add a profile loader in `src/hmc_mcp/config.py` that:

1. Resolves `hmc-mcp/config.toml` from the platform-native directory (XDG on
   Linux, `~/Library/Application Support` on macOS, `%APPDATA%` on Windows).
2. Parses named profiles using `tomllib` (Python ≥3.12 stdlib — no new
   dependency).
3. Selects a profile via: explicit arg → `HMC_PROFILE` env var →
   `default_profile` in TOML → error.
4. Builds `HMCConfig` from the selected profile, with env vars overriding TOML
   (existing `env_prefix="HMC_"` semantics preserved) and explicit constructor
   args overriding env vars.
5. Removes `env_file=".env"` from `HMCConfig` — the TOML file replaces the
   `.env` file's role; checkout-local credential files are no longer loaded.

## Consequences

- Operators with an existing `.env` file must migrate to
  `~/.config/hmc-mcp/config.toml` (or platform equivalent). The migration is
  one-time and documented in README.
- Tests that relied on `_env_file=None` to suppress `.env` loading continue to
  work unchanged; the parameter is still accepted (it is a pydantic-settings
  parameter, not custom).
- `list_profiles()` exposes profile names without resolving secrets; this is
  safe for tab-completion or diagnostics.
- `password_env` enables a common pattern where the TOML file stores the
  variable name and the secret itself remains in the process environment
  (e.g., from a secrets manager). Resolving at construction time (not at list
  time) keeps the inventory secret-free.

## Considered & Rejected

**Keep `.env` and add TOML on top.** Loading both files adds complexity and
two sources of truth. The TOML profile covers the same credential-supply role;
keeping `.env` loading creates a confusing precedence question and leaves
credentials at the checkout root.

**New PyPI dependency (python-dotenv, dynaconf, etc.).**  `tomllib` is stdlib
in Python ≥3.12, which is already required. No additional dependency is needed.

**Single-profile TOML (no `[profiles]` table).** A flat TOML with one profile
gives no migration benefit for multi-HMC operators and forces a second design
cycle when they inevitably need named profiles.

**Store secrets in OS keychain.** Out of scope for this issue (part of the
larger epic #123). `password_env` provides a bridge: the operator can place
the secret in the keychain and expose it via a small shell wrapper without
requiring a keychain integration in this PR.
