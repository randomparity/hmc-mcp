# `backup_name` and the `UNBOUNDED_ARGUMENTS` line

Issue: [#264](https://github.com/randomparity/hmc-mcp/issues/264)
Decision record: [ADR 0044](../../adr/0044-backup-name-is-bounded-by-its-vios.md)
Governing record: [ADR 0039](../../adr/0039-dispatch-time-target-scope.md)

## Goal

Make the rule that decides `exhaustive_targets` say what is actually being
applied, and classify `backup_name` under it — so the guardrail comment and the
code can be read together without inferring an unstated rule, and so neither can
drift from the other unnoticed.

## Context

`UNBOUNDED_ARGUMENTS` (`src/hmc_mcp/tool_registry.py:96`) holds the public
argument names that carry an identity no `targets` allowlist can pin down. A tool
accepting one cannot declare `exhaustive_targets=True`, so only the `all-targets`
sentinel grants it.

The guardrail comment in `tests/app/test_tool_security.py` states the line as a
question of **which side** the named thing lives on: a file on the HMC's own
filesystem is something "the policy is meant to bound and cannot". Applied
literally, `backup_name` on `hmc_restore_vios` — a backup file on the HMC — would
have to join the set. It has not, and `hmc_restore_vios` resolves
`exhaustive_targets=True`.

The issue proposes a write-versus-read reading as the operative distinction. It
does not hold: `hmc_restore_lpar_profiles` only *reads* its `file_path` and is
declared `exhaustive_targets=False` all the same
(`src/hmc_mcp/server_profiles.py:75-83`). Whatever separates the two arguments,
it is not that one is written and the other read.

## Requirements

- **R1** The rule text states the distinction actually applied, and that
  distinction is ADR 0039's containment principle
  (`docs/adr/0039-dispatch-time-target-scope.md:379-383`).
- **R2** `backup_name`'s classification agrees with the rule text, and the reason
  is recorded where a future classification is decided against it.
- **R3** No claim about HMC-side `chviosbackup` behaviour that this repository
  cannot verify is asserted as fact. Where containment depends on such a claim,
  the server enforces the containment itself.
- **R4** A test pins the classification together with whatever makes it true, so
  removing one fails on the other.
- **R5** No legitimate backup name that `hmc_list_vios_backups` can return is
  refused.

## Decision

Correct the rule text; leave `backup_name` bounded; add the guard that makes
"bounded" a fact about this code rather than an assumption about the HMC.

`file_path` is unbounded because `bkprofdata -f` and `rstprofdata -f` take an
absolute path anywhere on the HMC console filesystem, and the declared `-m`
system constrains that path not at all — the same string names the same console
file whichever system is passed. The console object is neither the value of a
declared selector nor reached from one. That holds for the writing tool and the
reading tool alike, which is why both are non-exhaustive.

`backup_name` is bounded because `chviosbackup -id <vios_uuid> -operation restore
-file <backup_name>` addresses the per-VIOS backup catalog that `-id` names. IBM
documents the store as `/data/viosbackup/<MTMS>/<VIOS-UUID>/<name>` and documents
a backup name as 1–40 characters drawn from `A-Z a-z 0-9 . - _`. A bare name is
therefore an entry *inside* the container the declared selector names — ADR
0039's "derived by the server through the HMC's own containment from" a declared
selector. A different `-id` reaches a different catalog.

**What that argument rests on, and what it does not.** IBM publishes the storage
layout and the name grammar; the grammar is stated for the HMC's own backup
management UI. This checkout found no published `chviosbackup` reference stating
that the CLI refuses a path-shaped `-file` value, and nothing here can test an
HMC. Recording the containment as an HMC-side fact would be the substitution ADR
0039 names as its own recurring error — an assumption about a system we cannot
observe, written in the grammar of a fact about code we can. So the server
enforces it: `hmc_restore_vios` refuses a `backup_name` that could leave the
catalog directory, before the command is built.

The refusal is the narrow one, following ADR 0039's own precedent for `job_href`
— which refused dot-segments and explicitly declined to require a UUID shape,
because refusing a legitimate identifier would trade a regression for reach that
the narrow refusal had already removed. Here the narrow refusal is: a name that
is empty or whitespace-only, contains `/` or `\`, or is `.` or `..`. Every name
IBM's documented grammar admits passes it, so R5 holds by construction.

Percent-decoding has no analogue here and is deliberately absent: this value
reaches an SSH command line, not a URL, and no component between the tool and the
HMC CLI percent-decodes it.

## Non-goals

- Re-opening `file_path`'s classification. ADR 0036 placed it outside every grant
  and ADR 0039 kept it there; this change explains that placement, it does not
  revisit it.
- Constraining *what a granted VIOS reads*, beyond keeping the read inside the
  declared VIOS's own catalog. Ingress control is a different dimension ADR 0039
  states it does not offer.
- Any change to `hmc_backup_vios`, which takes no name argument.

## Threat model

**Boundary inventory.** One existing boundary is narrowed, none added: the
`backup_name` argument of `hmc_restore_vios`, which crosses from an MCP caller
into an HMC CLI command string built by `_run_vios_backup_command` and executed
over SSH. No new entry point, no new grant, no dependency change.

**Actor model.** The untrusted party is an MCP client authorized for
`hmc_restore_vios` under a `targets` table naming some VIOS. Trust is placed in
the access policy to have bound the `vios_name_or_uuid` selector, and in the HMC
to execute the command as the configured SSH user. The client is *not* trusted to
supply a `backup_name` that stays inside the catalog — that is what the new guard
stops assuming.

**Control per boundary.** `shlex.quote` already governs shell metacharacters and
stays (this change does not replace it — a name refused for containment and a
name quoted for the shell are different failures). The new control is the
containment refusal above, raising `ValueError` with the offending constraint
named and the value not echoed back into any wider context than the caller's own
error. `vios_name_or_uuid` is authorized by the access policy before the handler
runs, unchanged.

**Explicitly out of scope.** Whether a caller granted one VIOS should be able to
read *any* backup of that VIOS is not addressed — every backup in the catalog
belongs to the declared VIOS, so it is inside the grant by construction. Whether
the HMC itself imposes further validation is unknown and deliberately not relied
on. Local-file disclosure through a *different* argument is covered elsewhere:
`iso_source` is issue #261.

## Changes

| File | Change |
|---|---|
| `docs/adr/0044-backup-name-is-bounded-by-its-vios.md` | New decision record. |
| `src/hmc_mcp/server_vios.py` | Containment guard on `backup_name` in `hmc_restore_vios`; docstring states the refusal. |
| `src/hmc_mcp/tool_registry.py` | `UNBOUNDED_ARGUMENTS` comment states the containment question and why `backup_name` is not a member. |
| `tests/app/test_tool_security.py` | Guardrail comment rewritten to the containment rule; new test pinning the classification to the guard. |
| `tests/vios/test_vios_backup.py` | A legitimate name still reaches the command unchanged. |

`UNBOUNDED_ARGUMENTS` itself is unchanged: `backup_name` does not join it.

## Testing

- The classification pin: `hmc_restore_vios.exhaustive_targets` is true,
  `backup_name` is absent from `UNBOUNDED_ARGUMENTS`, **and** each escape shape is
  refused — in one test, so deleting the guard reddens the classification
  assertion's own test and forces the classification to be re-argued (R4).
- Each refused shape is exercised: empty, whitespace-only, `/`-bearing,
  `\`-bearing, `.`, `..` (R3).
- A name matching IBM's documented grammar reaches the command unchanged,
  proving the guard does not refuse what the catalog can hold (R5).
- The existing `test_the_declared_set_is_exactly_what_the_check_finds` continues
  to pass with `hmc_restore_vios` absent from its expected mapping — the
  independent check that the declaration and the derivation still agree.
