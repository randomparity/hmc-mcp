# 0041 — Fail-closed composition, and a generated legacy-equivalent policy as the migration

## Status

Accepted (2026-08-19)

## Context

ADR 0035 through ADR 0040 built an access-control boundary and left it switched off. ADR 0036
compiled a policy, ADR 0037 enforced the tool dimension, ADR 0038 the connection dimension,
ADR 0039 the target dimension, and ADR 0040 recorded every decision. All five are reached only
when an operator passes `--access-policy NAME`. Without it `server.create_mcp()` applies no
ceiling, authorizes no connection, registers every tool, and — since ADR 0040 — writes no audit
record. `create_mcp`'s own docstring names that state "what every deployment gets until #225
makes startup fail closed."

Epic #218 requirement 9 asks for two things together: startup that refuses to serve when no
policy is selected, and a non-interactive generator that writes a reviewable legacy-equivalent
policy, activating nothing and overwriting nothing. Requirement 10 adds that the stdio path, the
HTTP path, a fresh application instance, the arbitrary-command toggle, and the smoke path all
exercise the same policy composition.

The two halves are one decision. The refusal alone strands every existing deployment; the
generator alone changes nothing. The order in which an operator meets them is the whole of the
migration, and it is why this record covers both.

Three facts about the boundary as it stands shape what "legacy-equivalent" can mean.

**An omitted optional selector is a well-formed call.** `target_scope.ABSENT` is what a call
yields when it leaves an optional selector unset. `all-targets` covers it; no `targets` table
can. A generated policy that narrowed targets would silently retire every call that omits an
optional argument, which is a large fraction of ordinary use.

**Three tools depend on the non-string selector arm.** `vios_partition_id` is the surface's only
`int` selector. `target_scope._value` renders it through `str()`; without that arm those calls
read as `UNREADABLE`, which denies under `all-targets` too.

**A grant naming a tool beside an incompatible `targets` table refuses a *load*, not a call.**
ADR 0039's rules R12 and R13 reject at compile time what ADR 0036 criterion A7 accepted. Under
`--access-policy` that is a non-zero exit with no server started.

## Decision

**Composition requires a policy, and the refusal lives there rather than at startup.**
`server.create_mcp(policy: AccessPolicy)` loses its default and rejects `None` explicitly, with a
message naming the generator. The module-level `server.mcp` application is removed rather than
made lazy: it is the one object in the package that could be an unbounded served application, and
nothing that cannot be constructed needs a guard. `server._gates` keeps its name and its
"derived together, always passed together" contract, and returns a non-optional pair.

Placing the refusal in the composer rather than in `_serve_application` is what makes requirement
10 true by construction instead of by four separate checks: the stdio path, the HTTP path, the
arbitrary-command toggle, the smoke script, and the live-test runner all reach `create_mcp`, so
none of them can compose a surface the policy did not bound.

**`hmc-mcp serve` requires `--access-policy NAME`.** Omitting it is a usage error: exit code 2,
no server started, and a message naming `hmc-mcp config init-access-policy` and the file the
generator would write. A policy that fails to read, parse, or compile keeps ADR 0036's existing
behaviour — exit code 1 through the CLI's error path. Two codes, because they are two different
operator problems: the first is "you have not chosen", the second is "what you chose is wrong".

**`hmc-mcp config init-access-policy` generates the legacy-equivalent policy.** It takes no
options, writes only the platform-native `access-policy.toml`, creates it with `O_EXCL` at mode
`0o600`, refuses to overwrite an existing file, prints the path it wrote, and activates nothing.
The written document is a single grant under the policy name `legacy-equivalent`:

- **`tools` names every ordinary tool explicitly**, sorted, rather than granting effect classes.
  An `effects` grant would silently confer every tool a later release adds; a named list pins the
  surface to what was legacy at generation time and leaves a new tool ungranted until the
  operator regenerates and re-reads the diff. That is the "reviewable rather than silently
  preserving" the epic asks for, and it is the fail-closed direction of the two.
- **`hmc_run_command` is omitted from the written file, always.** Epic requirement 6 keeps the
  arbitrary-command escape hatch a distinct maximum-risk capability that
  `--enable-arbitrary-command` alone does not confer. A generator that granted it would make the
  flag sufficient again. The generated file carries a comment saying how to add it. The in-memory
  compiler takes an explicit `include_arbitrary_command` opt-in, defaulting to false and reachable
  from no CLI path; `scripts/live_test_runner.py` is its one caller, because that harness drives
  `hmc_run_command` against a real HMC and would otherwise compose a surface in which its own
  `--enable-arbitrary-command` toggle can never register anything.
- **`connections` names every profile key in `config.toml`, plus `<default>`.** Legacy accepted
  any `profile` argument, so every key is granted; `<default>` is granted as well because under
  `HMC_HOST` `connection_scope.selected_connection` collapses every token to it, and because an
  omitted `profile` argument means it. Nicknames are excluded — ADR 0030 resolves a nickname
  before the comparison, so granting one never matches. Absent a config file the list is
  `["<default>"]` alone; an unreadable or malformed one fails the command with the `ConfigError`
  rather than generating a policy that quietly grants less than it claims.
- **`targets = "all-targets"`.** The only value that covers an omitted optional selector, and the
  only one that grants the 25 tools ADR 0039 records as unboundable by any table.

**The same generator composes the two scripts.** `scripts/smoke_mcp.py` and
`scripts/live_test_runner.py` compile a legacy-equivalent policy in memory over
`connections = ("<default>",)` and pass it to `create_mcp` — the smoke path with the operator's
own settings, the live runner with the escape hatch opted in. What the smoke leg proves on every
CI run is that the grant compiles and composes; it never serializes TOML, so the emitted
document's parse is proved separately by the render round-trip test, and a rendering defect
would pass smoke untouched.

**Short-lived scoped grants remain a future extension point and nothing more.** The seam is
`server._gates`: a future grant issuer would compose a second `Authorize` alongside
`dispatch_authorizer` and widen a decision for a bounded time, under a separately authenticated
channel. No MCP tool argument, request field, header, or environment variable selects, widens, or
supplies a grant now, and `AccessPolicy` stays frozen for the process lifetime (ADR 0036).

## Consequences

- **Every deployment that upgrades without an `access-policy.toml` stops serving.** That is the
  point of the entry, and it is the loudest change in the epic. The refusal names the generator
  command and the path, so the migration is two commands: generate, review, then add
  `--access-policy legacy-equivalent` to the launcher.
- **ADR 0039's R12 and R13 load refusals become universal.** Before this record an operator whose
  policy tripped them had an escape: drop `--access-policy` and get an unenforcing server. That
  escape is gone, so a load-time refusal is now something every upgrading operator meets rather
  than something only policy-users opted into. The generated policy uses `all-targets` throughout
  and cannot trip either rule, which is what makes the escape's removal survivable — and it is
  why generator correctness is a release blocker rather than a convenience.
- **Every deployment now emits one audit record per authorization decision.** ADR 0040's
  documented contract was that without a policy there is no authorizer and so no record. That
  sentence stops being true here. The direct consequence is issue #269: `audit._AuditHandler`
  writes synchronously to `sys.stderr`, and an open-but-undrained pipe blocks a write rather than
  raising, so none of the three guards `_warn` and the handler share fires. A record measured on
  this branch is 386 bytes for a denial carrying no targets and 563 for a permit carrying two, so
  a client that never drains fd 2 can wedge the server after on the order of 120-170 calls on a
  64 KiB pipe — no timeout and no diagnostic. Before ADR 0040 that
  was unreachable in practice; after it, reachable for policy-using deployments; after this
  record, reachable for all of them, and reachable by an ungranted caller, because ADR 0040
  emits the record before the denial. #269 is not resolved here. It is stated here so the
  deployment requirement — the host must drain fd 2 — is a documented precondition of the
  fail-closed default rather than a field discovery. Issues #270 (the level split is unreachable
  from `hmc-mcp serve`) and #267 (a routine denial renders a traceback) are on every deployment's
  path for the same reason.
- **Legacy-equivalent is not legacy.** Under the generated policy every connection-bearing tool
  is wrapped by `tool_registry.authorized`, every call runs the full `dispatch_scope` conjunction,
  and every decision is recorded. For every tool the file names the outcome is the same as before
  — permitted — but the cost, the log volume, and the failure modes are not. An operator reading
  "legacy-equivalent" should read it as "grants what legacy granted", never as "behaves as legacy
  behaved".
- **A deployment running `--enable-arbitrary-command` loses `hmc_run_command` at migration.**
  It is the one tool whose outcome the generated file does change, and it changes to
  unregistered rather than denied: the flag and the ceiling compose conjunctively, so the tool
  never reaches `tools/list` and the existing startup warning fires instead. Restoring it is a
  deliberate edit to the generated grant's `tools`, which is the review step epic requirement 6
  asks for.
- **The generated policy pins the tool surface at generation time.** After an upgrade that adds a
  tool, the tool is registered by no grant and absent from `tools/list`. That is fail-closed and
  deliberate, and `hmc_effective_permissions` is how an operator sees the difference.
- **Regenerating is a manual, three-step procedure, and it discards hand edits.** The command
  refuses to overwrite, so regeneration means moving `access-policy.toml` aside, running the
  generator, diffing the two files, and re-applying by hand whatever was edited into the old one
  — an added `hmc_run_command` grant, a narrowed connection list, a second policy. That is stated
  in the generated file's header and in the README rather than discharged with a `--force` flag,
  because a flag that overwrites a reviewed policy in place is the one operation this command
  most needs not to have.
- **Three statements in earlier records are retired.** ADR 0037's "`create_mcp(policy=None)`
  registers every tool", ADR 0038's R14 "no policy means no authorization", and ADR 0039's R20
  "no behaviour change without a policy" each describe a composition that no longer exists.
  Those records are otherwise unaffected and are not superseded; the tests asserting the retired
  behaviour are inverted rather than deleted, as ADR 0039 inverted ADR 0036's A7 test.
- **`hmc_effective_permissions` can no longer report a null policy through a served application.**
  `server_permissions.describe` keeps its `AccessPolicy | None` parameter and its honest
  null-reporting arm — it is documented as binding a direct caller, and this record does not make
  a direct caller impossible — but no composed application reaches that arm any more.
- **Importing `hmc_mcp.server` no longer composes an application.** `mcp = create_mcp()` ran at
  import and registered 129 tools; removing it makes the import cheap and makes any code that
  depended on the import side effect fail loudly rather than quietly get a surface it never asked
  for.
- **The generator reads `config.toml` and writes its profile keys into a second file.** Both live
  in the same platform-native directory under the same ownership, and a profile *key* is not a
  credential, a host, or a user; `config show` already discloses more. But the coupling is new:
  renaming a profile after generation leaves a grant that matches nothing, which denies rather
  than over-grants.

## Considered & rejected

**Do nothing — keep startup permissive and ship only the generator.** The null option, and it is
what every prior entry in the epic implicitly chose. Rejected because it leaves the whole
boundary opt-in: an operator who never learns the flag exists gets ADR 0035-0040's cost and none
of its protection, which is requirement 9's exact target.

**Refuse in `_serve_application` and leave `create_mcp(policy=None)` alone.** Smaller diff, and it
satisfies a literal reading of "startup refuses to serve". Rejected because requirement 10 asks
the fresh-application and smoke paths to exercise the same composition, and an unbounded composer
that only the served path checks is one an embedder, a script, or a future entry point reaches
without passing that check. The property worth having is that no unbounded application exists,
not that no unbounded application is served.

**Generate with effect classes rather than named tools.** `effects = ["read", "mutate",
"destructive"]` is three lines against the 129 a named list needs, and it is what a hand-written
legacy-equivalent policy would look like. Rejected because it grants tools that do not exist yet:
every release adding a tool would widen the operator's policy without an edit or a review, which
is the silent privilege retention the epic's generator was asked to replace.

**Include `hmc_run_command` in the generated grant, gated by the existing flag.** Argued as the
truer legacy equivalence, since `--enable-arbitrary-command` did expose it. Rejected on epic
requirement 6: the flag was never meant to be sufficient, and a generator that pre-grants the
escape hatch restores exactly the sufficiency the requirement removes.

**Make `--access-policy` a Typer-required option.** One line, and Typer emits its own usage error.
Rejected because the message is the deliverable: "Missing option '--access-policy'" tells an
upgrading operator nothing about the generator, the file, or why their working server stopped.

**Select a policy implicitly, or keep a named opt-out.** Two variants, weighed together because
they trade the same thing. *Implicit selection* — an `HMC_ACCESS_POLICY` environment variable, or
defaulting to the sole policy in `access-policy.toml` — would remove the launcher edit from the
migration, which is the step that costs most in an MCP deployment where the launch command lives
in a client-owned JSON config the operator may not edit directly. Rejected because issue #225 and
epic requirement 9 both say *explicitly selected*, and because a file that activates by existing
is the property the packaged-default entry below rejects, reached by a different route: an
operator who generates a policy to read it would find it enforcing before they finished reading.
*A named opt-out* — a `--no-access-policy` token by which an operator states they accept an
unbounded server — was considered as a gentler removal of the escape this record closes.
Rejected because it is the same escape under a longer name, and the whole difficulty ADR 0039's
R12 and R13 create is that an escape exists at all; an operator who needs a permissive server can
say so in a policy that grants everything, and that policy is exactly what the generator writes.

**Ship a default legacy-equivalent `access-policy.toml` in the package.** Rejected because a
policy file that exists is a policy file that can be selected by name without review, which
inverts "generation does not activate it" — and because the correct connection list is the
deployment's own, which a packaged file cannot know.

**Narrow `tool_registry.register_tools` and `tool_registry.authorized` to require the gates.**
Their `None` arms become unreachable from production once `create_mcp` requires a policy, and
removing a dead branch is ordinarily right. Rejected because they are the mechanism ADR 0037 and
ADR 0038 defined, where `None` still describes the function accurately, and because
`authorized`'s early return stays live through its other disjunct — a tool declaring no
connection argument. The residual is named rather than closed: the mechanism can still register
an unwrapped tool if a future caller passes `None`, and what prevents it is that `create_mcp` is
the only composer, checked by the ADR 0038 registry assertion in
`tests/app/test_connection_authorization.py`.

**Block this change on #269.** Considered seriously, because this record is what makes #269
reachable everywhere. Rejected because #269's mechanism is unchanged here — the same handler,
the same three guards, the same synchronous write — and its own record already chose option 1,
"accept it and say so in the operator documentation", when ADR 0040 shipped. What changes is
reach, and reach is exactly what a documented precondition can cover. Holding the fail-closed
default hostage to a liveness hardening would leave every deployment unbounded for longer in
order to avoid a hang that requires a client which never drains its child's stderr. The
precondition is documented in the README and named above; #269 remains open and its priority
should be re-read against this record.
