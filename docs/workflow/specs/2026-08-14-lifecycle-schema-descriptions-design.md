# Lifecycle schema descriptions design

## Goal

Make every parameter of every tool registered by the eight core lifecycle
server modules self-describing in FastMCP's rendered JSON Schema, including the
nested provisioning and LPAR resource objects.

## Contract

- Every top-level input property has a nonempty string ``description``.
- Every property of ``LparResources``, ``ProvisionNetwork``, and
  ``ProvisionStorage`` has a nonempty string ``description``.
- Descriptions preserve current #141, #144, #150, and #151 behavior.
- Job submitters identify submission versus waiting and normalized outcomes.
- Modify and rename guidance references ADR 0011 ownership responsibility;
  ``ownership_override`` requires explicit operator approval.
- Memory, timeout, interval, processor, VLAN, slot, and install-time units are
  explicit wherever the parameter name does not already carry the unit.
- Provisioning documents the complete structured result field set.

## Non-goals

No signature, default, validation, operation ordering, return shape, or other
tool module changes. No dependency or generated schema artifact is added.

## Verification

An exhaustive schema test derives the in-scope tool set from the module
registries and fails on any missing description. Focused assertions pin nested
metadata and high-risk semantic wording. The repository guardrail remains
``just verify``.
