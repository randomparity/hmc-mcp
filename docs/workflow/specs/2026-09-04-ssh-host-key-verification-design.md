# SSH host-key verification — issue #605

Authority: issue #605, frozen scope token `q605-7ad983e1`. The issue delegates
the final shape; campaign dispatch assigns ADR 0116 and direct documentation/tests.
Decision: [ADR 0116](../../adr/0116-ssh-host-key-verification.md).

## Behavior and acceptance

Production exposes `ssh_verify_host_key`, default true, via existing configuration
and profile/env loading. Both command and console connections pass the expanded
process-user `~/.ssh/known_hosts` path. False passes `None` and logs a warning
before authentication. Invalid booleans fail configuration validation. Environment
precedence stays unchanged. Existing password-only authentication is preserved.

The probe accepts `--insecure` through argparse and threads it through `main`,
`probe_profile`, and `connect_kwargs`; all default false. Each bypass warns on
stderr, naming the host before connection. Default verification uses the same
explicit trust file. Production profile bypasses never disable probe checking.

Missing/unreadable trust files and unknown/changed keys refuse authentication
without retrying insecurely. Existing transport error translation/reporting applies.
HMC_ACCESS and environment docs explain trust enrollment and both opt-outs;
the OpenSSH recipe also requires host-key checking.

## Threat model

Existing boundary narrowed: a network peer supplies a server key before receiving
operator credentials. AsyncSSH matches that key against operator-owned known_hosts.
No trust boundary is widened. Operators control config/env, command flags, the
process account, and the trust file; a local actor controlling those is trusted.
The adversary is a management-network interceptor presenting a substituted key.
Warnings expose destination identity locally but never passwords or key material.
No automatic enrollment, remote credential use in tests, TLS change, or protection
against a compromised operator account is claimed.

## Verification

Behavioral tests cover both production entry points/auth modes, defaults, false
warnings, profile/env precedence, validation, probe argument propagation/warnings,
and a local AsyncSSH server rejecting unknown/changed keys before password auth,
accepting a trusted key and accepting explicit bypass. Fixtures own temporary
trust files and server cleanup. Missing-file failures use the existing error path.

## Global Constraints

Python 3.11–3.14; Linux amd64 and arm64. No new dependencies or toolchain floors.
Sibling worktree; `just setup`; all `uv run` calls use `--no-sync`.
Run `just test`, `just smoke`, `just verify`, and `uv run --no-sync prek run --all-files`.
No live HMC credentials are needed; local SSH handshakes prove transport behavior.
