# ADR 0134: Configurable HMC response ceiling

## Status
Accepted

## Context
Issue #771 completes #768 after #770 introduced a 32 MiB transport ceiling.
Large inventories need an operator override through the existing configuration model.

## Decision
Expose `HMCConfig.max_response_bytes` as an integer greater than zero, defaulting to
33554432 bytes (32 MiB). Use the existing Pydantic integer conversion and settings
sources, including `HMC_MAX_RESPONSE_BYTES` and the TOML `max_response_bytes` key.
Pass the resolved value from `_request` to the shared bounded reader; remove its
internal default constant. The independent error diagnostic cap remains unchanged.

## Consequences
The default preserves #770 behavior. Raising the setting permits larger allocations;
the operator chooses a ceiling appropriate to available memory. Zero cannot disable
the bound. Constructor/environment/profile precedence and `from_mapping` isolation
continue through the existing generic field handling.

## Considered & rejected
- Keep only the constant. verified: #771 explicitly requires an operator override.
- Add another environment reader or transport-level validator. judgment: duplicate
  configuration resolution and validation would create unnecessary policy owners.
- Require strict integers. judgment: ordinary integer settings conversion supports
  the textual environment source without introducing a separate parsing contract.
