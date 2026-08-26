# hmc-mcp

MCP server and CLI for the **IBM Hardware Management Console (HMC) REST API**
(`/rest/api/uom/...`). It lets you — or an AI agent over MCP — inventory
Power systems, inspect LPARs/VIOS, and submit jobs such as power on/off.

## Stack

- **Python ≥3.11**, managed with [uv](https://docs.astral.sh/uv/). The project supports every
  stable, non-EOL CPython release at or above that floor.
- **MCP server**: [FastMCP](https://gofastmcp.com/) (stdio or streamable HTTP)
- **CLI**: [Typer](https://typer.tiangolo.com/) + Rich tables
- **REST transport**: httpx (async), XML parsed with defusedxml
- **CLI passthrough**: [asyncssh](https://asyncssh.readthedocs.io) — tools that
  shell out to HMC CLI commands (`lssyscfg`, `lshwres`, `chsyscfg`, ...) over
  SSH

## Contributing, security, and license

See [Contributing](CONTRIBUTING.md) for the development and pull-request path. Report suspected
vulnerabilities through the private channel in the [Security policy](SECURITY.md). This project is
available under the [License](LICENSE).

## Install

For reusable-library use, install the bare project dependencies:

```bash
cd ~/src/hmc-mcp
uv sync --no-dev
```

To use the CLI or MCP server, install the `app` extra as well:

```bash
cd ~/src/hmc-mcp
uv sync --no-dev --extra app
```

## Reusable Python API

Import reusable library code only from `hmc_mcp.api`. This example reads connection settings from
the `HMC_*` environment variables described below, constructs a client, and runs an exported domain
operation:

```python
import asyncio

from hmc_mcp.api import HMCClient, HMCConfig, capacity_report


async def main() -> None:
    async with HMCClient(HMCConfig()) as hmc:
        for system in await capacity_report(hmc):
            print(system)


asyncio.run(main())
```

`hmc_mcp.api` is the only supported reusable-library import path. Its explicit `__all__` is the
complete compatibility manifest. For `HMCClient`, only `__init__`, `__aenter__`, `__aexit__`,
`is_logged_on`, `logon`, and `logoff` are supported lifecycle members. Other import paths, generic
UOM helpers, inherited mixin methods, XML and parser helpers, SSH primitives, and CLI and MCP
presentation modules are implementation details. They may remain importable or discoverable, but
they are unsupported and may change without a compatibility release.

The distribution ships a PEP 561 `py.typed` marker, so a type-checker reads the facade's inline
annotations instead of treating every value as `Any`. That covers exactly the surface `__all__`
declares: each export's call signature, the fields and constructor of each exported package-owned
model, the keys of each exported `TypedDict`, each exported exception type, and the members and
values of each exported enum and literal alias. Modules outside `hmc_mcp.api` carry annotations too, but they are implementation details and
their types are not part of the contract.

What the marker does not do is make the open-ended HMC payloads specific. Operations that return a
raw resource mapping are annotated `dict[str, Any]`, and ADR 0029 keeps them that way deliberately
so an IBM-side field addition is not a breaking change — the call is typed, the payload contents
stay opaque. Operations that return a package-owned result model are typed all the way down.

One consequence is worth planning for: the operations annotate the concrete `HMCClient`, so a
type-checker now rejects a duck-typed fake passed in a consumer's own tests even though the call
still runs. ADR 0029 deliberately promises no alternate-client protocol, so pass such a fake through
`typing.cast(HMCClient, fake)` at the call site, or silence that call with
`# type: ignore[arg-type]`.

While hmc-mcp is in `0.x`, strict SemVer applies to this supported surface: removing or renaming an
export, invalidating a compatible call, changing an owned model incompatibly, changing an exported
enum or literal value set, or adding a facade export requires a minor release. Patch releases are
limited to compatible fixes that change neither the export set nor enum and literal value sets.
See [ADR 0029](docs/adr/0029-supported-reusable-python-api-contract.md) for the complete contract.

## Configure

Configuration priority (highest to lowest): **CLI flags > `HMC_*` env vars > TOML profile**.

### TOML profile (recommended for multi-HMC setups)

Create `~/.config/hmc-mcp/config.toml` (Linux / macOS `~/Library/Application Support/hmc-mcp/config.toml` / Windows `%APPDATA%/hmc-mcp/config.toml`):

```toml
default_profile = "prod"

[profiles.prod]
host = "hmc.example.com"
port = 443                         # optional; omit to allow legacy fallback
user = "admin"
password_env = "HMC_PROD_PASSWORD"   # resolved from the environment at runtime  # pragma: allowlist secret

[profiles.dev]
host = "hmc-dev.example.com"
user = "devadmin"
password = "devpassword"              # or store inline for non-production  # pragma: allowlist secret
```

Select a profile with `--profile <name>` or `HMC_PROFILE=<name>`.
`password_env` keeps secrets out of the file; `password` is accepted for convenience.

#### Profile nicknames (friendly names)

When you run more than one HMC, remember the exact `[profiles]` key for every
`--profile` / `HMC_PROFILE` call. A top-level `nicknames` table maps a friendly
name to an existing profile key, so a memorable name resolves to a profile:

```toml
[nicknames]
big-iron = "prod"
staging  = "stg-hmc-03"
```

`big-iron` and `staging` now work anywhere a profile name does — `--profile
big-iron`, `HMC_PROFILE=staging`, and even `default_profile = "big-iron"` —
because resolution is a single name-selection step inside the profile loader, so
the CLI and every MCP tool inherit it with no per-tool change.

Rules:

- **One level deep.** A nickname resolves to a *profile key*; it never resolves
  to another nickname (no chains, no cycles).
- **Case-sensitive.** `big-iron` does not match `BIG-IRON`.
- **Profile key wins on collision.** A name that is both a profile key and a
  nickname key selects the profile.
- **Clear failures.** A nickname whose target is not a profile, an unknown name,
  or a malformed `nicknames` table raises a `ConfigError` naming the available
  profiles and nicknames.

Nicknames are *surfaced, not hidden*. `config list` prints each nickname as
`nick -> target` (flagging a dangling target), `config show <nick>` shows the
resolved profile with a `resolved_from` field naming the nickname, and
`hmc_list_configured_hosts` reports each nickname and its target-existence — none
of which resolves a secret. `config init` scaffolds a commented `nicknames`
example. A guardrail (`just nicknames`, in `just verify`) validates a committed
fixture: every nickname target exists, no nickname collides with a profile key,
and no target is itself a nickname.

### Environment variables (single-HMC / MCP server)

| Setting           | Env var              | CLI flag          | Default   |
|-------------------|----------------------|-------------------|-----------|
| Profile           | `HMC_PROFILE`        | `--profile`       | —         |
| HMC host / IP     | `HMC_HOST`           | `--host`          | —         |
| REST port         | `HMC_PORT`           | —                 | `443`     |
| User              | `HMC_USER`           | `--user, -u`      | —         |
| Password          | `HMC_PASSWORD`       | `--password, -p`  | —         |
| Verify TLS        | `HMC_VERIFY_SSL`     | `--verify-ssl`    | `false`   |
| HTTP timeout (s)  | `HMC_TIMEOUT`        | —                 | `60.0`    |
| SSH timeout (s)   | `HMC_SSH_TIMEOUT`    | —                 | `300.0`   |
| SSH key file      | `HMC_SSH_KEY_FILE`   | —                 | —         |
| Audit memento     | `HMC_AUDIT_MEMENTO`  | —                 | `hmc-mcp` |
| Schema version    | `HMC_SCHEMA_VERSION` | —                 | _(unset)_ |

When the REST port is omitted, hmc-mcp tries port 443 and retries logon once on
legacy port 12443 only if the first attempt fails at the transport layer. Setting
`port` in TOML or `HMC_PORT` selects that port explicitly: a connection failure
is returned immediately and never falls back. On an older HMC, leaving the port
unset can therefore add the duration of the failed 443 attempt. `HMC_TIMEOUT`
applies to each HTTP timeout phase rather than to the combined two-attempt wall
clock time. If a 443 logon response is lost, that unreachable attempt may leave
a server-side session until the HMC expires it.

See [`docs/environment-variables.md`](docs/environment-variables.md) for the
full reference, including descriptions and usage notes.

HMCs ship self-signed certificates, so TLS verification is off by default and
`hmc-mcp` warns on every logon while it stays off. To verify the HMC
certificate, install its CA locally and set `HMC_VERIFY_SSL=true`
(`--verify-ssl`) — otherwise the HMC credentials are at risk of
man-in-the-middle interception.

## HMC version compatibility

`hmc-mcp` targets **HMC V8 through V11** and all the POWER generations they
manage. All uom XML documents are written with `schemaVersion="V1_0"` — the
floor every supported HMC understands — so create/modify operations succeed
regardless of firmware age.

| HMC version | POWER generations managed | uom schema floor |
|-------------|--------------------------|------------------|
| HMC V8      | POWER6, POWER7, POWER8   | V1_0             |
| HMC V9      | POWER7, POWER8, POWER9   | V1_0             |
| HMC V10     | POWER8, POWER9, POWER10  | V1_0             |
| HMC V11     | POWER9, POWER10, POWER11 | V1_0             |

Three VIOS backup catalog tools have a narrower floor:
`hmc_list_vios_backups`, `hmc_backup_vios`, and `hmc_restore_vios` require
**HMC V10 or newer**. Their supported HMC commands do not exist in the V9.1.940
command inventory, so these tools have no runtime version probe or V8/V9
fallback. Other tools retain the general HMC V8 through V11 support stated
above.

**`HMC_SCHEMA_VERSION` — leave this unset for normal operation.**
`hmc-mcp` omits the `X-HMC-Schema-Version` request header from all write
paths (`PUT`/`POST`) regardless of this setting — some HMC firmware versions
return HTTP 406 on every UOM write when that header is present. The variable
only affects `GET` requests. Set it only if you are debugging schema
negotiation on a specific HMC read path; it has no effect on LPAR creation,
adapter configuration, storage operations, or any other mutating call.
See [`docs/environment-variables.md`](docs/environment-variables.md) for all
supported variables.

### Firmware write-path compatibility

Some HMC V10 firmware builds return HTTP 406 for all UOM write paths — even
without the schema-version header — for child-resource endpoints such as
`ClientNetworkAdapter` and `VirtualSCSIClientAdapter` PUT. On those builds:

- **LPAR creation** (`hmc_create_lpar`, `hmc_provision_lpar`): automatically
  falls back to `mksyscfg` over SSH. `HMC_PASSWORD` (or `HMC_SSH_KEY_FILE`)
  must be set for SSH auth; the fallback is transparent to the caller.
- **Virtual adapter attachment** (`hmc_add_network_adapter`,
  `hmc_add_vscsi_adapter`): no automatic fallback. Configure adapter profiles
  via the HMC GUI, the HMC CLI (`chhwres`), or the opt-in `hmc_run_command`
  escape hatch if this affects your firmware.
- **Virtual disk creation** (`hmc_create_virtual_disk`): no automatic fallback.
  The disk can be created directly on the VIOS with `mkbdsp` and then mapped
  with `hmc_map_storage_to_lpar`.

## CLI usage

```bash
hmc-mcp console info                 # connectivity check / HMC version
hmc-mcp systems list                 # table of managed systems
hmc-mcp systems show <uuid>
hmc-mcp systems summary <uuid>       # one-call summary: state, MTMS, firmware, LPARs, free resources
hmc-mcp systems health               # exception-only fleet health; add --json for automation
hmc-mcp lpars list                   # all LPARs
hmc-mcp lpars list --system <uuid>   # LPARs of one system
hmc-mcp lpars show mylpar            # by name or UUID (JSON)
hmc-mcp lpars state mylpar           # just "running", "not activated", ...
hmc-mcp lpars summary mylpar         # one-call summary: state, RMC, memory, CPU, OS, adapters
hmc-mcp lpars get-minimum-affinity-policy mylpar sys1 --json
hmc-mcp lpars system-memopt-score sys1
hmc-mcp lpars plan-memopt-scores sys1 --prioritize-name web --exclude-name batch
hmc-mcp lpars plan-system-memopt-score sys1 --prioritize-id 3 --exclude-id 9 --json
hmc-mcp lpars create web01 --system <uuid> --mem 8192 --vcpus 2
hmc-mcp lpars modify web01 --mem 16384 --procs 2.0   # assign resources
hmc-mcp lpars delete web01           # destroy (must be powered off)
hmc-mcp lpars decommission web01 --system <uuid> --dry-run   # preview blast radius
hmc-mcp lpars power-on mylpar        # submits a PowerOn job (asks first)
hmc-mcp lpars power-off mylpar --immediate
hmc-mcp adapters list mylpar                    # network adapters (default type)
hmc-mcp adapters list mylpar --type VirtualSCSIClientAdapter
hmc-mcp adapters add-network mylpar --vlan 100  # add a NIC on VLAN 100
hmc-mcp adapters add-vscsi mylpar --vios-id 1 --vios-slot 5
hmc-mcp adapters add-vfc mylpar --vios-id 1 --vios-slot 6
hmc-mcp adapters delete mylpar --type ClientNetworkAdapter --uuid <adapter-uuid>
hmc-mcp vios list
hmc-mcp jobs list                    # recent jobs (default 20)
hmc-mcp jobs list -n 5               # last 5 jobs
hmc-mcp jobs show <job-uuid>
hmc-mcp raw get /rest/api/uom/VirtualSwitch   # escape hatch, prints XML
```

Add `--json` to list commands for machine-readable output. Composite workflows
such as `hmc-mcp lpars decommission` and `hmc-mcp lpars provision` return a
stable envelope with `workflow_completed`, ordered `steps`, `warnings`, and the
blast-radius or provisioning summary, which is friendlier for automation than
raw HMC payloads. Every entry is the parsed uom resource:
`{UUID, title, link, ResourceType, Resource}` where `Resource` is the flattened
XML (namespace-stripped, HMC bookkeeping attributes removed).

The memory-affinity planning commands are read-only `lsmemopt` calculations.
Repeat `--prioritize-name`, `--prioritize-id`, `--exclude-name`, or `--exclude-id`
to describe a scenario, using names or IDs consistently. Calculated scores are
predictions, not guarantees of placement, and these commands never start optimization.

The minimum-affinity policy command is also read-only. It first checks whether the managed
system advertises `POWER11` processor compatibility, then explicitly requests
`min_affinity_score` and `min_affinity_score_action` through `lssyscfg`. The score is validated
as an integer from 0 through 100 and the action as `none`, `warn`, or `fail`. Systems without
that capability remain usable and return an actionable `capability-unavailable` reason. This
surface does not provide a setter; portable snapshots record the policy only when supported.

Portable snapshots capture one named LPAR profile together with source identity and separate
timestamped placement and affinity observations:

```bash
hmc-mcp snapshot capture sys1 aix1 default --output aix1.snapshot.json
hmc-mcp snapshot validate aix1.snapshot.json
hmc-mcp snapshot inspect aix1.snapshot.json
```

Capture refuses to overwrite an existing local file. Validation and inspection are local and
perform no HMC I/O. Snapshots do not expose a replay command; observation data is diagnostic and
never part of the replayable profile configuration.

`hmc-mcp lpars decommission` enforces the ADR 0011 ownership token even for
`--dry-run`; use `--ownership-override` only after explicit operator approval.

## MCP server

**`--access-policy NAME` is required.** The server refuses to start without one
rather than serving unbounded. If you are upgrading, generate a policy matching what
your server exposed before, read it, then select it — see
[Migrating to a required access policy](#migrating-to-a-required-access-policy).

```bash
# Once: write a policy for review. It activates nothing and never overwrites.
hmc-mcp config init-access-policy

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
[ADR 0029](docs/adr/0029-supported-reusable-python-api-contract.md) places MCP
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
- **If `serve` reports a policy that will not compile and the generator reports the file
  already exists**, the file is truncated or corrupt. Delete it and re-run the generator.
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
targets = { managed_system = ["Server-9080-HEX-SN123456"], lpar = ["scratch-01"] }

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
  way ([ADR 0063](docs/adr/0063-source-system-selectors-for-fleet-ambiguous-lpar-tools.md)),
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
can call it. It carries no credential. One caveat since the generator exists: a
generated `legacy-equivalent` policy has `config.toml`'s profile **keys** as its
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
[ADR 0043](docs/adr/0043-non-blocking-stderr-diagnostics.md); a full trail still wants
something reading the server's stderr (under stdio that is the MCP client, not you; under
`--http` it is whatever supervisor or journal collects the unit's stderr). ADR 0011
ownership-override records are not policy-gated and are emitted on the CLI and Python API
paths too.

See [docs/authorization-audit.md](docs/authorization-audit.md) for the field set, the
reason codes, and how to route or silence them.

### Collection limits

Feed-backed collection tools accept an optional client-side `limit`. The complete HMC feed is
still transferred and parsed before the result is truncated: the limit bounds only the number of
entries returned to the agent, not HMC work, network bytes, parsing cost, or the size of each
entry. `hmc_list_recent_jobs` defaults to 20 entries; the other affected collection tools are
unbounded when `limit` is omitted.

### Public parameter units and selectors

Storage quantities use binary-unit suffixes: `capacity_mib` for virtual disks,
`size_mib` for media repositories and optical media, and `lu_size_gib` for
Shared Storage Pool logical units. Numeric virtual-switch selectors are named
`virtual_switch_id`. Verified vNIC mutations instead select the backing VIOS,
SR-IOV adapter, and physical port; they do not accept a virtual-switch selector.

The NIM install tools distinguish `hmc_timeout_minutes` from the client-side
`wait_timeout_seconds`. With `wait=True` and no explicit client budget, the
client waits for the HMC timeout converted to seconds plus one polling interval,
so it can observe the terminal state at the HMC deadline. LPM's separate
`wait_time` value is an HMC migration/validation field measured in seconds.

Every partition tool accepts an optional `system_name_or_uuid`
(SystemName or UUID) that disambiguates its `lpar_name_or_uuid`; omitted, the
name is searched fleet-wide. The LPM tools take it as the *source*-system
selector beside their required `target_system_name_or_uuid`, so a policy table
must match both endpoints against its `managed_system` allowlist.

> **SSH/CLI tools** — some tools reach the HMC by running CLI commands over SSH
> rather than through the REST API.
> Their system/LPAR arguments (`system_name_or_uuid` / `lpar_name_or_uuid`)
> accept either a CLI name or a UUID. Names are used as-is; UUIDs are resolved
> to their CLI names via the REST API first, falling back to an `lssyscfg` name
> lookup over SSH when the REST API is unreachable. Resolution happens before
> the command runs, so a UUID that cannot be resolved surfaces as an error
> rather than being passed through to the CLI. VIOS backup and restore are an
> exception: a direct system name plus VIOS UUID is SSH-ready, but a system UUID
> requires REST to resolve its unique MTMS identity and has no `lssyscfg`
> fallback. Separately, the opt-in `hmc_run_command` tool runs whatever command
> you give it verbatim without selector resolution.
> See [docs/hmc-cli-cheatsheet.md](docs/hmc-cli-cheatsheet.md) for a concise
> reference to all HMC CLI commands used by this project.

`hmc_backup_vios` and `hmc_restore_vios` retain required managed-system and VIOS
selector metadata for authorization diagnostics and audit. Because their `ssp`
mode can affect the wider cluster, both tools are non-exhaustive and require
`targets = "all-targets"`. Prefer a grant that explicitly names only the needed
tool. An effect-class grant with `all-targets` can reach either tool; a targets
table cannot authorize either one even when it contains both selector kinds.

The verified vNIC mutation contract is admitted only for POWER9 8375-42A managed by HMC V10R3
M1060. `hmc_add_vnic` selects one backing with `vios_name`, `vios_lpar_id`, `adapter_id`,
`physical_port_id`, and `capacity_percent`, plus the vNIC `port_vlan_id`. The HMC assigns the
logical-port ID; callers do not supply one. A successful or unchanged result reports the assigned
ID in `backing_after[].logical_port_id` after correlated HMC readback. `hmc_remove_vnic` accepts
the `slot_num` reported by `hmc_list_vnics`, verifies its single active Operational backing, and
removes that slot.

Add and remove return the stable fields `operation`, `mutation_dispatched`, `changed`, `selector`,
`slot_num`, `vnic_before`, `backing_before`, `vnic_after`, `backing_after`,
`vnic_after_read_succeeded`, `backing_after_read_succeeded`, `output`, and `errors`. Once a
mutation has been dispatched, a command or reconciliation failure raises a structured partial
error carrying the same result evidence; there is no rollback promise. An empty inventory with
its matching read-succeeded flag set means verified absence.

| vNIC capability | Admitted | Notes |
|-----------------|----------|-------|
| One SR-IOV backing on POWER9 8375-42A / HMC V10R3 M1060 | Yes | Exact family and HMC level only |
| Caller-selected logical-port ID | No | The HMC allocates and readback reports it |
| Multiple backings or failover topology | No | Ambiguous or degraded topology fails before mutation |
| Priority or maximum-capacity inputs | No | HMC defaults remain in effect |
| Other server families or HMC levels | No | Capability checks fail before mutation |
| Rollback after a dispatched mutation | No | Partial errors retain observed before/after evidence |

> **PCM notes**: metrics are stored as *JSON*, reached via an Atom feed of
> links. The `*_metric_links` tools return the link list, while the `*_metrics`
> tools download the most recent document (or `{}` when none are in range). The CLI
> `metrics show` accepts `--fetch` to do both in one step. Logical-partition
> processed and aggregated metrics require the owning managed system through
> `system_name_or_uuid` (MCP) or `--system` (CLI); their endpoint is nested
> below that system. Long-term
> monitoring + aggregation must be enabled via `hmc_set_pcm_preferences`
> before processed/aggregated metrics accumulate. Preferences and raw Long
> Term Monitor feeds are documented only for `ManagedSystem`, not
> `LogicalPartition`.

`hmc_update_firmware` accepts IBM's nested `PlatformUpdateParameter` JSON.
It rejects older HMC releases before submitting the destructive operation.
For example, a system-firmware update is:

```json
{
  "system_name_or_uuid": "system1",
  "platform_update": {
    "SystemFirmwareUpdate": {
      "UpdateType": "Update",
      "UpdateOrder": 1
    }
  }
}
```

`hmc_vios_update` uses IBM's operation-specific parameter names. Every source
requires `ResourceType`. Updates accept `HMC`, `NFS`, `SFTP`, `USB`, or
`IBMWebsite` and may include `RestartVIOS`; upgrades accept `HMC`, `NFS`,
`SFTP`, or `USB` and require `Disks`. For example:

HMC sources require `Name`, NFS/SFTP sources require `ServerHostOrIP` and
`RemoteDirectory`, USB sources require `USBDevice`, and every upgrade requires
`Disks`. Setting `SaveFile` to `true` also requires `Name`.

```json
{
  "vios_name_or_uuid": "vios1",
  "kind": "update",
  "repository": {
    "ResourceType": "NFS",
    "ServerHostOrIP": "repo.example.com",
    "RemoteDirectory": "/images/vios",
    "Name": "update.iso",
    "RestartVIOS": "false"
  }
}
```

```json
{
  "vios_name_or_uuid": "vios1",
  "kind": "upgrade",
  "repository": {
    "ResourceType": "HMC",
    "Name": "install.iso",
    "Disks": "hdisk1"
  }
}
```

With `wait=true`, a terminal job's documented `stdOut` result is also exposed
at the top level when present. Invalid or cross-operation parameters are
rejected before connecting to the HMC.

Normalized PCIe inventories share this envelope:

- `resource_kind`: `dedicated_slot`, `sriov_adapter`, `sriov_physical_port`, or
  `sriov_logical_port`;
- `capability`: `available` or `capability-unavailable`;
- `system`: the resolved managed-system name;
- `selector`: nullable `adapter_id`, `physical_port_id`, and `logical_port_id` values copied from
  the request;
- `items`: stable records for the selected resource kind; and
- `unavailable_reason`: `null` when available, otherwise a stable evidence-bound explanation.

Dedicated-slot identity is `(system, drc_index)`. Its `description` and `owner_lpar` come from the
exact ADR 0053 projection; `availability` remains `null` because an empty owner does not prove that
a slot is assignable. SR-IOV identities are `(system, adapter_id)`, adapter identity plus
`physical_port_id`, and adapter identity plus `logical_port_id`. Their mode, availability,
ownership/use, location, capacity, and compatibility category fields are explicit nullable values.
ADR 0053 admits selectors and percentage capacity semantics but no SR-IOV read projection, so these
three collections currently return `capability-unavailable` with no items and perform no inventory
command. Percentage fields use decimal percentages, never bytes, bandwidth, or integer weights.

The CLI equivalents are `hmc-mcp network list-dedicated-pcie-slots`, `list-sriov-adapters`,
`list-sriov-physical-ports`, and `list-sriov-logical-ports`. All accept `--json`; the SR-IOV
commands accept the applicable `--adapter-id`, `--physical-port-id`, and `--logical-port-id`
selectors. The older `hmc_list_io_slots` / `network list-io-slots` surface remains raw and is not
the normalized contract.

### End-to-end: give an LPAR a bootable disk

```bash
# 1. create the partition
hmc-mcp lpars create web01 --system <sys-uuid> --mem 8192 --vcpus 2

# 2. give it a vSCSI adapter paired to the VIOS (find IDs via `vios list`)
hmc-mcp adapters add-vscsi web01 --vios-id 1 --vios-slot 5

# 3. carve a virtual disk out of a VIOS volume group
hmc-mcp storage list-vgs <vios-uuid>                       # find the VG + free space
hmc-mcp storage create-disk <vios-uuid> --vg <vg-uuid> --name web01_root --capacity-mib 51200

# 4. map the disk to the partition
hmc-mcp storage map <vios-uuid> --lpar web01 --disk web01_root

# 5. (optionally) network + power on
hmc-mcp adapters add-network web01 --vlan 100
hmc-mcp lpars power-on web01
```

> **Note on the storage model**: an LPAR's vSCSI/vFC *adapter* (added with
> `adapters add-vscsi` / `add-vfc`) is just plumbing — it pairs the partition
> with a VIOS server slot. The actual *disk* lives on the VIOS or in a Shared
> Storage Pool: carve it out of a Volume Group (`storage create-disk`) or a
> Cluster/SSP (`cluster create-lu`), then connect it with a mapping
> (`storage map`). Both the VIOS **Volume Group / Virtual Disk** model and the
> **Cluster / SSP Logical Unit** model are wrapped. Once a disk is mapped,
> partitioning it into filesystems is the guest OS's job (NIM, cloud-init,
> `mkfs`), not the HMC's.

### Use with Hermes Agent

```bash
hermes mcp add hmc -- uv run --directory ~/src/hmc-mcp hmc-mcp serve \
  --access-policy legacy-equivalent
```

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

Verifies the server actually speaks MCP over stdio. It connects with a real
FastMCP client and reports the exposed tool count:

```bash
just smoke
# Connected. <N> tools exposed.
```

Use `just smoke-verbose` to list every exposed tool when diagnosing the registry.

### 3. Live check against a real HMC

With credentials configured (TOML profile, env vars, or flags), the cheapest end-to-end check
is `console info` — one Logon + one ManagementConsole GET:

```bash
hmc-mcp console info
hmc-mcp systems list      # should print a table of your Power servers
```

If `console info` prints the HMC version, auth, TLS and the session
lifecycle all work; everything else uses the same path.

## Layout

```
src/hmc_mcp/
  __init__.py    # package version and the `hmc-mcp` console-script entry point
  api.py         # supported reusable-library facade (ADR 0029)
  config.py      # pydantic-settings config (TOML profile + env vars + CLI flags)
  xmlutil.py     # defusedxml Atom-feed -> dict parsing
  errors.py      # HMCError (shared by client and its mixins)
  error_translation.py       # presentation-neutral wording for identified HMC failures
  client.py      # async HMCClient: session, transport, uom helpers, jobs
  client_*.py    # per-domain mixins (users, systems, lpars, storage, pcm, ...)
  client_parse.py# defusedxml wrappers tagging failures with the HMC call
  common.py      # shared HMCClient/config helpers for tool definitions
  operations_*.py# workflows and policies shared by MCP and CLI presentations
  affinity_assessment.py     # evidence-first, read-only LPAR NUMA-affinity assessment
  snapshot.py    # version-1 portable LPAR snapshot values and local I/O
  ssh.py         # transport-only asyncssh session and command execution
  ssh_commands.py# resource operations implemented with the HMC CLI
  ssh_selectors.py           # public resource selectors for the HMC SSH commands
  console_capture.py         # bounded, non-interactive LPAR console capture (mkvterm)
  documents.py   # XML request-document builders (LPAR, adapters, storage, users, ...)
  jobs.py        # JobRequest XML templates (PowerOn/PowerOff/...)
  pcm.py         # PCM metrics/preferences parsing + XML documents
  access_policy.py           # server access policy: TOML loading, validation, compilation
  legacy_policy.py           # the legacy-equivalent access policy, built and compiled
  dispatch_scope.py          # the dispatch-boundary authorization decision
  target_scope.py            # dispatch-time authorization of the targets a call names
  connection_scope.py        # dispatch-time authorization of the connection a call selects
  audit.py       # one audit record per authorization decision: vocabulary, rendering, sink
  tool_registry.py           # local MCP tool collection, each tool carrying ToolSecurity
  _app.py        # shared FastMCP instance, sync-run and SSH helpers, entry points
  server.py      # thin aggregator importing every server_*.py tool module
  server_*.py    # resource-domain @mcp.tool definitions (systems, lpars, VIOS, ...)
  server_lpar_config.py      # SSH-only LPAR configuration handlers
  server_system_resources.py # SSH-only managed-system resource handlers
  cli.py         # thin aggregator importing every cli_*.py command module
  cli_app.py     # root Typer app, GlobalOpts/GLOBALS, shared CLI helpers
  cli_*.py       # per-domain CLI commands (systems, lpars, storage, ...)
tests/           # pytest + respx, no real HMC needed
scripts/         # smoke/manual harnesses
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
