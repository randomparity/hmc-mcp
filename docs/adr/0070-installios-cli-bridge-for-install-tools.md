# ADR 0070: Bridge hmc_install_lpar_os / hmc_install_vios to the HMC CLI installios Command

## Status

Accepted

## Context

ADR 0069 (with #381's live-HMC survey) established that the `InstallLPAR` and
`InstallVIOS` REST jobs do not exist on any surveyed HMC — absent from the
Power10 and Power11 REST API documentation sets and rejected with
`REST0006 No such Operation` by live PUT probes on HMC V10R3 M1060 and V11R2
M1120 across Power9/Power10/Power11 systems. The two tools that targeted those
endpoints were therefore phantom tools. Issue #410 asked for a disposition, and
the operator chose route 3: keep both tools as CLI-bridge tools over SSH
`installios`, with explicit caveats.

`installios` is documented in the HMC manual pages (`installios(1)`,
IBM Power10 HMC doc set 5765-VHP, topic `commands-installios`, page dated May
2022; retrieved from ibm.com/docs on 2026-08-22). Its synopsis:

    installios -p partition -i ipaddr -S subnet-mask -g gateway -d path
                -s system-name [-m mac-address] -r profile
                [-V vlan_tag] [-Y vlan_priority] [ ... other optional flags ]
              | -u | -q | [-F] -e -R label

Relevant documented facts:

- With all flags supplied it runs non-interactively; with none it invokes an
  interactive wizard.
- `-p`: the target partition "must be of type Virtual I/O Server" —
  `installios` is the *Virtual I/O Server* installer.
- `-i/-S/-g`: the client's IP/subnet mask/gateway for the install-time network
  interface. There is **no NIM-server address flag**: the HMC itself rips the
  image named by `-d` into nimol resources and serves the install. `-d`
  accepts `/dev/cdrom`, an `lsmediadev` USB device, an absolute HMC path to a
  `backupios` tarball or VIOS ISO, or `nfs_server:/remote/path`.
- `-V` VLAN tag: 0–4094; `-m` MAC address of the client interface; `-r`
  partition profile; `-s` managed-system name.
- `-u` unconfigures leftover NIM resources after a failed run; `-q` lists labels.
- Requires hmcsuperadmin-level authority (hscroot-class login).

## Decision

**Both tools are rebuilt as SSH bridges to `installios`.**

### Grammar mapping and parameter changes

| Tool parameter | installios flag | Change vs REST era |
|---|---|---|
| `lpar_ip` / `vios_ip` | `-i` | name kept, semantics unchanged |
| `nim_subnetmask` | `-S` | name kept; configures the *client* interface |
| `nim_gateway` | `-g` | name kept; client-side gateway |
| `vlan_id` | `-V` | name kept, semantics unchanged ("0" = untagged) |
| — removed `nim_ip` | none | **removed**: no external NIM server exists under CLI semantics |
| new `install_source` | `-d` | device path, absolute HMC path, or NFS `server:/path` |
| new `profile_name` (default `"default"`) | `-r` | required by the engine; co-management mode omits profiles |
| new `mac_address` (optional) | `-m` | avoids MAC discovery, which can time out |
| `system_name_or_uuid` now required | `-s` | the engine needs an explicit managed system |
| removed: `wait`, `wait_timeout_seconds`, `poll_interval`, `hmc_timeout_minutes` | — | there is no job object to poll |

Tool names, operation ids (`vios.install`, `lpar.install_os`), effects, and
target selectors are unchanged.

For `hmc_install_lpar_os` the honest scope is stated in its docstring: the
engine installs VIOS images (the man page restricts `-p` to Virtual I/O Server
partitions). General AIX/Linux NIM installs remain a NIM-master workflow that
the HMC alone cannot drive (ADR 0069).

### Submission semantics: submit-and-detach

A NIM install runs far longer than any sane `ssh_timeout`, so one SSH exec
cannot promise completion. Running `installios` synchronously would also be
dangerous: the transport's timeout closes the SSH channel and the HMC would
SIGHUP the installer mid-write. The wrapper therefore submits detached:

    nohup installios <flags> </dev/null >LOG 2>&1 & echo HMC_MCP_INSTALLIOS_PID=$!

and returns `{system, partition, pid, log_path, message}` as soon as the shell
echoes the backgrounded PID (bounded by `config.ssh_timeout`). stdin is closed
so any unexpected interactive prompt fails fast instead of hanging the
submission. Consequences stated plainly in both docstrings:

- The tool cannot report progress or outcome; monitor via the log file or the
  partition console, then confirm with the partition state tools.
- **There is no HMC job on this path**: `hmc_get_job` / `hmc_wait_for_job` do
  not apply.
- A failed install leaves NIM resources configured; cleanup is `installios -u`
  in an interactive session (documented, deliberately not automated here).

The log path is deterministic per partition (`/tmp/hmc-mcp-installios-<name>.log`)
so operators can find it without parsing tool output.

### Injection validation

Following the established validator patterns (`validate_caller_token`,
`validate_agent_id`): every interpolated value passes a named validator before
composition, and every value is additionally `shlex.quote`d — quoting remains
the actual injection boundary, validators give fast named rejections and pin
semantics:

- `validate_ipv4_address` — dotted quad, octets 0–255 (`-i`, `-g`).
- `validate_ipv4_subnet_mask` — IPv4 plus contiguity check (`-S`).
- `validate_vlan_id` — integer string 0–4094 (`-V`).
- `validate_mac_address` — six colon-separated hex octets (`-m`).
- `validate_install_source` — non-empty, printable, no leading `-` (flag-like),
  NFS form's server part must be hostname-shaped (`-d`).
- `validate_hmc_name` — non-empty printable text for system/partition/profile
  names (HMC names are free-form; anything legitimate survives quoting).
- `build_installios_command` re-validates everything at composition time: it,
  not the tool layer, is the trust boundary.
- Partition UUIDs are resolved to CLI names over SSH (`_ssh_lpar_name`) because
  whether `installios -p` accepts UUIDs is unverified; names are always what is
  sent.

`parse_installios_pid` raises `HMCCLIError` when the submission output carries
no PID tag; transport failures surface as `HMCCLIError` per existing
conventions.

### Removed code

Clean cutover: `install_lpar_job`, `install_vios_job`, and
`install_wait_timeout_seconds` are deleted from `jobs.py` together with their
tests and the `/do/InstallLPAR` + `/do/InstallVIOS` fixtures. No shims.

## Assumptions and unverified behaviors

Verified against repo sources only (the man page above, ADR 0069, #381's
survey). **No live-HMC verification was possible**; the following remain
unverified against real hardware and should be confirmed in the next daytime
live-HMC window:

1. That a fully-flagged `installios` invocation never prompts when stdin is at
   EOF (the man page implies it; wizard mode is only documented for the
   no-flags case). Any hidden prompt fails fast instead of hanging, but the
   submission would still report success while the detached process dies in
   the log.
2. That `nohup … & echo $!` behaves under the HMC's restricted SSH shell as on
   a standard Linux shell (PID echo, process survival after disconnect).
3. Actual long-run behavior: that the detached installer survives HMC session
   teardown, and how long a real rip-and-install takes per firmware level.
4. Whether `installios -p` accepts a partition UUID (we assume not and always
   send names).
5. Whether any HMC release has widened `installios` beyond VIOS-type targets;
   if so, `hmc_install_lpar_os`'s scope note can be revisited.
6. Exact exit-status semantics recorded in the log file (the tool does not
   parse them).

## Consequences

- The phantom REST calls are gone; every surveyed HMC can actually accept the
  new submissions (the CLI exists where the REST jobs do not).
- Callers lose the wait/poll ergonomics of the old (never-functional) path;
  monitoring moves to the log/console. This is a behavioral break and is
  recorded under Changed in CHANGELOG.md.
- The facade manifest (`hmc_mcp.api`) is unaffected: tool signatures changed
  but no export was added, removed, or renamed.
- `_PAYLOAD_SOURCE_ARGUMENTS` swaps `nim_ip` out and `install_source` in, with
  the reason recorded beside the set per the G15 guardrail.

## Considered & rejected

- **Remove both tools entirely (route 1).** judgment: defensible, but the
  operator chose to preserve the capability surface; `installios` is real,
  reachable functionality the package can honestly expose.
- **Bridge via `PowerOn` job with `OperationType=netboot`.** judgment: that
  kicks a network boot but does not drive the NIM handshake (ADR 0069); it
  would replace one half-working tool with another.
- **Run `installios` synchronously with a large timeout.** judgment: bounded
  observation kills the install on timeout (channel close → SIGHUP mid-write)
  and still cannot bound a multi-hour install honestly. Detach-and-report is
  the only safe contract one SSH exec can keep.
- **Automate `installios -u` cleanup after detected failures.** judgment:
  detecting failure requires monitoring we deliberately do not have; running
  destructive cleanup blind against a possibly-running installer would be worse
  than documenting the manual step.
