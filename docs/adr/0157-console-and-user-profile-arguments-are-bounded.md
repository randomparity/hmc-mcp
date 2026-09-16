# ADR 0157: Console and user-profile arguments are bounded

## Status

Accepted (2026-09-16)

## Context

Issue #843 identifies three user-profile append sites, two remote-access paths,
and two console-update job paths bypassing ADR 0150's existing argument bound.
Its local reproduction builds a 20,081-character user path from a 20,000-character
profile identifier. `_child_path` bounds only the console argument it receives;
that does not bound arguments appended afterward or paths built elsewhere.

## Decision

The bound belongs to each argument, not to `_child_path` or the complete URL.
Reuse `_reject_over_long_path_value` unchanged immediately before encoding
`user_profile_uuid` in `get_hmc_user`, `modify_hmc_user`, and `delete_hmc_user`;
`console_uuid` in `get_remote_access` and `configure_remote_access`; and
`console_uuid` in `update_console_software` and `submit_available_hmc_ptfs_query`.
The existing `_child_path` check remains the console guard for child operations.

Each named value accepts at most 256 Python characters. Longer values raise
`ValueError` naming the argument and lengths, without echoing the value, before
encoding or I/O. Operations retain wait-timing validation before this check.
Remote access retains its literal `?group=RemoteAccess` suffix and media type;
no child segment is invented to reuse `_child_path`.

This explicitly extends the application sites of ADR 0150, not its numeric
policy. Each value contributes at most 3,072 encoded characters. A profile path
has two bounded identifiers (at most 6,144 encoded characters plus literals);
remote-access paths and the two console job paths have one plus fixed suffixes.
This is not a global request-line guarantee: authority, transport and other
operations remain outside this decision. UUID shape is unchanged.

## Consequences

All nine UsersMixin entry points taking console_uuid now apply the same bound;
the three profile-specific methods independently bound user_profile_uuid.
The two console update operations refuse before calling even a substituted
client, rather than relying on transport validation. Values at or below the
bound retain percent-encoding and existing request/response behavior.
The operations module depends on the existing leaf client-contracts predicate.
System/VIOS client classification remains #838; VIOS update paths remain an
orchestrator follow-up proposal requiring approval before filing.

## Considered & rejected

- **Bound the entire `_child_path` result.** verified: at `45ef18be`,
  `client_users.py:74-95` appends user identifiers after that helper returns;
  the remote-access and operations sites never call it. A helper-result check
  cannot cover these callers.
- **Introduce a new shared URL builder.** judgment: seven direct checks reuse
  the existing policy without changing protocol interfaces or mixing child,
  query and job path shapes in one abstraction.
- **Bound encoded output or rely on httpx.** verified: ADR 0150 records variable
  UTF-8 expansion and late transport refusal. Neither gives the existing cheap,
  attributable per-argument contract.
- **Do nothing.** verified: issue #843's local 20,000-character reproduction
  demonstrates the unbounded append; leaving it contradicts the requested closure.
