# ADR 0159: Inventory UOM construction across the client package

## Status

Accepted (2026-09-16)

## Context

Issue #849, part of #838, widens the AST drift guard without changing production
acceptance. The former inventory parsed only `client/core.py`, recognized a
leading literal UOM prefix, and keyed checks by unqualified function name.
The client also constructs prefixed document links, inline-encoded segments,
and user-profile paths composed from `_child_path` and an encoded suffix.

## Decision

Keep the inventory in `tests/unit/test_request_path_safety.py`. Discover Python
modules recursively under the client package without importing them. Identify
construction sites by relative module, lexically qualified function and segment
expression. Checks belong to that same lexical function, not a nested function
or an equally named method elsewhere.

Recognize the package's existing f-string forms: literal UOM prefixes (including
implicitly adjacent strings), `_rest_base_url` before a literal UOM prefix,
`UsersMixin._child_path` as a composed prefix, and inline `quote(name, safe="")`.
Record comparison-only storage filters and document links separately from
request destinations. A constructed path reused as an absolute returned URL
adds no segment site; appended group queries retain their existing separate
guard. A helper's prefix arguments are inventoried where the prefix is built.

Retain the type-check and literal quote-binding assertions, as well as existing
UUID and operation behavioral checks. Inventory membership is not enforcement.
An explicit site-keyed residual inventory points to #850 for the seven classes
`cluster_uuid`, `console_path_id`, `lpar_uuid`, `network_uuid`, `system_uuid`,
`vg_uuid`, and `vios_uuid`. These classes have mixed existing governance:
storage and update request sites already declare UUID metadata; console paths
are quote-bound and length-bounded. #850 must preserve those contracts while
reviewing the remaining sites, not treat all seven classes as uniformly unsafe.
The prefixed network document link also exposes `switch_uuid`; it is recorded
as a linked-document identity requiring policy review by #850, not certified
by its spelling. The already governed `profile_path_id` suffix and closed-set
LPM `operation` are explicit separate cases.

## Consequences

A harmless new unclassified construction outside `core.py` becomes observable
and fails classification. Residual rows name concrete sites rather than granting
a package-wide exception to an argument name. Usage labels are an inventory,
not proof that a value cannot reach a request or document by another route.

This is not a general Python analyzer or a whole-program safety proof. It does
not establish guard dominance, prohibit rebinding, follow arbitrary aliases or
helper returns, or recognize `%`/`.format` construction. Future construction
idioms require deliberate matcher changes. The synthetic regression uses only
ordinary names and local source text; no HMC traffic or exploit probe is needed.

## Considered & rejected

- **Keep core-only discovery.** verified: #849 identifies omitted Cluster job
  paths and the prefixed Network link in the existing client modules.
- **Classify by argument name alone.** judgment: a new site must not inherit an
  old site's exception or borrow a guard from another function.
- **Build a general string/dataflow analyzer.** judgment: unnecessary machinery
  for this bounded drift guard, with a much larger correctness burden.
- **Enforce a new segment policy here.** verified: the operator-approved #838
  decomposition assigns production acceptance changes to #850.
