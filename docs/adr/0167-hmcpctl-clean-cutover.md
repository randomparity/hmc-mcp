# ADR 0167: Rename every active project identity to hmcpctl

## Status

Accepted (2026-09-22)

## Context

The project is pre-release, and its distribution, command, Python namespace,
configuration directory, audit identity, ownership stamp, and snapshot identifiers all
embed `hmc-mcp` or `hmc_mcp`. Issue #898 renames the project to `hmcpctl`. The operator
confirmed that the software controls no persistent systems, only disposable test systems,
and that no existing snapshots require compatibility. Keeping aliases or dual readers now
would preserve a name the project is explicitly abandoning.

The repository name and PyPI publication are external operator actions. Until the repository
is renamed, links must continue to resolve to `randomparity/hmc-mcp`; documentation must not
claim an unpublished `hmcpctl` package can be installed from PyPI.

## Decision

Make one clean replacement. The distribution, console command, source package, stable
six-name facade, configuration directory, runtime/logging identity, audit memento,
ownership-stamp grammar, snapshot discriminator, and vendor media types become `hmcpctl`.
No `hmc_mcp` package, `hmc-mcp` executable, old configuration fallback, dual ownership
parser, or old snapshot/media-type reader remains.

Keep snapshot version 1. This cut changes the pre-release identifier, not the envelope
schema, and the operator confirmed that no persisted snapshots need conversion. Existing
configuration is handled by an executable manual directory move in the upgrade guide. The
guide requires the destination to be absent, removes the old installed tool, verifies that
the old command is absent, and installs the new tool from an explicit local checkout. The
new ownership grammar preserves the existing owned, foreign-owned, malformed-token, and
override behavior under the new prefix.

Preserve `HMC_*` environment variables and IBM HMC terminology. Preserve old names only in
historical records, valid repository URLs pending the external rename, and upgrade prose
that identifies the former path or executable. The repository/PyPI cutover remains an
operator checklist, not an automated side effect of this change.

## Consequences

Upgrading is intentionally breaking: callers change imports to `hmcpctl`, MCP clients and
shell users change the command to `hmcpctl`, and operators move their configuration directory
before starting the new version. Old snapshots and ownership stamps are not recognized.
That break is bounded by the operator-confirmed absence of persistent systems and snapshots.

The source tree and active documentation have one name, with no compatibility package or
migration framework to maintain. Historical ADRs continue to report the names that were true
when their decisions were made. Repository links remain valid during the separately owned
GitHub rename and can rely on GitHub redirects afterwards.

Because ownership behavior changes on HMC-visible descriptions, the branch requires the
repository's live-test preflight, the dedicated arm's ownership-stamp row to PASS rather than
SKIP, and its independent recovery procedure to report clean before delivery.

## Considered & rejected

- **Retain an `hmc_mcp.api` compatibility package and old command alias.** judgment: the
  pre-release cutover is the least costly point to remove the old identity, and aliases would
  leave two advertised names with no persisted consumer requiring them.
- **Read old configuration, ownership stamps, and snapshots while writing new values.**
  verified: the operator recorded on issue #898 that only disposable test systems exist and
  no snapshots require compatibility; a dual reader would therefore protect no live asset.
- **Keep the old ownership and snapshot strings as protocol identifiers.** judgment: without
  persistent consumers, treating abandoned branding as a permanent protocol name creates the
  inconsistency this rename is meant to remove.
- **Bump snapshots to version 2.** verified: ADR 0082 reserves a new major version for an
  envelope or replayable-configuration schema change; this rename changes neither. A cosmetic
  version bump would manufacture a conversion problem where no saved artifacts exist.
- **Update repository URLs to a not-yet-created GitHub path.** verified: issue #898 excludes
  repository settings from this PR, so changing links before the operator cutover would create
  instructions whose targets do not yet exist.
- **Add a generic migration command.** judgment: one documented directory move is sufficient;
  framework code for nonexistent remote or snapshot state would be speculative scope.
