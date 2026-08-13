# ADR 0007: CLI Configuration Command Group

## Status

Accepted

## Context

Issue #124 landed the platform-native TOML profile loader (`load_profile`,
`list_profiles`, `resolve_config_path`) with no CLI surface. Operators must
know the platform-native path by heart to inspect or bootstrap their
configuration. Issue #125 adds the CLI face for this loader: a `config`
subgroup with `init`, `list`, and `show` commands.

The global `--profile` flag was already wired into `GlobalOpts` and the root
callback in the same PR that merged #124; this ADR only governs the new
subcommand group.

## Decision

Add a `cli_config.py` module that registers on a new `config_app` Typer wired
into the root CLI. The three commands delegate entirely to the existing
`config.py` API (`resolve_config_path`, `list_profiles`, `load_profile`);
`cli_config.py` owns only presentation and the `init` write path.

`config show` inspects the loaded `HMCConfig` and emits non-secret metadata.
It never resolves `password_env` — it reports only whether a password or key
credential is configured. This keeps the command safe to run in any environment
regardless of whether the referenced env var is present.

`config init` writes the platform-native path returned by
`resolve_config_path()` logic (i.e., the same path resolution, without the
existence check). On POSIX it applies mode `0o600` immediately after creation.
On Windows, `0o600` is a no-op; the file inherits the user-account ACL from
`%APPDATA%`, which is accepted as the Windows security posture for this issue.

## Consequences

- Operators get a `hmc-mcp config init` bootstrap command that creates a
  well-commented starter TOML, prints the created path on success, and exits
  non-zero with a message if the file already exists.
- `hmc-mcp config list` is safe for tab-completion and scripting; it exits 0
  even when no config file exists.
- `hmc-mcp config show` exits non-zero with a human-readable message when no
  profile is resolvable (no config file, no matching profile name, no
  default). It does not attempt to resolve `password_env`.
- `hmc-mcp config show` can reveal hostname and username; it cannot reveal
  passwords or private-key contents. This is the same exposure level as
  `hmc-mcp --help`.
- Adding `from . import cli_config` to `cli.py` is the only change outside the
  new file and the two lines in `cli_app.py` that register the group.

## Considered & Rejected

**No CLI surface (rely on text editor + README).** The loader API from #124 is
fully functional without any CLI wrapper. Operators can locate the platform
path from the README and write a config file directly. This is rejected because
`config init` does work a text editor cannot: it creates the directory tree if
absent and applies restrictive file permissions (`0o600`) on first creation.
`config list` and `config show` reduce the support burden for operators who
need to verify their setup without inspecting raw TOML.

**Inline `config` commands in `cli_app.py`.** The pattern in this repo is one
file per domain group. Inlining would make `cli_app.py` the right place to look
for connection-plumbing code and the wrong place to look for command bodies —
they serve different readers.

**`config show` resolves `password_env`.** Resolving the referenced env var
would fail with a `ConfigError` when the variable is absent (common in developer
environments). The command's purpose is inspection, not connection — reporting
"password configured via env var FOO" answers the inspection question without
requiring the secret to be present.

**`config init` uses user-supplied path.** The platform-native path is
deterministic and does not need a flag. A `--path` override would create a
file the loader would never read, confusing operators. If the path must be
overridden, `HMC_CONFIG_FILE` or a future `--config` flag is the right vehicle.
