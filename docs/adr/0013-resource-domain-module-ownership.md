# 0013 — Resource-domain module ownership

## Status

Accepted (2026-08-14)

## Context

The server and CLI grew from a few broad modules into resource-domain modules.
Several accepted ADRs still name removed modules such as `server_tools/system.py`,
`server_tools/power.py`, and `server_tools/cli.py`. Their behavioral decisions remain in
force, but those paths no longer describe where the behavior is owned.

## Decision

Presentation adapters are grouped by resource domain:

- `server_tools/` modules own MCP tool definitions and `cli_commands/` modules
  own Typer commands. `cli_commands/app.py` owns the shared Typer application
  and presentation helpers; resource modules register commands on that application.
- `server.py` and `cli.py` are composition entry points, not domain owners.
- `client/` owns REST transport operations, while the `ssh_*.py` modules own
  operations implemented through the HMC CLI.
- Shared workflows and policies used by both presentations belong in
  presentation-neutral `operations/` modules.
- `resource_identity.py` owns REST-facing name/UUID resolution.
  `operations/ownership.py` owns the LPAR ownership protocol and authorization-name
  resolution; neither concern belongs to a presentation adapter.
- Cross-domain request construction remains in `documents.py`, `jobs.py`, and
  `client/pcm_payloads.py`; configuration remains in `config.py`, and selector helpers live
  beside their transport boundary (`ssh/selectors.py` for HMC CLI selectors).

This ADR supersedes only the module-path and ownership statements in ADRs
0005, 0008, 0009, 0010, and 0011. Their public contracts, profile-routing
rules, provisioning behavior, and ownership protocol remain accepted.

## Consequences

Architectural documentation can name stable ownership categories without
depending on a removed broad module. New behavior goes into the matching
resource adapter and, when shared across MCP and CLI, a neutral operation.
Moves between domain files do not require a new ADR unless they change these
ownership boundaries or a public contract.

## Considered & rejected

**Rewrite the older ADRs as if they named the current files.** ADRs record the
decision at the time it was made. Rewriting their history would hide why later
structure differs and make the architectural sequence harder to audit.

**Keep path ownership implicit in the README layout.** The README describes
the current tree but does not state which accepted decisions it supersedes.
