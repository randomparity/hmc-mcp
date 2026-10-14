# Development

[Documentation index](index.md) · [Contributing](../CONTRIBUTING.md)

## Stack

- **Python ≥3.11**, managed with [uv](https://docs.astral.sh/uv/). The project supports every
  stable, non-EOL CPython release at or above that floor.
- **MCP server**: [FastMCP](https://gofastmcp.com/) (stdio or streamable HTTP)
- **CLI**: [Typer](https://typer.tiangolo.com/) + Rich tables
- **REST transport**: httpx (async), XML parsed with defusedxml
- **CLI passthrough**: [asyncssh](https://asyncssh.readthedocs.io) — tools that
  shell out to HMC CLI commands (`lssyscfg`, `lshwres`, `chsyscfg`, ...) over
  SSH

## Setup

From a source checkout, run `just setup` to install the locked development environment
and repository hooks. Use `uv run --no-sync` before CLI commands in this environment.
See [Contributing](../CONTRIBUTING.md) for the full change and verification workflow.

## Testing

### 1. Unit tests (no HMC needed)

The client and XML parser are tested against an HMC mocked with
[respx](https://lundberg.github.io/respx/) — no real hardware required. The
default command reports only the configured test and coverage result:

```bash
just test
# test: passed; configured coverage gate passed
```

Use `just test-verbose` for native pytest diagnostics and missing-lines coverage.

### 2. MCP protocol smoke test (no HMC needed)

Verifies the MCP handshake in process with a real FastMCP client and reports
the exposed tool count:

```bash
just smoke
# Connected. <N> tools exposed.
```

Use `just smoke-verbose` to list every exposed tool when diagnosing the registry.

### 3. Live check against a real HMC

With credentials configured (TOML profile, env vars, or flags), the cheapest end-to-end check
is `console info` — one Logon + one ManagementConsole GET:

```bash
hmcpctl console info
hmcpctl systems list      # should print a table of your Power servers
```

If `console info` prints the HMC version, auth, TLS and the session
lifecycle all work; everything else uses the same path.

## Layout

```
src/hmcpctl/
  *.py              # package-level modules and composition roots
  api.py             # supported connection/configuration facade (ADR 0118)
  config.py          # TOML profiles, environment values, and CLI configuration
  errors.py          # shared HMC error hierarchy
  resource_identity.py # managed-system, LPAR, and VIOS selector resolution
  audit/             # audit records and non-blocking diagnostic transport
  authorization/     # access policy plus dispatch-time scope enforcement
  client/            # HMCClient transport, response parsing, and domain mixins
  documents/         # domain XML request builders and common envelopes
  jobs/*.py          # job outcome normalization, polling, and request builders
  operations/        # presentation-neutral workflows and authorization policy
    affinity/, inventory/, lpar/, metrics/, storage/
    systems/, templates/, updates/, users/, vios/, virtualization/
                    # domain workflows; see each package for its resource scope
  server_tools/      # MCP adapters: inventory, lpar, metrics, storage, systems,
                     # templates, users, vios, and virtualization
  cli_commands/      # Typer adapters: config, jobs, lpar, metrics, storage, systems,
                     # vios, and virtualization
  snapshots/         # portable LPAR snapshots and affinity assessment
  ssh/*.py           # asyncssh transport and resource-specific HMC CLI commands
  tool_registry.py   # local MCP tool collection and ToolSecurity metadata
  _app.py            # FastMCP factory and shared execution helpers
  server.py          # MCP composition, startup validation, and serving bootstrap
  cli.py             # CLI registration aggregator
tests/           # pytest + respx, no real HMC needed
scripts/         # repository guardrails, generators, test runners, smoke checks,
                 # and live-test harnesses
```

## Notes on the HMC API

- Auth: `PUT /rest/api/web/Logon` with a LogonRequest XML body returns an
  `X-API-Session` token, sent as a header on every subsequent call;
  `DELETE /rest/api/web/Logon` logs off.
- Resources are Atom feeds of vendor media type
  `application/vnd.ibm.powervm.uom+xml; type=<ResourceType>`.
- `.../quick/<Property>` returns a single property cheaply;
  `.../search/(<Property>==<Value>)` filters server-side.
- State changes are asynchronous **jobs**: POST a JobRequest to
  `/rest/api/uom/<Type>/<uuid>/do/<Operation>`, then poll
  `/rest/api/uom/Job/<job-uuid>`.

## Releasing

A release is a version in `pyproject.toml` and a Git tag on the same commit
([ADR 0033](adr/0033-static-package-version.md)); the tag is annotated. Nothing is published
to a package index, so the tag is what a consumer pins; the [README](../README.md#install)
shows the form.

Versions are semver. The tag is `vX.Y.Z`, and the GitHub release is titled
`hmcpctl X.Y.Z (<short-sha>)`, where `<short-sha>` is the tag commit's
`git rev-parse --short` ID. The version
is static in `pyproject.toml`, as ADR 0033 sets out, and stays so for 0.1.0. From 0.2.0 the
version is planned to be derived from Git with hatch-vcs instead (#1471, an ADR superseding
0033): a tagged build would be `X.Y.Z`, and an untagged one `X.Y.(Z+1).devN+g<sha>`, marked
when the tree is dirty. Until #1471 lands, the steps below use the static version. Either way
the changelog heading stays `## [X.Y.Z] - YYYY-MM-DD`, because a commit cannot name its own
SHA.

A release runs in the order of the subsections below: scope it, name the candidate and freeze
`main`, verify the candidate live, read the evidence gate, review the docs and cut the
changelog, tag it, then reopen `main`. Each subsection says who decides what. The operator is
the maintainer who owns the release.

### Scope

A release is scoped by a release epic, an issue that names:

- the consumer contract the release serves, and
- the operations it claims as live-verified, by their `just verification-report` operation
  names, each with the live-testing arm and scenario that evidences it, taken from the
  scenario id of its catalog observation. Those arms are the window's arm list.

The claimed set is what the release promises; nothing outside it is promised, whatever its
verification state. For 0.1.0 the epic is #871: the Tier A kdive adopt contract and the
Tier B bare-CEC path, 15 claimed operations. Changing the claimed set after the live window
reopens the window for any operation it adds.

Before naming the candidate, run the
[coupled dependency bump](../CONTRIBUTING.md#coupled-dependency-bumps) for `fastmcp-slim`,
`mcp` and `mcp-types`, which Dependabot does not update.

### Freeze

The candidate is the `main` commit whose claimed operations' code the release ships and the
live window runs on. The operator names it on the release epic before the first arm runs, and
the freeze starts then; it ends when the tag is pushed.

During the freeze, nothing may merge that changes the import closure of a claimed operation:
the `.py` modules under `src/hmcpctl/` that
[ADR 0127](adr/0127-derived-live-verification-staleness.md) fingerprints for it. That test is
the whole rule. Changes that pass it include:

- documentation, including the docs review and changelog cut below;
- the release-prep commit of [tag and release](#tag-and-release) step 1, which changes
  `pyproject.toml`, `uv.lock`, the changelog and the README's pinned tag; and
- evidence commits that promote observations into the catalog, including the
  `src/hmcpctl/_operation_maturity.json` projection they regenerate, which is data, not a
  module.

A merge that does move a claimed operation's closure re-stales that operation. Re-run its
evidencing arms at the new tip, under the window's authorization or a new one, and re-read
the gate there; never argue that the delta is harmless. The new tip becomes the candidate.

### Live window

The live window produces the evidence that makes each claimed operation `current`. A live run
creates, mutates and deletes real partitions, so the operator authorizes every window
explicitly, with:

- the arm list, naming each arm the window may run;
- the mutation boundary: the managed system and partitions the arms may change;
- an explicit grant for any arm that touches a VIOS. A grant that does not name VIOS access
  does not include it; and
- the read-only reads that make up the window's independent baseline, below.

The operator records the authorization on the release epic before the first arm runs. Each
authorized arm then runs on the candidate as [live testing](live-testing.md) describes: the
preflight, the arm, and the recovery check, in that order, with the recovery check run after
every arm, including one that looked clean.

The window also takes an independent read-only baseline of the mutation boundary before the
first arm and after the last, separate from the baselines the arms record for themselves.
Record the comparison on the release epic. Any difference holds the gate until it is
recovered under live testing's "Confirm the system is clean" step, or explained on the
release epic.

Each arm's evidence matrix comes from `scripts/live_test_evidence.py`. The runner never
writes into the catalog, so its observations are promoted by hand in evidence commits, as the
[capability catalog](capabilities/README.md) describes under "Recording an observation".

### Evidence gate

Read the gate on the `main` commit that carries the promoted observations: the candidate plus
the evidence commits the freeze admits. At the candidate itself the window's observations are
not in the catalog yet. Run `just verification-report` on a clean checkout of that commit. It
prints each operation's derived live-verification state (ADR 0127).

- **Gating:** every claimed operation must read `current`. A claimed operation that reads
  anything else holds the tag until its evidencing arms re-run at the candidate and their
  observations are promoted.
- **Reported, not gating:** `--fail-on-stale` exits 1 when any operation in the whole catalog
  is stale, including operations the release does not claim and its window did not cover.
  Run it and record its result on the release epic, but its exit status does not hold the
  tag. The changelog entry's Known issues list states the global counts from that read
  (current, stale, unevidenced and unrecorded, of the catalog's total) and points to
  `just verification-report` and the [capability catalog](capabilities/README.md) for the
  per-operation list; the full read stays on the release epic. Before the 0.1.0 window, on
  2026-10-12, the catalog's 163 operations read 17 current, 90 stale, 39 unevidenced and
  17 unrecorded.

No check yet reads the claimed set alone, so compare the claimed rows against the report by
hand. Record the pass on the epic with both commits: the candidate and the commit the gate
was read on.

An observation goes stale either when its closure changes or when it is older than 90 days
(ADR 0127). A long freeze can therefore re-stale a claimed operation with no merge at all;
the remedy is the same, re-running its evidencing arms.

### Docs and changelog

Before the tag, review the user-facing documentation against the candidate, and cut the
changelog, which is step 1 of [tag and release](#tag-and-release). Both land on `main`
during the freeze. For 0.1.0 that work is #1468.

Contributors record each user-facing change under `[Unreleased]` in `CHANGELOG.md`, in the
usual [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) categories, ADR 0118 facade
changes included. A release's changelog entry keeps those itemized categories, and it is also
the GitHub release body. At the cut it gains:

- a short statement of the release's scope and the consumer contract it serves;
- a **Verified** section naming the claimed operations, each verified `current` at the gate;
  and
- a **Known issues** list that names concrete problems, including the global
  verification counts from the [evidence gate](#evidence-gate). Every release's entry needs
  one; an entry without it is incomplete.

0.1.0 is the one exception: the first release, with nothing shipped before it, so its entry
carries no itemized changes. Its itemized pre-release history was dropped from the changelog,
after the guidance an operator needs that only the changelog stated moved into the user
documentation; Git history keeps it.

### Tag and release

The operator authorizes the tag after the gate passes, and authorizes the GitHub release
separately, after the tag. An agent or automated workflow never creates the tag or the
release.

1. On a branch, set `[project] version` to the new `X.Y.Z` and run `uv lock`; `uv lock --check`
   must then pass, because `uv sync --locked` in `just setup` refuses a lock that disagrees.
   Cut the changelog: move the `[Unreleased]` entries under a dated `## [X.Y.Z] - YYYY-MM-DD`
   heading in `CHANGELOG.md`, keeping their categories, and add the scope statement,
   **Verified** and **Known issues** sections described under
   [docs and changelog](#docs-and-changelog) (`tests/unit/test_changelog.py` requires the
   declared version to have a heading). Point the changelog's link references at
   `vX.Y.Z` (the `[unreleased]` compare base and an `[X.Y.Z]` tag link), and update the
   README's pinned tag. When `pyproject.toml` already declares `X.Y.Z` and the changelog
   already has its heading but the version was never tagged, as `0.1.0` was, skip the bump
   and replace both that existing section and the `[Unreleased]` entries, itemized
   pre-release history included, with a one-off minimal entry dated for the release (scope,
   Verified and Known issues only), leaving an empty `[Unreleased]` heading. Git keeps the
   history. This applies to 0.1.0 alone; later releases keep their itemized categories.
   Merge that change to `main`.
2. Choose the tag commit: the `main` commit that change merged as, or a later `main` commit
   the freeze admits. Its CI run must be green, and on a clean checkout of it `just verify`
   must pass.
3. Re-read `just verification-report` on that checkout and confirm every claimed operation
   still reads `current`. If one reads anything else, either a merge moved its closure or its
   observation passed 90 days; in both cases re-run its evidencing arms, as the
   [freeze](#freeze) rule says, promote their observations, and repeat from step 2 on the
   commit that carries them. Record the passing read on the release epic with the tag commit.
   Its global counts must match the entry's Known issues list; if they do not, correct the
   entry, merge it, and repeat from step 2 on that commit.
4. Tag that exact commit and push the tag:

   ```bash
   git tag -a vX.Y.Z -m "hmcpctl X.Y.Z" <tag-commit-sha>
   git push origin vX.Y.Z
   ```

   Check that `git rev-parse vX.Y.Z^{commit}` prints the tag commit and that
   `git show vX.Y.Z:pyproject.toml` declares `X.Y.Z`.
5. Create the GitHub release from the pushed tag. Its body is the version's changelog entry:
   copy the entry, without its `## [X.Y.Z]` heading, into the notes file. The title carries
   the tag commit's short ID:

   ```bash
   short_sha=$(git rev-parse --short "vX.Y.Z^{commit}")
   gh release create vX.Y.Z --verify-tag --title "hmcpctl X.Y.Z ($short_sha)" \
     --notes-file <notes.md>
   ```

### After the release

Pushing the tag ends the freeze, and `main` reopens to every change. Close the release epic
and open the next one, or adopt an existing epic for it, naming its claimed operations and
consumer contract as in [scope](#scope). The 0.2.0 epic is #1215.
