# 0016 — Render lifecycle tool parameter descriptions

## Status

Accepted (2026-08-14)

## Context

FastMCP exposes function signatures as JSON Schema but only renders parameter
descriptions that are attached through supported docstring or model metadata.
The core lifecycle tools mostly describe arguments in free prose, leaving MCP
clients unable to present per-parameter guidance. Nested dataclass inputs have
the same gap even when their class docstrings explain the vocabulary.

## Decision

Every public tool in the core lifecycle modules carries a Google-style
``Args:`` section with a nonempty description for every parameter. Standard
dataclass fields used as nested tool inputs carry Pydantic-compatible
``dataclasses.field(metadata={"description": ...})`` metadata. Contract tests
inspect FastMCP's rendered schemas, not source text.

Descriptions state current behavior only: units, selectors, wait and timeout
semantics, normalized job outcomes, provisioning result fields, and the ADR
0011 ownership advisory. Existing signatures, defaults, validation, and return
shapes remain unchanged.

## Consequences

MCP clients receive useful descriptions at both top-level and nested schema
properties without a new dependency or runtime wrapper. Documentation changes
must update schema contract tests when a public lifecycle signature changes.

## Considered & rejected

**Free-form prose only.** FastMCP does not reliably associate prose with JSON
Schema properties.

**Replace dataclasses with Pydantic models.** This widens a documentation-only
change into runtime construction and compatibility behavior.

**Annotated aliases for every argument.** This duplicates domain-specific text
in signatures and makes already-large public signatures harder to read.
