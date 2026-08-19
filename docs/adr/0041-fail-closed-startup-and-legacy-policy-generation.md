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
behaviour — exit code 1 through the CLI's error path.

Three operator problems, not two, and the third is the likeliest one on an upgrade: the option is
supplied correctly and the file has not been generated yet. Left alone it reaches
`load_access_policy`'s unreadable-file arm and prints `cannot be read: [Errno 2]`, which names
neither the generator nor the remedy — the least useful message in the one release where every
deployment meets it. So an absent policy file gets the same migration text as the absent option,
at exit code 1, because the command line was right and the environment was not. The codes split
on whose problem it is: 2 for "you have not chosen", 1 for everything about the policy itself.

Both messages name the documented read-only and limited-mutation examples beside the generator.
Nothing at the point of refusal distinguishes an upgrade from a first run, so a message offering
only the generator would send every new deployment to the widest policy the system can express.

Both also resolve the policy path through the same guard `load_access_policy` applies. A uid with
no passwd entry and no `HOME` makes `Path.home()` raise, so an unguarded path interpolation — or
an unguarded existence check for the absent-file case — would replace a refusal with a traceback,
in the deployment shape least able to diagnose it. That is what `server._unselected_policy_file`
existed for, and its removal must not take the guard with it: an unresolvable path renders the
message without one and falls through to the load's own `AccessPolicyError`.

**`hmc-mcp config init-access-policy` generates the legacy-equivalent policy.** It writes the
platform-native `access-policy.toml` by default, creates the file with `O_EXCL` at mode `0o600`,
refuses to overwrite whatever path it is given, prints the path it wrote, and activates nothing.
It takes one option, `--output PATH`, because the command cannot overwrite and therefore has no
other way to regenerate for a diff. A `--force` that overwrites a reviewed policy in place is the
alternative, and it destroys the review. `--output` moves the *write* only: the connections list
is always read from the invoking identity's `config.toml`, so a split-identity deployment has to
run the generator under the serving identity's `HOME` or `XDG_CONFIG_HOME` — which then places
the default path correctly too, leaving `--output` for the scratch-and-diff case and for macOS,
where `config_dir()` has no environment override.

**Two decisions about emitting the document.** The emitter is hand-written, because epic
requirement 11 forbids a new runtime dependency and `tomllib` only reads; every operator-authored
key it renders is escaped, since profile keys arrive unvalidated. And the generator **loads what
it rendered before it writes anything** — parsing is not the property the migration needs, ADR
0036 enforces rules on entry *content* that escaping cannot satisfy, and compiling the rendered
text through the same `compile_access_policy` a server would use is what makes "the file this
command writes is a file that loads" true without a second copy of ADR 0036's rules living here.
The spec holds the escape set and the key shapes that motivate it.

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
  compiler takes an explicit `include_arbitrary_command` opt-in defaulting to false; the
  *renderer* does not take it and cannot emit the name at all, so requirement 6's property — no
  file this generator writes grants the escape hatch — holds of the code rather than of today's
  callers. `scripts/live_test_runner.py` is the opt-in's one caller, because that harness drives
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
CI run is that the grant compiles and composes; it never serializes TOML, so a rendering defect
would pass smoke untouched. What proves the emitted document *loads* is the generator's own
pre-write compile, and the render round-trip test that exercises it.

**Every registration site requires both gates.** `tool_registry.register_tools`,
`tool_registry.authorized`, `server_permissions.register_permissions_tool`, and
`server_command.configure_arbitrary_command_tool` all lose their `None` defaults. A mandatory
policy at the composer does not by itself reach them: `configure_arbitrary_command_tool` runs
*outside* `create_mcp`, and while its gates defaulted to `None` a call on an application
composed from a read-only policy registered `hmc_run_command` with no ceiling and no
authorizer — the fail-open this record claims to remove, surviving at the highest-risk tool in
the package. `register_tools` is the same shape at the bulk site, where omitting the gates
registers a module's entire tool set unbounded and without error.

This record first rejected the narrowing, on the ground that `None` still described the
mechanism accurately and that ADR 0038 had priced it at a dozen call sites. Both halves stopped
holding: `None` describes a composition that no longer exists, and every non-test caller now
passes both gates, so the price is paid already. `authorized`'s early return survives through
its other disjunct alone — a tool declaring no connection argument. What the earlier reasoning
got right is narrower than it claimed: the *policy object* still travels as callables, so
`access_policy` never imports `tool_registry` back.

**Short-lived scoped grants remain a future extension point and nothing more.** The seam is
`server._gates`: a future grant issuer would compose a second `Authorize` alongside
`dispatch_authorizer` and widen a decision for a bounded time, under a separately authenticated
channel. No MCP tool argument, request field, header, or environment variable selects, widens, or
supplies a grant now, and `AccessPolicy` stays frozen for the process lifetime (ADR 0036).

## Consequences

- **Every deployment that upgrades without an `access-policy.toml` stops serving.** That is the
  point of the entry, and it is the loudest change in the epic. Both refusals — the missing
  option and the missing file — name the generator command and the path, so however the operator
  arrives, the migration is: generate, review, then add
  `--access-policy legacy-equivalent` to the launcher.
- **ADR 0039's R12 and R13 load refusals become universal.** Before this record an operator whose
  policy tripped them had an escape: drop `--access-policy` and get an unenforcing server. That
  escape is gone, so a load-time refusal is now something every upgrading operator meets rather
  than something only policy-users opted into. The generated policy uses `all-targets` throughout
  and cannot trip either rule, which is what makes the escape's removal survivable — and it is
  why generator correctness is a release blocker rather than a convenience.
- **Every deployment now emits one audit record per authorization decision.** ADR 0040's
  documented contract — no policy, no authorizer, no record — stops being true here, and that
  makes issue #269 universally reachable: the audit sink writes synchronously to `sys.stderr`, an
  undrained pipe blocks rather than raising, and no guard fires. #269 carries the mechanism and
  the arithmetic; what this record decides is that **something must drain fd 2** becomes a
  precondition of the default rather than a property of the deployments that opted in. The
  precondition binds the operator only for the HTTP transport and for a stdio launcher they
  control: under stdio an MCP client spawns the server and owns fd 2, so for the shape this
  record makes the default the residual is "choose a client that drains its child's stderr". It
  is reachable by an ungranted caller too, since ADR 0040 emits the record before the denial. And
  with #270 open there is no in-process lever — `install_audit_sink` takes no argument and reads
  no environment variable — so an operator meeting a non-draining client can neither reduce
  record volume nor redirect it. #267 (a routine denial renders a traceback) is on every
  deployment's path for the same reason.
- **The generator is the onboarding path for fresh installs too, and its output is the widest
  policy this system expresses.** A first run reaches the same refusal an upgrade does, and
  `legacy-equivalent` names a history a new deployment does not have: 129 tools including every
  destructive one, every connection, `all-targets`. That is why both refusal messages point at
  the narrower documented examples as well, and why the generated file's own header says the
  grant is a migration aid rather than a recommended posture. The paved road for a new
  deployment is the read-only example, not this file.
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
- **The generated policy pins the tool surface at generation time, and nothing inside the running
  server shows the gap.** After an upgrade that adds a tool, the tool is named by no grant, so it
  is never registered — and `hmc_effective_permissions` reports the *registered* set, so it shows
  exactly what `tools/list` shows and carries no signal that anything is missing. The detection
  path is `--output`: regenerate to a scratch path and diff the `tools` array against the deployed
  policy. Absent that, release notes are the only signal. The pin itself is fail-closed and
  deliberate; presenting the inspection tool as the way to see it would not have been true.
- **Regenerating discards hand edits, and the deployed file must not be moved aside to do it.**
  Regeneration is: generate to a scratch path with `--output`, diff, and merge by hand whatever
  was edited into the deployed file — an added `hmc_run_command` grant, a narrowed connection
  list, a second policy. The obvious alternative, moving `access-policy.toml` aside and
  regenerating in place, leaves a window in which the platform-native path holds nothing or an
  unreviewed file, and under this record every start needs that file: an MCP stdio client
  respawns the server per session, so a reconnect during the window gets a server that exits 1
  for reasons unconnected to anything the client did. A running server is unaffected, the policy
  being frozen for its lifetime, which is exactly why the window is easy to miss.
- **The generator must run as the identity, and with the environment, that `serve` runs under.**
  Both resolve the file through `config.config_dir()`, which reads `XDG_CONFIG_HOME` or
  `Path.home()` — and on macOS `Path.home()` alone, with no override. Generating as a login user
  and serving as a systemd `User=` or a container uid produces a policy the server never reads —
  and, because the connections list is read through the same resolution, one naming the wrong
  profiles. Running the generator under the serving identity's environment fixes both; `--output`
  fixes only the write.
- **A uid with no passwd entry, no `HOME`, and no `XDG_CONFIG_HOME` can no longer serve at all.**
  `Path.home()` raises there, `--access-policy` selects a *name* at the platform-native path
  rather than a file, and `--output` moves only where the generator writes — so the load fails
  and omitting the option is now a refusal. Today such a deployment serves unbounded; after this
  it does not serve. The remedy is one environment variable in the unit or image, and it is
  stated in the README rather than answered with an `--access-policy-file` option, which would
  reintroduce a second place a policy can come from.
- **Statements in earlier records are retired.** ADR 0037's "`create_mcp(policy=None)` registers
  every tool" and ADR 0038's section "Without a policy, nothing is authorized" each describe a
  composition that no longer exists, as do R14 of the connection-scope design spec and R20 of the
  target-scope design spec. ADR 0039 is not among them: it expressly leaves `create_mcp`'s
  no-policy default undecided. Removing the module-level application retires more: ADR 0037's
  "the module-level `mcp` remains the unfiltered composition that tests and `scripts/` import" is
  a positive statement about an object that no longer exists, and ADR 0035, ADR 0036, and
  ADR 0037 each describe `create_mcp()` as called "at import and again per test", of which only
  the second half survives.
  Those records are otherwise unaffected and are not superseded; the tests asserting the retired
  behaviour are inverted rather than deleted, as ADR 0039 inverted ADR 0036's A7 test. The code
  they describe goes with them: `server._unselected_policy_file` and the no-policy branch of
  `server._startup_warnings` it exists to feed are unreachable once no server starts without a
  policy, and both are removed rather than left standing.
- **`hmc_effective_permissions` can no longer report a null policy through a served application.**
  `server_permissions.describe` keeps its `AccessPolicy | None` parameter and its honest
  null-reporting arm — it is documented as binding a direct caller, and this record does not make
  a direct caller impossible — but no composed application reaches that arm any more.
- **Importing `hmc_mcp.server` no longer composes an application.** `mcp = create_mcp()` ran at
  import and registered 129 tools; removing it makes the import cheap and makes any code that
  depended on the import side effect fail loudly rather than quietly get a surface it never asked
  for.
- **The generated policy is the first whose `connections` come from `config.toml` rather than
  from an operator's hand, and `hmc_effective_permissions` echoes them to the MCP client.**
  ADR 0037 accepted that tool's disclosure on the premise that "no value is read from
  `config.toml`, from an `HMC_*` environment variable, or from the HMC" — true of every policy
  written by hand, and no longer true of this one. What a client can now read is the deployment's
  full **profile-key inventory**: names only, no host, user, port, or credential, and strictly
  less than `hmc_list_configured_hosts` already discloses to the same caller. So the exposure is
  small and the premise is not; the premise is corrected here rather than the code changed,
  because narrowing the report would break the ADR 0012 output contract to hide a name the same
  client can ask for directly. An operator who considers their profile keys sensitive should
  withhold both tools by name, which ADR 0037 made possible.
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

**Warn for one release, then refuse in the next.** The standard answer to a breaking default, and
nearly free here: `_startup_warnings` already carries a policy-not-selected line, gated on the
file existing, and widening that gate to fire whenever no policy is selected is a one-condition
change that would give operators a release of notice. Rejected because requirement 9 asks for the
refusal in this entry rather than the one after it, and because the package is 0.1.0 — a
deployment base that does not justify carrying a second unbounded release to soften the landing.
The generator is the softer landing.

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

**Default to the sole policy in `access-policy.toml`.** It would remove the launcher edit from the
migration entirely. Rejected because it makes the file activate by existing, which is the
property the packaged-default entry below rejects reached by another route: an operator who
generates a policy in order to read it would find it enforcing before they had finished reading,
and requirement 9's *explicitly selected* is exactly the word that forecloses it.

**Select the policy through an `HMC_ACCESS_POLICY` environment variable.** Weighed separately,
because the argument above does not reach it: naming a policy in a variable is a deliberate act,
a generated file under it stays inert, and it is arguably an explicit selection. It is rejected
on two grounds of its own. It does not buy what it appears to buy — under the stdio transport an
MCP client's configuration carries `env` in the same client-owned block as `command` and `args`,
so an operator who cannot edit one cannot edit the other, and the launcher edit is not avoided.
And ambient process state selecting an authorization policy is worse than an argument visible in
the process line: a variable exported in a shell profile or inherited from a unit file would
carry a selection into a `serve` nobody meant to constrain that way, which is the class of
accident `--access-policy` on argv cannot have.

**Give the generator a posture argument, so it can emit the read-only policy too.** It would fix
the ergonomic gradient the fresh-install consequence concedes: today the one command that works
produces the widest grant, and the narrower posture is hand-copied from the README. Rejected
because the migration is the only case where the file's contents cannot be chosen by the operator
— legacy exposure is a fact about their existing deployment, and reproducing it by hand across
129 tool names is what makes a generator worth having. A read-only policy is four lines that the
operator should read as they write, and a second generated posture would need its own connection
list, its own review guidance, and its own regeneration story for a document nobody needs
generated.

**Keep a named opt-out.** A `--no-access-policy` token by which an operator states they accept an
unbounded server, as a gentler removal of the escape this record closes. Rejected because it is
the same escape under a longer name, and the whole difficulty ADR 0039's R12 and R13 create is
that an escape exists at all; an operator who needs a permissive server can say so in a policy
that grants everything, and that policy is exactly what the generator writes.

**Let the live-test runner extend the grant itself, and give the compiler no escape-hatch knob.**
It would keep the arbitrary-command grant where the arbitrary-command user is, and leave
`legacy_policy` with no parameter justified by a script. Rejected because the harness's value is
that it exercises the shipped composer: a hand-built grant beside the compiler is one that can
drift from what the generator produces, and then the live run stops being evidence about the path
operators take. The opt-in keeps one compiler and confines the divergence to one boolean.

**Add `tomli-w` and serialize the document rather than rendering it.** The obvious way to avoid
hand-rolling an emitter and its escaping. Rejected on epic requirement 11, which forbids a new
runtime dependency; the document is one table with three keys, and the escaping it needs is the
TOML basic-string set, which is a dozen lines and fully covered by the round-trip test.

**Ship a default legacy-equivalent `access-policy.toml` in the package.** Rejected because a
policy file that exists is a policy file that can be selected by name without review, which
inverts "generation does not activate it" — and because the correct connection list is the
deployment's own, which a packaged file cannot know.

**Block this change on #269.** Considered seriously, because this record is what makes #269
reachable everywhere. Rejected because #269's mechanism is unchanged here — the same handler,
the same three guards, the same synchronous write — and its own record already chose option 1,
"accept it and say so in the operator documentation", when ADR 0040 shipped. What changes is
reach, and reach is exactly what a documented precondition can cover. Holding the fail-closed
default hostage to a liveness hardening would leave every deployment unbounded for longer in
order to avoid a hang that requires a client which never drains its child's stderr. The
precondition is documented in the README and named above; #269 remains open and its priority
should be re-read against this record.
