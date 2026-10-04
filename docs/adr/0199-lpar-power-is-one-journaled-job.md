# 0199 — LPAR power is one journaled job per call, reconciled from partition state

## Status

Accepted (2026-10-03), issue #1223. Implements the H1 spec's `hmc_power_lpar` row under ADR 0189
Decision 2 and ADRs 0190 and 0195. It narrows the spec's selector rule for this tool only:
`system_name_or_uuid` is required (Decision 2). ADR 0189 and the H1 spec otherwise stand.

## Context

The H1 spec gives `hmc_power_lpar` the inputs `lpar`, `action` (`start` / `stop` / `restart`)
and `mode` (`graceful` / `immediate`), and says a restart resumed after its power-off was
applied must not power off again. Three facts shape how:

- The HMC's PowerOff job takes `operation` (`shutdown` / `osshutdown` / `dumprestart`),
  `immediate` and `restart` (`jobs/requests.py`). `osshutdown` needs an active RMC connection.
  With `restart=true` one job powers off and on again.
- The ADR 0190 partition guard is keyed by system UUID and partition UUID. The specialists'
  system selector is optional; without it, finding the owning system walks up to 100 systems
  (ADR 0094).
- The engine records an effect's identity only with its outcome (ADR 0195), so an `uncertain`
  effect never carries a JobID. The spec's "plus the HMC JobID if one was recorded" therefore
  applies to replay of an `applied` effect, not to classification.

## Decision

1. **Mode mapping.** `graceful` is PowerOff `operation=osshutdown`; `immediate` is
   `operation=shutdown` with `immediate=true`. `restart` is the same job with `restart=true`.
   `start` takes no mode; `mode=immediate` with `start` is refused. A graceful stop or restart
   whose partition reports RMC other than `active` fails before any write, naming
   `mode=immediate`. Nothing escalates from graceful to immediate: not a timeout, a failed job,
   or a resume.
2. **The system selector is required.** It keys the partition guard and keeps resolution to one
   system's partition list. The partition must be in that list; a UUID that is not is refused.
3. **One effect per call.** Each action writes at most one HMC job, journaled under key
   `power` with kind `lpar.power_on`, `lpar.power_off` or `lpar.restart`. Its identity is the
   JobID and job link. A replay that finds it `applied` polls that JobID again and never
   resubmits.
4. **Classification.** An open `lpar.power_on` effect is `applied` when the partition is
   activated (`running`, `starting`, `open firmware`) and `not_applied` when it is
   `not activated`. An open `lpar.power_off` effect is `applied` when the partition is
   `not activated`, and otherwise needs attention, because a queued power-off job is
   indistinguishable from none. `lpar.restart` registers no classifier: a completed cycle and
   no cycle both read as running, so an open restart always pauses for attention (ADR 0195
   Decision 4). Resume can therefore never power off twice.
5. **Already in state.** Before writing, a `start` on an activated partition and a `stop` on a
   `not activated` one complete with `already_in_state=true` and record no effect. A `restart`
   of a `not activated` partition fails, naming `start`.
6. **Verified state, bounded.** After a successful job the body re-reads the state until it is
   the action's target (activated for `start` and `restart`, `not activated` for `stop`) or
   120 seconds pass. A job not terminal after 300 seconds, or a state that never settles,
   pauses with `needs_attention`; `resume` re-polls. A failed job, or `error` / `not activated`
   after a start, is `failed`.
7. **ADR 0011 ownership** applies as for the specialists: with
   `HMC_AUTHORIZE_POWER_OPERATIONS` set, a partition another agent owns is refused before any
   write. `ownership_override` is not offered; the specialists keep it.

## Consequences

- A restart interrupted mid-write always needs a human: inspect the partition, then abandon.
  That is the cost of never cycling twice.
- A stop whose job was never submitted reads the same as one still queued, so it also needs
  attention until the partition reads `not activated` or the operation is abandoned.
- Callers who know only a partition name must now name its system.
- Effect keys and kinds are a persisted contract (ADR 0195 Consequences).

## Considered & rejected

- **`graceful` as `shutdown` without `immediate`.** judgment: fit. That is the HMC's delayed
  power-off, which does not ask the operating system; the H1 spec's graceful is the OS's.
- **Restart as a stop effect then a start effect.** judgment: fit. Two jobs double the
  windows in which a crash leaves an effect open, and the HMC already offers the cycle as one
  job.
- **Classify an open restart from the partition's uptime or reference codes.** judgment:
  complexity. No read in this codebase exposes a boot counter, and refcodes are #1224's.
- **Keep the system selector optional and walk the fleet.** judgment: cost. Every call and
  every classification would pay the ADR 0094 walk to key the guard.
- **Expose `ownership_override`.** judgment: fit. The spec's inputs omit it, and an override is
  an operator exception the audited specialists already carry.
