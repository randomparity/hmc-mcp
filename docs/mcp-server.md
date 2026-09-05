# MCP server guide

[Documentation index](index.md) · [MCP quick start](../README.md#mcp-quick-start)

## MCP server

**`--access-policy NAME` is required.** The server refuses to start without one
rather than serving unbounded. If you are upgrading, generate a policy matching what
your server exposed before, read it, then select it — see
[Migrating to a required access policy](#migrating-to-a-required-access-policy).

Create a `lab` policy using the read-only example below before running these commands.
For first-time setup, the [MCP quick start](../README.md#mcp-quick-start) walks through
creating a policy and connecting a client.

```bash
hmc-mcp serve --access-policy lab            # stdio — what MCP clients/agents expect
hmc-mcp serve --access-policy lab --http --listen-host 127.0.0.1 --port 8000
# Explicitly enable the arbitrary-command MCP escape hatch when required. The policy
# must also grant hmc_run_command by name; the flag alone is not enough:
hmc-mcp serve --access-policy lab --enable-arbitrary-command
hmc-mcp serve --access-policy lab --audit-level WARNING   # authorization denials only on stderr
```

`--access-policy NAME` enforces the named policy from the platform-native
`access-policy.toml`: the server registers only the tools that policy permits, so
a withheld tool never appears in `tools/list` and cannot be called by name. Omitting
it is a usage error (exit 2) that starts nothing; a policy that is selected but
cannot be read, parsed, or compiled — or whose file does not exist — exits 1 and
starts nothing. Both refusals name the generator and the file they looked for.

All three dimensions — tools, connections, and targets — are enforced, and a
call is permitted only when a **single** grant covers all three together. A
grant that names your connection and a *different* grant that names your target
do not combine. Every refusal happens before the server opens any connection to
an HMC. Call `hmc_effective_permissions` on a running server to see what the
selected policy actually applies.

Connection scope is decided on the connection the call will *actually* select,
not on the `profile` string the caller passes, so a nickname is resolved to the
profile it targets before the check. Three consequences are worth knowing
before you author a policy:

- **`connections` entries must be profile keys**, not nicknames. A nickname is
  resolved away before the comparison, so granting one never matches.
- **When `HMC_HOST` is set, the server can reach exactly one HMC and the
  `profile` argument is ignored** — that is how `build_config` has always
  resolved. Every call is therefore evaluated as `<default>`, so grant
  `connections = ["<default>"]`; a policy naming profile keys denies everything
  in that deployment, and says so in the denial.
- **`<default>` binds late, and it binds to whatever the deployment resolves.**
  It is not a fixed HMC: absent `HMC_HOST` it follows `HMC_PROFILE`, then
  `default_profile` — which may itself be a nickname, so the granted connection
  can be two hops from anything written in the policy. Granting `<default>`
  beside a narrow profile list therefore also grants the current default, even
  when that is a profile the policy deliberately withholds. Do not grant it
  unless the deployment's default is a connection you mean to allow.

Omitting `profile` means `<default>`, which is *not* covered by a grant naming
the profile that happens to be the deployment default — grant both if callers
may omit the argument.

### What the policy does not bound

The access policy bounds **this MCP server**. It does not bound `hmc-mcp`
commands run at a shell, and it does not bound a Python program importing the
supported reusable API — both reach the HMC directly under the operator's own
credentials, and
[ADR 0029](adr/0029-supported-reusable-python-api-contract.md) places MCP
tools, CLI commands, and the server composition modules outside that API's
contract for the same reason. If you need a constraint that binds a human at a
shell, use HMC-side user roles.

Every MCP tool is dispatch-wrapped.
For a connectionless tool, the connection dimension is vacuous because the call
selects no profile to authorize. A grant must still reach the tool by tool or
effect class, and target authorization still applies. `hmc_list_configured_hosts`
returns every configured profile's name, host, user, and default flag, while
`hmc_effective_permissions` returns the policy's own grants. Both are
non-exhaustive connectionless tools, so a targets table cannot authorize either
one; they require `targets = "all-targets"`. A `connections = ["lab"]` read grant
with `all-targets` can therefore still disclose the `prod` inventory. When the
configuration or policy is sensitive, withhold these tools by name: enumerate
the permitted `tools` without granting the `read` effect class.

### Migrating to a required access policy

An access policy used to be optional; a server started without one exposed every tool on
every configured connection. It is now required, so an existing deployment that upgrades
without one stops serving. Two commands and a read:

```bash
# 1. Write it. This activates nothing, and prints the path it wrote to stdout.
hmc-mcp config init-access-policy

# 2. Read it. It is one grant, and the tools array is the whole of your exposure.
$EDITOR ~/.config/hmc-mcp/access-policy.toml   # macOS: ~/Library/Application Support/hmc-mcp/

# 3. Select it, in whatever launches your server.
hmc-mcp serve --access-policy legacy-equivalent
```

The generated `legacy-equivalent` policy grants exactly what the unpolicied server granted:
every ordinary tool, every configured connection plus `<default>`, and
`targets = "all-targets"`. **It is a migration aid, not a recommended posture.** A new
deployment should start from the read-only example below and add what it needs — the
generated policy is the widest one this system can express.

Six things are worth knowing before you run it.

- **`hmc_run_command` is not in it.** The escape hatch stays a separate decision:
  `--enable-arbitrary-command` alone never exposed it, and a generated grant that named it
  would undo that. If you ran with the flag, add `hmc_run_command` to the grant's `tools`
  by hand — and note it will show as a deletion in every future regeneration diff, because
  the generator cannot emit it.
- **Run it as the identity, and with the environment, that `serve` runs under.** Both
  resolve `access-policy.toml` through the same config directory, and the connection list
  is read from that identity's `config.toml`. Generating as your login user and serving as
  a systemd `User=` or a container uid writes a policy the server never reads, naming
  profiles it does not have.
- **A container or unit needs a resolvable `HOME` or `XDG_CONFIG_HOME`.** Under a uid with
  no passwd entry and neither variable set, the path cannot be resolved and the server
  cannot start at any setting.
- **It never overwrites.** To check a deployed policy after an upgrade, run:

  ```bash
  hmc-mcp config diff-access-policy ~/.config/hmc-mcp/access-policy.toml
  ```

  This renders what the current build plus your current `config.toml` would generate
  and prints a unified diff against the deployed policy, exiting non-zero on any
  difference — see [Detecting access-policy drift](#detecting-access-policy-drift)
  below. Compare **both** the `tools` and the `connections` arrays in that diff. The
  policy is a snapshot of each: a tool a later release adds, and a profile you add to
  `config.toml`, are both ungranted until you add them to the deployed file by hand.
  Nothing in a running server surfaces either gap — `hmc_effective_permissions`
  reports what was registered, which is exactly what the policy produced — so this
  diff is the detection path.
- **If `serve` reports an `unknown tool`, the policy is stale, not corrupt.** Preserve the
  reviewed deployed policy, then run `config diff-access-policy` against it. Alternatively,
  run `config init-access-policy --output /tmp/access-policy.new`, review the generated
  policy's legacy-equivalent breadth, diff it against the deployed policy, and merge the
  intended changes by hand. A file that cannot be read or parsed as TOML may genuinely be
  truncated or corrupt; preserve it for review and recovery before generating a scratch copy
  and manually restoring the reviewed policy decisions.
- **`config.toml` and `access-policy.toml` are different files with different jobs.**
  `config.toml` holds **HMC connection profiles** — which consoles you can reach, and how.
  `access-policy.toml` holds **server access policies** — what an MCP server may do with
  them. They have separate lifecycles, and a grant's `connections` entries are profile
  *keys* from `config.toml`, never profile contents.

### Detecting access-policy drift

A generated policy is a snapshot of two things that both move on: the tool surface of
the build that ran the generator, and the profile keys in `config.toml`. After an
upgrade adds a tool, or you add `[profiles.newsite]` and restart, the deployed policy
grants neither — silently, because `hmc_effective_permissions` reports only the
registered set, which is exactly what the policy produced.

`config diff-access-policy` makes the comparison routine instead of something an
operator has to remember to do:

```bash
hmc-mcp config diff-access-policy ~/.config/hmc-mcp/access-policy.toml
```

It renders the legacy-equivalent policy exactly as `config init-access-policy` would —
same generator, same config-directory resolution, so run it as the identity and with
the environment `serve` runs under — and prints a unified diff against the deployed
document. Exit codes: `0` identical, `1` different (the diff goes to stdout, so a CI
log captures it directly), `2` usage error, `3` the deployed file could not be read,
`4` generation failed. A CI gate or health check asserts on exit status `1`.

The command compares full documents, so a deliberately narrow authored policy always
differs from the generated one — point it at deployments running the generated
`legacy-equivalent` policy, or at a copy of it.

Policies live in `access-policy.toml`, beside `config.toml` in the same
platform-native directory. A minimal read-only policy:

```toml
[[policies.lab.grants]]
effects = ["read"]           # "read", "mutate", "destructive"
connections = ["<default>"]  # profile names, or "<default>" for the env HMC
targets = "all-targets"      # or a table; see "Narrowing targets" below
```

A grant must name at least one tool through `effects`, `tools`, or both, and
must name at least one connection. `targets` is either the string
`"all-targets"` or a table of target kind to selector strings — a bare array is
rejected. `hmc_run_command` cannot be reached by effect class: name it in a
grant's `tools` to grant it, start the server with `--enable-arbitrary-command`
as well, and grant it under `"all-targets"`, since it declares no target
selector. All three are required, and they compose conjunctively.

A limited-mutation policy — read anywhere the deployment reaches, but change only the
lab, and never destroy anything:

```toml
[[policies.limited.grants]]
effects = ["read"]
connections = ["lab", "prod", "<default>"]
targets = "all-targets"

[[policies.limited.grants]]
effects = ["mutate"]         # "destructive" is deliberately absent
connections = ["lab"]
targets = "all-targets"
```

And the legacy-equivalent policy, which `hmc-mcp config init-access-policy` writes in full
— every ordinary tool named explicitly, which is why it is generated rather than typed:

```toml
[[policies.legacy-equivalent.grants]]
tools = [
    "hmc_add_network_adapter",
    "hmc_add_vfc_adapter",
    # ... every ordinary tool is named. hmc_run_command is not among them.
]
connections = ["<default>", "lab", "prod"]   # every profile key in config.toml
targets = "all-targets"
```

It names tools rather than granting `effects` deliberately: an effect-class grant would
silently confer every tool a later release adds, which is the silent privilege retention
the generator exists to replace.

### Narrowing `targets`

Substituting a table for `"all-targets"` is a much stronger statement than it
looks, so a narrowed policy is normally **two** grants:

```toml
# Exactly one partition, on exactly one system.
[[policies.lab.grants]]
tools = ["hmc_delete_lpar", "hmc_power_off_lpar"]
connections = ["lab"]
targets = { managed_system = ["sys-R1"], lpar = ["scratch-01"] }

# Everything console-wide, which no table can express.
[[policies.lab.grants]]
effects = ["read"]
connections = ["lab"]
targets = "all-targets"
```

Four rules explain why:

- **Every selector a tool declares must be supplied and must match** — not only
  the required ones. `hmc_power_off_lpar` takes an optional
  `system_name_or_uuid`; omitting it means "whichever system has a partition by
  that name", which a `managed_system` allowlist did not grant, so the call is
  refused. Likewise `hmc_list_lpars` without a system means *every* system.
- **Matching is exact string equality.** No globs, no case folding, and no
  name-to-UUID resolution — resolving would mean asking the HMC inside the
  check that is supposed to run before any HMC request. A policy written in
  names does not cover a call written in UUIDs, or the reverse.
- **A table never grants a tool it cannot bound.** Some tools declare no target
  selector at all (`hmc_list_systems`, `hmc_run_command` — they act on the
  console, whose identity *is* the connection). Others act on something the
  selectors do not name:
  `hmc_provision_lpar` mutates a VIOS chosen inside its `storage` argument, the
  LPAR-profile backup and restore pair write an arbitrary path on the HMC's own
  filesystem, three adapter tools take a VIOS *partition ID* (a slot number
  reused on every system in the fleet), and the two job tools accept a
  `job_href` whose path replaces the job UUID outright. Each of those needs a
  grant whose targets are `"all-targets"`. So do `hmc_effective_permissions` and
  `hmc_list_configured_hosts`, which read local state rather than an HMC and so
  have nothing a table could bind either. `hmc_effective_permissions` reports
  `exhaustive_targets: false` for every one of them. Naming any such tool in a
  grant's `tools` beside a table is refused at startup, with no exceptions.

  **A table-only policy therefore denies `hmc_effective_permissions` itself**, so
  a client has no way to ask the server what it may do. Give it the second grant
  above — that is what the `effects = ["read"]` / `"all-targets"` grant in the
  example is for — or expect the startup warning naming it.
- **An LPAR name is unique within a system, not across the fleet.** Every tool
  that takes an `lpar_name_or_uuid` also takes an optional
  `system_name_or_uuid`, and every LPM tool names its source system the same
  way ([ADR 0063](adr/0063-source-system-selectors-for-fleet-ambiguous-lpar-tools.md)),
  so the rule above pins the system on all of them: under a table, a call that
  omits the selector is refused, and one allowlist entry must match both
  migration endpoints. Where you grant a tool under `"all-targets"`, or call it
  outside a table-constrained policy and omit the selector, a partition name is
  still matched on whichever system has one — list partition **UUIDs** there,
  which are unique across the fleet.

### Startup warnings

`serve` writes these to stderr, never stdout, which carries JSON-RPC on stdio — with
one caveat that applies to the audit records below as well: a launcher merging the
descriptors (`serve 2>&1`, or a unit file doing the same) makes stderr *become* the
JSON-RPC channel, and nothing inside the process can detect that.

| Condition | What it means |
|-----------|---------------|
| The served surface has no tools | The policy withholds everything reachable; nothing the server is asked to do will succeed. Suppresses the next line. |
| The policy withholds `hmc_effective_permissions` | The server cannot report its own permissions to a client. Any policy that neither grants the `read` effect class nor names the tool in a grant's `tools` causes this. |
| `--enable-arbitrary-command` was passed but the policy does not grant `hmc_run_command` | The flag and the ceiling compose conjunctively, so the escape hatch is not exposed. Name it in a grant's `tools` to allow it. |
| A grant's `targets` table cannot bind some of the tools it reaches | One line per such grant, naming them. Those tools are registered and advertised, and every call to them is denied — a table cannot bound them, so only an `"all-targets"` grant reaches them. Watch for `hmc_effective_permissions` in the list: when it is there, the server cannot report its own permissions even though the policy grants the tool. |

> **Security:** the streamable-HTTP transport is **unauthenticated**. It
> exposes enabled tools — including user administration — to anyone who can reach the
> port. Keep the default loopback bind. `serve --http` refuses a
> non-loopback `--listen-host` unless `--allow-remote` is passed; if you need remote
> access, put an authenticated reverse proxy (MCP gateway or HTTPS proxy with
> bearer-token auth) in front and never expose the port directly. The arbitrary
> `hmc_run_command` escape hatch is disabled unless the server starts with
> `--enable-arbitrary-command`.

Every tool is documented in the
[MCP tool reference](https://github.com/randomparity/hmc-mcp/blob/main/docs/tools/index.md):
one page per operation domain, each row carrying the tool's effect class, operation, target
kind, and summary. Those pages are generated from the server's own registry by
`scripts/gen_tool_reference.py`, and `just tool-docs-check` fails CI when they fall behind the
code — which the hand-maintained table this replaced had no way to do. MCP clients also receive
a rendered description for every tool parameter, including fields nested inside structured
inputs such as LPAR resources, firmware update parameters, and VIOS update repositories.

`hmc_effective_permissions` discloses the selected policy's name, its absolute
path, every connection token, and every target selector to any MCP client that
can call it. Since #470 it also discloses, per granted connection, the effective
`authorize_power_operations` value; whether `HMC_AUTHORIZE_POWER_OPERATIONS` is
exported in the served process's environment and whether that spelling is exact
or a case variant; whether each granted connection resolves in `config.toml`, as
`source: unresolved`; and, inferably, whether `HMC_HOST` is set, because the guard
rows then collapse to `<default>` while `declared_grants` still names the profiles.
It carries no credential. One caveat since the generator exists: a generated
`legacy-equivalent` policy has `config.toml`'s profile **keys** as its
`connections`, so those names reach the client through this tool — names only, and
strictly less than `hmc_list_configured_hosts` discloses to the same caller. If your
profile keys are themselves sensitive, withhold both tools by name. The policy
path is the exception it is not — it is built from `XDG_CONFIG_HOME` (Linux),
`%APPDATA%` (Windows), or your home directory, so it names the account, and it
is disclosed deliberately so an operator can tell which file is in effect. Any
policy that neither grants the `read` effect class nor names the tool in a
grant's `tools` withholds it; a policy granting `read` reaches it and cannot
exclude it.

`hmc_fleet_health` and `systems health` return only exceptions across the whole
estate: non-operating systems, non-running VIOS partitions, LPARs with inactive
RMC, and recent failed jobs. This is not equivalent to composing N
`hmc_system_summary` calls or using `hmc_capacity_report`, which report per-system
inventory and capacity rather than individual unhealthy resources. On HMCs that
do not support global Job listing, the stable response keeps `failed_jobs` empty
and includes a warning that recent-job health is unavailable; that warning must
not be interpreted as a healthy job feed.

### Authorization audit records

With a policy selected, every authorization decision writes one line of JSON to stderr
on the `hmc_mcp.audit` logger — policy, tool, effect class, decision, a stable reason
code, the connection selector, and the declared target selectors. Denials are `WARNING`
and permits are `INFO`. Credentials, whole argument sets, command text, and response
bodies are absent by construction.

`--audit-level LEVEL` on `hmc-mcp serve` tunes that stream: `DEBUG` and `INFO` keep both
records (the default), `WARNING` keeps denials only, and `ERROR` or `CRITICAL` silences it.
An unknown level name is a usage error that starts nothing.

Every deployment writes these, because every deployment now selects a policy. Delivery is
**asynchronous and droppable**: records go onto a bounded in-memory queue drained by one
background thread, so a destination nobody is reading can cost you records but cannot stop
the server answering. A dropped line is never silent — the next line that lands is preceded
by `{"event": "records-dropped", "count": N}`. See
[ADR 0043](adr/0043-non-blocking-stderr-diagnostics.md); a full trail still wants
something reading the server's stderr (under stdio that is the MCP client, not you; under
`--http` it is whatever supervisor or journal collects the unit's stderr). ADR 0011
ownership-override and ownership-denied records are not policy-gated and are emitted on
the CLI and Python API paths too.

See [docs/authorization-audit.md](authorization-audit.md) for the field set, the
reason codes, and how to route or silence them.

## Client setup

The [README quick start](../README.md#mcp-quick-start) shows a stdio client configuration.
The client must launch the installed `hmc-mcp` executable with the connection environment
and access-policy file available to that process.

### Use with Hermes Agent

```bash
hermes mcp add hmc -- hmc-mcp serve \
  --access-policy legacy-equivalent
```

See [Operation details](operations.md) for collection limits, selectors, units, and
firmware-specific behavior.
