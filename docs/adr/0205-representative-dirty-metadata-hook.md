# ADR 0205: Use a representative hook for dirty metadata regression

## Status

Accepted for issue #1432, conditional on passing the specified fault controls.

## Context

The dirty-metadata regression initializes and syncs a real copied project, dirties
its package metadata, runs lint and then repeats the complete static hook set.
Issue #1432 requires preserving rebuild detection and independent execution of
that hook set while measuring whether the redundant work can be removed.

## Decision

Exercise `just lint` and the real lint hook in the dirty project. Check successful
hook output as well as stderr for rebuilds. Keep exhaustive structural assertions
for configured hook delegation and recipe no-sync protection. Independently run
all configured hooks through the existing required local and CI checks.
Prove the assertions with controlled missing-protection and rebuild faults,
including an unselected hook; retain the wider test if those controls refute it.

## Consequences

Pytest no longer repeats every static gate solely to exercise the shared command
launch path. Structural coverage and separate real full-hook execution are both
required; neither is a replacement for the other. Production hooks are unchanged.

## Considered & rejected

- Retain complete dirty-project hook execution: judgment: redundant when the
  representative launch path, exhaustive structural guard and independent full
  hook run jointly prove the contract; retained as the no-go fallback.
- Stub hook execution or remove the dirty fixture: judgment: removes the behavior
  issue #1432 explicitly requires this test to exercise.
