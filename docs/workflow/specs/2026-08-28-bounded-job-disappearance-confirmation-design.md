# Bounded job disappearance confirmation design

## Scope

Issue #532 requires `operations.jobs.wait_for_job` to give a vanished job a meaningful confirming
read without allowing an oversized polling interval to dominate a short timeout. The change is
limited to ADR 0093 clause 5, the polling loop, the served tool wording, deterministic unit tests,
and generated documentation derived from that wording. No retry policy or result shape changes.

## Decision

For `timeout_seconds > 0`, a disappearance observed after an earlier successful poll is confirmed
after `min(poll_interval, timeout_seconds)`. The delay is computed when the disappearance is seen
and is not shortened by the remaining deadline. Thus an ordinary interval at or below the timeout
is preserved in full, while an oversized interval is capped at one timeout and total elapsed time
on the poll schedule is bounded by both `timeout_seconds + poll_interval` and
`2 * timeout_seconds`. Time awaiting HMC reads is outside that schedule and retains the client's
independent HTTP timeout.

`timeout_seconds=0` keeps its existing single-poll contract. A miss on that only read returns
immediately because no earlier successful observation exists, so no confirmation is owed.

The implementation will select the sleep duration from polling state: ordinary polling sleeps no
longer than the deadline remainder, while a pending confirming read sleeps the fixed bounded
confirmation delay. It does not add a helper or new configuration surface.

ADR 0093 records the amended contract and rejected alternatives.

## Error and cancellation behavior

Validation remains in `validate_wait_timing`. Non-404 failures continue to propagate without an
internal retry. Cancellation during the confirmation sleep remains ordinary coroutine
cancellation and performs no HMC-side write.

## Test design

Tests replace wall-clock waiting with a deterministic fake loop clock and patched sleep that
advances it. One case uses `timeout_seconds=3` and `poll_interval=2`; a disappearance at time 2 is
confirmed at time 4, proving the deadline remainder does not compress the interval. A second uses
`timeout_seconds=2` and `poll_interval=5`; a disappearance at the deadline is confirmed at time 4,
proving the timeout-relative cap. Existing coverage preserves zero-timeout single-poll behavior.

## Global constraints

- Python 3.11 through 3.14 and both CI architectures must behave identically.
- No dependency, schema, public result model, authentication, or authorization change.
- `just verify` and `UV_NO_SYNC=1 uv run --no-sync prek run --all-files` must pass.
- Generated tool documentation must match the restored `hmc_wait_for_job` docstring.
