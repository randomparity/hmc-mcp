# ADR 0001: Pin Direct Dependencies and Automate Updates

## Status

Accepted

## Context

The project declares runtime and development dependencies with open-ended lower bounds,
allows a range of build-backend releases, commits uv's resolution, and has no automated
dependency-update configuration. An install that consumes project metadata without the
repository lock can therefore select different direct releases, while maintainers have no
automated path for updating the declarations and lock together. Issue #26 asks the
repository to lock dependency inputs and enable Dependabot. The repository's supplied
GitHub Actions standards require Dependabot update groups and seven-day cooldowns.

## Decision

Pin every direct runtime, development, and build dependency to one registry release with
`==`. Regenerate and continue committing the uv-managed universal `uv.lock` from those
declarations. Configure Dependabot's `uv` ecosystem at the repository root to check weekly,
group all version updates, and wait seven days after release before proposing them.

The manifest remains the reviewable declaration of direct dependencies; `uv.lock` records
the exact transitive resolution. Dependabot changes both together in reviewable pull
requests.

## Consequences

- A checkout resolves the same dependency graph until a reviewed repository change updates
  it.
- Direct and transitive upgrades become explicit diffs and run through repository CI.
- Security updates remain eligible immediately because Dependabot cooldowns apply only to
  version updates.
- An incompatible release can hold a grouped routine update until that release is excluded
  or corrected; this coupling is accepted to keep the manifest and one complete resolution
  reviewable together and to follow the repository update-group standard.
- Direct pins require routine update PRs; a stale Dependabot queue can leave the project on
  older releases.
- Consumers install the pinned direct versions because this repository is an application,
  not a compatibility-oriented library dependency.

## Considered & rejected

- **Keep lower bounds and commit only `uv.lock`.** Developer environments would be
  reproducible, but installing the published project without its repository lock could
  still select unreviewed direct versions, which does not meet the lock-declarations part
  of issue #26.
- **Use compatible-release or upper-bounded ranges plus `uv.lock`.** This is friendlier for
  reusable libraries but still permits an installer that ignores `uv.lock` to select a
  different release. The project is operated as an application and CLI.
- **Open individual updates immediately.** This minimizes coupling and delay, but creates
  separate manifest/lockfile review streams and conflicts with the supplied repository
  standard requiring grouped updates and seven-day cooldowns. Security updates are not
  subject to the configured cooldown.
- **Reference Git tags or commit SHAs.** The dependencies are registry packages, not
  repository-source dependencies. Converting them to Git sources would bypass normal wheel
  distribution and index metadata without improving the committed uv resolution.
- **Do nothing.** This preserves the current non-reproducible resolution and leaves updates
  dependent on manual discovery.
