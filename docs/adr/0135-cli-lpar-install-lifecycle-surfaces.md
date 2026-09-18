# ADR 0135: Compose the LPAR Install Lifecycle from Thin CLI Adapters

## Status

Accepted

## Context

Issue #776 requires an operator to complete an LPAR installation lifecycle using only the
installed `hmc-mcp` CLI. Most steps already have commands, but optical-media mount and unmount
exist only as storage operations, and bounded LPAR console capture exists only as an MCP tool.
The `console` CLI group describes the management console itself, while capture targets one LPAR.

The existing operations already own selector resolution, LPAR ownership checks, optical mapping
semantics, capture bounds, vterm contention, and release proof. The CLI must expose those contracts
without creating a second implementation or an interactive terminal.

## Decision

Add `storage mount-optical-media` and `storage unmount-optical-media` as thin adapters over the
existing storage operations. Both accept VIOS, LPAR, and media selectors; both pass through the
optional managed-system selector and ownership override. Mount uses the storage group's ordinary
`--yes` confirmation. Unmount uses `--confirm` because it removes a mapping, while stating that the
backing ISO remains.

Add `lpars capture-console`, not a command under `console`. Extract the existing selector-to-name
orchestration from the MCP adapter into a presentation-neutral LPAR operation and call that operation
from both surfaces. The CLI keeps the existing bounded, sealed-stdin capture. JSON output carries the
same base64 payload shape as the MCP tool; terminal output renders captured bytes as escaped text so
LPAR-controlled bytes cannot inject terminal control structure.

Keep the lifecycle as documentation rather than a new composite command. Its steps create resources
whose identifiers and operator choices are visible between calls, and its failure recovery is to
inspect and resume from the first incomplete step.

## Consequences

- The installable CLI can express every step required by the recipe.
- Existing storage authorization and console capture safety remain the source of truth.
- The new commands are public CLI/help contracts and receive focused body, confirmation, output,
  selector, ownership, and help-shape tests.
- Console capture remains observational and non-interactive; it does not send installer input.
- The documented workflow is intentionally non-transactional. Completed resources remain after a
  later failure until the operator resumes or runs the labelled cleanup steps.

## Considered & rejected

- **Put capture under `console`.** verified: `uv run --no-sync hmc-mcp console --help` at commit
  `1dde19996ec35213ac03191f4a4cdc2354c41c4c` describes that group as “The HMC itself,” while
  `src/hmc_mcp/server_tools/console.py` declares the capture target as an LPAR.
- **Call the MCP wrapper from the CLI.** verified: `src/hmc_mcp/cli_commands/runtime.py` builds from
  all active root connection options, while `src/hmc_mcp/server_tools/console.py` accepts only a
  profile; direct reuse would silently ignore CLI `--host`, `--user`, and TLS selections.
- **Print captured bytes directly.** verified: `src/hmc_mcp/ssh/console.py` documents binary data,
  ANSI escapes, and partial UTF-8 as valid capture content; direct terminal writes would give the
  partition control of terminal rendering.
- **Add one composite install command.** judgment: the requested copy-and-adapt recipe must expose
  identifiers, confirmation points, partial completion, and optional cleanup, which a new orchestration
  API would conceal while duplicating existing commands.
- **Document an out-of-band `mkvterm` command.** verified: issue #776 requires every published recipe
  command to exist in the installable CLI, and `hmc-mcp lpars --help` at the recorded commit has no
  console-capture command.
