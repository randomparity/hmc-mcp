# ADR 0117: Bounded storage operation results

## Status

Accepted (2026-09-05)

## Context

Supported storage operations currently forward client-parsed HMC mappings to
Python, MCP, and CLI callers. This couples each caller to HMC XML-derived keys
and lets presentation code depend on transport shape.

## Decision

Operations expose frozen, named storage result types and translate client
mappings once at the operation boundary. MCP and CLI serialize those types only
at their presentation boundaries. The pre-release supported facade replaces the
raw storage result shapes and exports the named result types.

## Consequences

Storage callers receive a smaller stable vocabulary and malformed required HMC
fields fail at one boundary. Existing consumers must move from raw HMC keys to
named result fields. The client retains raw mappings for protocol-specific work.

## Considered & rejected

- **Keep raw mappings as the supported operation result.** verified: `rg` on
  2026-09-05 shows `operations/storage.py` forwarding client mappings through
  supported `api.py` exports and MCP tools; this preserves transport coupling.
- **Add wrapper objects that retain a raw payload field.** judgment: a public
  escape hatch would preserve the same unstable transport contract the change
  is intended to remove.
