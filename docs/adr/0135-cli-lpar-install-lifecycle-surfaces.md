# ADR 0135: Compose the LPAR Install Lifecycle from Thin CLI Adapters

## Status

Accepted

## Context

Issue #776 requires an operator to complete an LPAR installation lifecycle using only the
installed `hmcpctl` CLI. Most steps already have commands, but optical-media mount and unmount
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

The shared operation resolves a partition UUID to its name with the REST partition read, as it
already does for a system UUID. HMC CLI attributes are not REST element names: the live run on
2026-09-23 showed that the SSH lookup's `-F UUID,PartitionName` is rejected as an invalid
attribute. That lookup now sends the CLI attributes `uuid,name`, which the HMC CLI cheatsheet
documents; that form has not yet run live.

Keep the lifecycle as documentation rather than a new composite command. Its steps create resources
whose identifiers and operator choices are visible between calls, and its failure recovery is to
inspect and resume from the first incomplete step.

## Consequences

- The installable CLI expresses every recipe step except three, which run as HMC CLI commands
  until their issues land: applying the new partition's profile (#939), writing REST-added
  adapters into that profile before a profile power-on (#981), and reading the partition
  description for ownership checks (#965). The 2026-09-23 live run found all three.
- Existing storage authorization and console capture safety remain the source of truth.
- The new commands are public CLI/help contracts and receive focused body, confirmation, output,
  selector, ownership, and help-shape tests.
- Console capture remains observational and non-interactive; it does not send installer input.
- The documented workflow is intentionally non-transactional. Completed resources remain after a
  later failure until the operator resumes or runs the labelled cleanup steps.

## Considered & rejected

- **Put capture under `console`.** verified: `uv run --no-sync hmcpctl --help` describes the
  `console` group as “The HMC itself,” while
  `src/hmcpctl/server_tools/console.py` declares the capture target as an LPAR.
- **Call the MCP wrapper from the CLI.** verified: `src/hmcpctl/cli_commands/runtime.py` builds from
  all active root connection options, while `src/hmcpctl/server_tools/console.py` accepts only a
  profile; direct reuse would silently ignore CLI `--host`, `--user`, and TLS selections.
- **Print captured bytes directly.** verified: `src/hmcpctl/ssh/console.py` documents binary data,
  ANSI escapes, and partial UTF-8 as valid capture content; direct terminal writes would give the
  partition control of terminal rendering.
- **Add one composite install command.** judgment: the requested copy-and-adapt recipe must expose
  identifiers, confirmation points, partial completion, and optional cleanup, which a new orchestration
  API would conceal while duplicating existing commands.
- **Document an out-of-band `mkvterm` command.** verified: issue #776 requires every published recipe
  command to exist in the installable CLI, and before this change `hmcpctl lpars --help` listed no
  console-capture command.
