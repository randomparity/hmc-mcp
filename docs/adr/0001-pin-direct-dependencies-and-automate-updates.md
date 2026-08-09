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
`==`. Runtime and development pins use their existing locked releases; the build backend,
which is outside uv's project lock, uses current stable uv-build 0.12.3 to match the current
uv tool. Regenerate and continue committing the uv-managed universal `uv.lock` from the
project and development declarations. Configure Dependabot's `uv` ecosystem at the
repository root to check weekly, group all version updates, and wait seven days after
release before proposing them.

The manifest remains the reviewable declaration of direct dependencies; `uv.lock` records
the exact project and development resolution. Dependabot updates those declarations with
the lock in reviewable pull requests. A build-backend-only update is manifest-only because
uv excludes the backend from the project lock.

## Consequences

- A checkout resolves the same dependency graph until a reviewed repository change updates
  it.
- `uv lock --check` does not validate the build backend; its exact manifest pin and the
  functional build/verification path cover that separate boundary.
- Direct and transitive upgrades become explicit diffs that require the ordered local
  `uv lock --check` and `just verify` proof plus human review before merge.
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
- **Do nothing.** This preserves the existing committed resolution for repository installs
  but leaves metadata-only installs open to different direct releases and leaves manifest
  and lock updates dependent on manual discovery.
