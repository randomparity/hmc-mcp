# Partial live results in the recovery and evidence readers

## Problem

Since #1336 an interrupted live run writes its results document with
`"partial": true` in `run`. `scripts/live_test_recovery.py` can still print
CLEAN and exit 0 on such a document, because the call in flight when the run
stopped has no row and possibly no artifact flag. `scripts/live_test_evidence.py`
prints the selection as `Subtasks dispatched` and the interruption time as
`Finished`, so the matrix reads as a complete run's.

## Scope

A document is partial when `run.partial` is present and is not `false`. A
non-boolean value fails closed as partial, since the runner only writes
booleans. A document without the key, as older runs wrote, keeps today's
behaviour in both scripts.

- **Recovery:** the header line starts `PARTIAL` and says the run was
  interrupted, not finished. The script still runs every check and prints its
  findings, never prints `CLEAN`, and exits 2 whatever it found, after
  printing "The system was NOT confirmed clean."
- **Evidence:** renders, marked rather than refused. The first line starts
  `**PARTIAL run**` and states that rows end where the run stopped and the call
  in flight has no row. The selection line says `Subtasks selected (not all ran)`
  and `Interrupted` in place of `Subtasks dispatched` and `Finished`. Exit 0.
- **Runbook:** `docs/live-testing.md` says what each script does with a partial
  document.

Marking is chosen over refusing. An interrupted run is the one most likely to
need reporting, and its recorded rows are still attributable to its commit.
Refusing would push the operator back to hand-copying the terminal, which is
the failure this script exists to prevent. The PARTIAL header travels with the
pasted matrix, so a reader cannot mistake it for a complete run.

Out of scope: the runner, and what `partial` means.

## Success

- A partial document yields recovery exit 2 and a `PARTIAL` header, including
  when the checks find nothing, and never `CLEAN`.
- A partial document yields a matrix whose first line is `PARTIAL` and which
  contains neither `Subtasks dispatched` nor `Finished`.
- `partial: false` and an absent key render and exit exactly as before.

## Validation

Offline tests in `tests/scripts/test_live_test_recovery.py` and
`tests/scripts/test_live_test_evidence.py` cover partial true, false, absent,
and a non-boolean value. Each is checked to bite by reverting the predicate
under `PYTHONDONTWRITEBYTECODE=1`. Then `just verify` and
`uv run --no-sync prek run --all-files` pass. No HMC is contacted.
