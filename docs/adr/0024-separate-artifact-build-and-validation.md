# 0024: Separate artifact construction from validation

## Status

Accepted

## Context

The source-checkout verification suite does not prove that distributable wheel and source
archives have matching metadata and expected contents. CI also needs a wheel that later jobs can
install without rebuilding it. Combining construction and validation in one opaque command would
make it impossible to prove that validation inspected the retained files rather than replacing
them.

Git-derived versions add another constraint: construction needs a clean, complete repository,
while validation must operate only on already-built artifacts and must not consult Git.

## Decision

Provide two canonical commands with distinct ownership:

- `just build` removes and recreates `dist/`, then builds one wheel and one source distribution
  from the clean checkout.
- `just verify-artifacts` validates the existing `dist/` wheel and source distribution without
  invoking a build backend. It checks the artifact set, normalized project name, version agreement,
  wheel compatibility metadata, unambiguous core metadata, and closed package contents directly
  from both archives, comparing package members with the clean source checkout without consulting
  Git. The build configuration limits the sdist to the package and inputs required to rebuild it;
  the validator also admits hatchling's automatic root `.gitignore`, making both artifact member
  sets explicit and reject-by-default.

`just verify` composes both commands after the existing source checks. CI runs that canonical
suite and uploads only the wheel for downstream fresh-environment installation tests. Artifact
upload is retention, not publication, and requires no expanded workflow permissions or PyPI
credentials.

## Consequences

Local and hosted verification now exercise the package boundary and take longer because they build
and inspect distributions. Validation failures identify the artifact and violated invariant. The
wheel has a stable downstream handoff, while the sdist remains validated in the producing job
without becoming a downstream matrix input.

The build command intentionally replaces `dist/`; callers must copy artifacts they intend to keep
before invoking it again. The validation command never extracts, repairs, rebuilds, or silently
selects among duplicate artifacts. It rejects unknown wheel members, every non-regular sdist member,
unsafe link targets, conflicting metadata cardinality, and wheel tags inconsistent with the
filename.

## Considered & rejected

- **Build and validate in one command.** This cannot establish that validation consumes the exact
  retained artifact and gives downstream jobs no independently reusable producer boundary.
- **Use `twine check` as the complete validator.** It checks distribution metadata but not the
  expected package files or wheel/sdist agreement, and would add a dependency for incomplete
  coverage.
- **Upload both wheel and sdist.** The downstream requirement consumes the wheel only; retaining a
  second artifact adds storage and another interface without a current consumer.
- **Publish to a package index as validation.** Publication is explicitly excluded and would add
  credentials and irreversible external state.
