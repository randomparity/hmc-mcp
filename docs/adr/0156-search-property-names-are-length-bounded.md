# ADR 0156: Search property names are length-bounded

## Status

Accepted (2026-09-16)

## Context

Issue #842 identifies the unbounded `property_name` in `search_uom`.
ADR 0150 bounds `property_value` at 256 characters but leaves this other
caller-controlled part of the same request line unbounded. Its optional
namespace discovery is not a size check when `validate=False`.

ADR 0146 declined a quick-property bound without a demonstrated size problem.
That decision remains local to quick properties; issue #842 supplies a search
path size problem, and ADR 0150 supplies an existing per-argument policy.

## Decision

Call `_reject_over_long_path_value("property_name", property_name)` in
`search_uom`, after its existing property-value check and before optional
discovery. Reuse the 256-character bound and `ValueError` diagnostic containing
the argument name and lengths, not the supplied name. Preserve existing
resource-type and property-value error precedence.

Do not change namespace validation or encoding. Names of exactly 256 Unicode
code points remain accepted by the length check. Empty names retain their
existing handling. Quick-property policy and other segment classes do not change.

## Consequences

Each search operand contributes at most 3,072 encoded characters, using
ADR 0150's worst-case UTF-8 expansion. This is a bound on those operands, not
a promise about server request-line limits or every client URL.
A legitimate search property name longer than 256 characters is now refused;
a demonstrated compatibility need would require revisiting the shared bound.
The refusal precedes both discovery and the search request.

## Considered & rejected

- **Leave the name unbounded under ADR 0146.** verified: `core.py` at
  `9bd2d2eb` checks only `property_value`; a 257-character ordinary name passes
  the client in both validation modes. ADR 0146 governs a different method.
- **Check after namespace discovery.** verified: `search_uom` performs a
  discovery read before encoding when `validate=True`; this would allow I/O
  before the local size refusal.
- **Add a name grammar or a separate limit.** judgment: neither is needed to
  close this asymmetry; reuse the existing per-argument policy without a
  second namespace rule or configuration surface.
