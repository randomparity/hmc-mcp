# HMC SSH Access Notes

Operational notes for running ad-hoc read-only queries against lab HMCs.
Derived from the live-testing session for issue #559.

## Config file

Profiles are stored in `~/.config/hmc-mcp/config.toml`.  Each profile has a
`host`, `port`, `user`, and `password`.

```toml
[profiles.myhmc]
host = "hmc.example.com"
port = 443          # HTTPS REST — NOT the SSH port
user = "hscroot"
password = "..."
verify_ssl = false
schema_version = "V1_0"
```

## SSH port is always 22

The `port` in `config.toml` is the **HTTPS REST API port** (443 or 12443).
HMC SSH always listens on **port 22**.  Do not use the config-file port for
SSH connections.

```sh
ssh -p 22 hscroot@hmc.example.com
```

## Password authentication with asyncssh

`asyncssh` tries all local SSH keys before attempting password auth.  On a
workstation with several keys loaded, this exhausts the HMC's
`MaxAuthTries` limit and the connection is dropped with
`Too many authentication failures` before the password is ever sent.

Fix: disable public-key auth and force password-only in the connect call.

```python
asyncssh.connect(
    host=host, port=22,
    username=user, password=password,
    known_hosts=None,       # HMC has no entry in known_hosts
    preferred_auth="password",
    client_keys=[],         # suppress all key attempts
)
```

## Account lockout after MaxAuthTries

HMC enforces a hard lockout after 3 consecutive failed login attempts.  Once
triggered, **every subsequent connection is rejected immediately** with:

```
The account is locked due to 3 failed logins.
(5 minutes left to unlock)
```

The 5-minute cooldown is per violation; a second failed attempt while locked
resets the timer.  This means:

- **Do not run multiple sequential `ssh` / `sshpass` commands against the same
  HMC in a tight loop** (e.g. iterating over managed-system names) — the key
  negotiation overhead counts as attempts even when authentication succeeds on
  the first try if another process is simultaneously probing.
- **`asyncssh` without `client_keys=[]` and `preferred_auth="password"` will
  lock the account** — each key loaded in the agent counts as a separate failed
  attempt before the password is ever tried.  See [the fix above](#password-authentication-with-asyncssh).
- If you accidentally trigger a lockout, wait the full 5 minutes before
  retrying.  Retrying early resets the timer and extends the lockout.
- The `hmc-mcp` asyncssh transport (`src/hmc_mcp/ssh/transport.py`) applies
  `client_keys=[]` and `preferred_auth="password"` automatically in
  password-auth mode; ad-hoc scripts must apply the same pattern manually.

## Password authentication with openssh / sshpass

`ssh` from macOS OpenSSH 10.x is interactive by default.  Use `sshpass` to
supply the password non-interactively.  Also disable pubkey auth to avoid the
same `MaxAuthTries` problem:

```sh
sshpass -p 'PASSWORD' ssh \
  -o StrictHostKeyChecking=no \
  -o PubkeyAuthentication=no \
  -o IdentitiesOnly=yes \
  -p 22 hscroot@HOST 'lshmc -V'
```

`IdentitiesOnly=yes` is belt-and-suspenders: `PubkeyAuthentication=no`
suppresses public-key auth, but some OpenSSH builds still offer agent keys
during the initial handshake.  Both together guarantee no key is presented.

## HMC SSH closes connections on port 443/12443

Port 443 and 12443 are HTTPS-only on HMC.  An SSH connection attempt to
those ports is rejected at the protocol identification stage
(`kex_exchange_identification: Connection closed by remote host`) before
any key exchange occurs.  This looks identical to a firewall block; the fix
is simply to use port 22.

## Managed-system field names

`lssyscfg -r sys` uses `name` (not `SystemName`) for the system name field.
The correct read-only inventory command is:

```sh
lssyscfg -r sys -F name,type_model,serial_num,state
```

## lslabelvios requires -m

`lslabelvios` has no global form.  `-m <system_name>` is mandatory; omitting
it exits 1 with:

```
The command entered is either missing a required parameter or a parameter
value is invalid.  The parameters that are missing or have an invalid value
are -m.
```

Enumerate managed systems first with `lssyscfg -r sys`, then loop per system.

## Non-Operating systems return HSCL0238

Running `lslabelvios` against a system in `Failed Authentication`,
`No Connection`, or any non-Operating state exits 1 with:

```
HSCL0238 This operation is only allowed when the managed system is in
the Operating state.
```

Filter systems to `state=Operating` before querying `lslabelvios`.

## Empty-result output

When a command returns no rows it exits **0** and prints the literal string:

```
No results were found.
```

This is not an empty string and not an error.  Both `-F --header` and the
default `name=value` format produce this same sentinel when there are no
matching resources.

## Two output formats for lslabelvios

**`-F --header`** — CSV with a header row:

```
name,lpar_id,port_name,port_phys_loc,port_label
vios1,100,fcs0,U9999.XXX.XXXXXXX-P0-C0-T0,
```

**Default (no `-F`)** — `name=value` comma-separated pairs, one row per line:

```
name=vios1,lpar_id=100,port_name=fcs0,port_phys_loc=U9999.XXX.XXXXXXX-P0-C0-T0,port_label=
```

Both forms carry the same five fields.  The `port_label` field is present but
empty when no label has been applied.

## Probe script

[`scripts/probe_labelvios.py`](scripts/probe_labelvios.py) is a reusable
asyncssh-based probe template that applies all of the above.  It reads
`~/.config/hmc-mcp/config.toml`, connects in parallel to all profiles on
port 22 with password-only auth, and runs a two-stage read-only query:
stage 1 enumerates managed systems, stage 2 runs per-system commands.

```sh
uv run --no-sync python scripts/probe_labelvios.py
```
