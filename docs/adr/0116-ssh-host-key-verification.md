# ADR 0116: Operator-controlled SSH host-key verification

## Status

Accepted — 2026-09-04. Implements issue #605.

## Context

Both production SSH entry points and the operator probe unconditionally pass
`known_hosts=None`. This accepts an intercepting peer's key before password
authentication. Issue #605 delegates the configuration shape to implementation.

## Decision

Add `ssh_verify_host_key: bool = True` to `HMCConfig`, available through profiles
and `HMC_SSH_VERIFY_HOST_KEY`. Verification explicitly uses the process user's
`~/.ssh/known_hosts`. Explicit `False` passes `None` and logs a warning naming
the destination before each connection. Password/key selection remains unchanged.

The standalone probe verifies against the same file by default. Only its
per-run `--insecure` flag disables verification and prints a destination-specific
warning to stderr before connecting. It does not inherit production opt-outs.

## Consequences

Upgrades require operators to provision trusted keys before using SSH. Missing,
unreadable, unknown, or changed trust entries fail without insecure fallback.
Operators must obtain/confirm fingerprints independently before installing keys;
the application never enrolls keys automatically. Production errors retain the
existing `HMCCLIError` boundary; the probe retains its connection-error report.
Only this known-hosts file is supported; SSH config cannot silently select `none`.
Explicit opt-out accepts credential interception risk. REST TLS is unchanged.

## Considered & rejected

- **Unverified default with a warning.** judgment: secure defaults make bypass
  an actual operator decision, satisfying the issue's expected behavior directly.
- **Boolean plus a custom trust-file setting.** judgment: the standard operator-owned
  known-hosts file meets the requested trust choice with one field.
- **Omit `known_hosts` and inherit SSH config.** verified: AsyncSSH 2.24.0's
  `SSHClientConnectionOptions.prepare` maps `UserKnownHostsFile none` to `None`
  (inspected `asyncssh/connection.py`, lines 8246–8255), bypassing verification.
- **Leave the current behavior.** judgment: leaves the reported credential exposure
  and no operator control.
