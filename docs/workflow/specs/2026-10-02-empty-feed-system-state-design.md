# Empty partition feeds from a non-operating system (#1289)

## Problem

A managed system that is not operating answers its per-system partition and VIOS feeds with
HTTP 204 and no body. `_get` returns `""` for a 204, and `list_logical_partitions` /
`list_vios` turn that into `[]`. So `hmc_list_lpars --system` and `hmc_list_vios --system`
return `[]`, and a system-scoped name lookup reports every partition as not found. Captured on
V11R2 SP1120 with a POWER10 9080-HEX in `State` `recovery` (`DetailedState` `Recovery`) and
`no connection` (`Unknown`): both feeds answered 204
(`tests/fixtures/live/vocabulary/v11r2-p10-9080-hex.json`).

## Design

1. **One check.** `require_operating_system(get_managed_system, system_uuid, resource_label)`
   in `client/client_resolution.py` reads the system document. When `Resource.State`
   lower-cased is `operating` it returns. Otherwise it raises `HMCError` (no status code) that
   names the resource kind, the system name (its UUID when the document has none), the UUID,
   `State` and `DetailedState` (`unknown` when either is missing or the document is `None`), and
   says the HMC cannot report them until the system is operating. Each HMC-supplied value is
   stripped and cut to 100 characters, since a captured `State` already runs to 51.
   `status_code` stays `None`: the HMC answered 204, so there is no failing HTTP status to
   report, and the message carries the state a caller acts on.
2. **Only on an empty feed.** The check runs only when a system-scoped feed returned no
   entries, so a non-empty read costs nothing extra and an empty read costs one system GET.
   It runs before any state filter, so an empty *filtered* result from a non-empty feed is
   unaffected. Call sites:
   - `operations/lpar/core.list_lpars` and `operations/vios/core.list_vios`, when a system
     selector was given and the system-scoped feed was empty (the unscoped VIOS state search
     is not a per-system feed and is unchanged);
   - `client_lpars.find_partition_by_name` and `client_systems.find_vios_by_name`, in the
     `system_uuid` branch when the feed was empty. These are what `get_lpar`,
     `resolve_lpar_uuid` and `resolve_vios_uuid` reach with system scope. Those resolvers used
     to raise `ResourceNotFoundError` here; they now pass the `HMCError` through, so every
     tool that takes a system-scoped LPAR or VIOS name, and the VIOS-install name probe (which
     catches `ResourceNotFoundError`), report the system state instead of "not found".
3. **Other feed consumers unchanged.** `list_logical_partitions` / `list_vios` keep returning
   `[]` for a 204: fleet health already reports a non-operating system as a system issue and
   would fail entirely if its partition feed raised; inventory, ownership, decommission,
   templates and capacity are outside this issue's surface; `list_lpar_ownership` with a
   system and decommission's VIOS storage inventory keep the same false empty and are listed
   in the PR as unchanged callers. The client feed methods stay the
   owners of the 204 mapping; this change adds an interpretation at the four read paths.

No ADR: this applies the `operating` rule `operations/systems/health._system_issue` already
uses to the read paths, with no alternative design that changes a contract differently.

## Failure model

1. **Actors** — an operator or agent (kdive) listing or resolving partitions/VIOSes on one
   system through MCP, CLI or the library.
2. **Invariants**
   - an empty system-scoped LPAR/VIOS list or name lookup never means "none" unless the
     system's `State` reads `operating` at the time of the check;
   - an `operating` system's empty feed still returns `[]` / `None`.
3. **Accepted failure classes**
   - the system changes state between the feed read and the system read. Bounded: one
     read-only call; a retry sees the new state.
   - `GET ManagedSystem/{uuid}` has not been captured on a non-operating system; only the feed
     and search responses were. If it fails there (for example the firmware's null-property
     500, which `get_managed_system` already falls back from), the caller gets that error
     instead of the state. Still an error, never a false empty; the pending live run must
     exercise this read.
   - `DetailedState` placement is not captured as a document position, only as values; when
     absent the message says `unknown`. The decision reads only `State`, which the captured
     system documents carry as a top-level element.
   - the system GET itself fails (`HMCError` from `get_managed_system`). It propagates; the
     caller gets an error, never a false empty.
   - a 204 from an `operating` system still reads as empty; no capture shows otherwise.
4. **Covered elsewhere** — unscoped HMC-wide feeds (#1293); `ManagedSystem/search` 204 in the
   name resolver (#1290); the full Recovery/No Connection sweep (#1290); I/O and SR-IOV
   per-system feeds (unverified, listed in the PR).

## Success

- `list_lpars(system)` and `list_vios(system)` with a 204 feed and `State` `recovery` or
  `no connection` raise `HMCError` naming the system, `State` and `DetailedState`.
- The scoped LPAR and VIOS name lookups raise the same instead of returning `None`.
- With `State` `operating` the same 204 returns `[]` / `None`.
- A non-empty feed never triggers the system read.

## Validation

Unit tests with respx in `tests/unit/test_empty_feed_system_state.py`, using the captured
values (`recovery`/`Recovery`, `no connection`/`Unknown`, `operating`): the four paths above
for both non-operating states, the operating case, and a non-empty feed with no system GET;
`resolve_lpar_uuid` with system scope; a scoped `state` filter over a non-empty feed with no
match (no system GET); a system document with no `State`/`DetailedState` and a `None`
document (`unknown`); and a mixed-case `Operating`. Tool docstrings are unchanged: the
error message itself names the system and its state, and the tool modules are outside the
frozen surface.
Live verification is pending (label `verification:live-hmc`); the operator runs it.
