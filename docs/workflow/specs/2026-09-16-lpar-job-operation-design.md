# Governing `_lpar_job`'s operation segment

Record: [ADR 0151](../../adr/0151-lpar-job-operation-is-a-closed-set.md). Issue:
[#828](https://github.com/randomparity/hmc-mcp/issues/828).

## Problem

`LpmMixin._lpar_job` builds `/rest/api/uom/LogicalPartition/{lpar_uuid}/do/{operation}` with
`operation` interpolated raw (`client_lpm.py:24-29`) under no rule, and the repository's drift
walker parses `hmc_mcp.client.core` only, so it cannot see the site.

## Scope

Add `_LPAR_JOB_OPERATIONS`, a frozenset of the five operations this client submits, to
`client_lpm.py`; refuse anything outside it in `_lpar_job` with a `ValueError` before
`submit_job` is called; add the refusal's tests to `tests/unit/test_request_path_safety.py`.
This is a clean extension of `_lpar_job` — current and intended owner are the same module — so no
caller migrates, no path is obsoleted, and the five call sites are unchanged. Out of scope per
the frozen charter: widening `_uom_path_sites()` beyond `core.py`; the seven other ungoverned
segment arguments ADR 0147 enumerates; `property_name`'s grammar (#818).

## Failure model

**Actors and deployments** — a local operator at the CLI; an MCP client through the `api` surface;
this repository's test suite. All three reach `_lpar_job` only through the five public `lpar_*`
methods, which pass literals.

**Invariants and assets at stake**
- The request addresses the LPAR and operation the caller named, not another resource.
- The five existing job paths keep their exact wire form.
- A refusal message carries no operator-supplied string into a log.

**Accepted failure classes**
- A sixth call site passing an unlisted literal fails at runtime, not at test time: the drift
  walker cannot reach this file and widening it is excluded. Bounded — the failure is a refusal
  rather than a sent request, and any tested call site reddens `tests/lpar/test_lpm.py`.
- A real HMC job operation outside the five is refused locally. Accepted: this client submits no
  other, and the remedy is one line beside the call site that needs it.

**Covered elsewhere** — `lpar_uuid` on the same line and the six other ungoverned segment
arguments: ADR 0147's open follow-up, unowned. Dot segments in an assembled path:
`_reject_dot_segments` at the waist (ADR 0143). A URL httpx will not build: `_request`'s
translation to `HMCError` (ADR 0148).

## Threat model

- **Boundary inventory** — one existing boundary narrowed, none added: `operation`, crossing from
  a private argument into a URL path with no check.
- **Actor model** — no untrusted party reaches `operation` today; every caller is in-module and
  passes a literal. The design trusts that, and refuses rather than relying on it.
- **Control** — membership in `_LPAR_JOB_OPERATIONS`, raising `ValueError` before the f-string is
  built. On failure it discloses the permitted set and the argument name, never the value.
- **Out of scope** — `lpar_uuid` on the same line (unowned, ADR 0147); the job XML body, built by
  `hmc_mcp.jobs` and covered by `tests/unit/test_xml_escaping.py`.

## Success

1. `_lpar_job` raises `ValueError` for any `operation` outside the five, with no request built.
2. The message names the permitted operations and not the rejected value.
3. The five existing paths are unchanged on the wire.
4. `just verify` and `uv run --no-sync prek run --all-files` both exit 0.

## Validation

The plan's Task 1 Verification inventory carries the contracts, tests, and commands. In summary:
(1) and (2) are new parametrized and equality cases in `tests/unit/test_request_path_safety.py`,
each driving `_lpar_job` with `_http.build_request` patched to fail the test if reached; (3) is
the existing `tests/lpar/test_lpm.py:101-152`, five `respx` routes reading `route.calls.last`,
staying green unedited; (4) is the two guardrail commands, run bare.
