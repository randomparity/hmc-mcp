# 0044 — Containment decides `UNBOUNDED_ARGUMENTS`, and the server holds it for `backup_name`

## Status

Accepted (2026-08-19)

## Context

ADR 0039 paired each tool's `exhaustive_targets` declaration with
`UNBOUNDED_ARGUMENTS`, a list of public argument names carrying an identity no
`targets` allowlist can pin down. A tool accepting one cannot be `True`, so only
`all-targets` grants it.

The guardrail comment carrying that rule stated the line as **which side** the
named thing lives on: `file_path` is a file on the HMC's own filesystem, "so the
policy is meant to bound it and cannot". `hmc_restore_vios` takes `backup_name`,
also a file on the HMC, and is bounded. Read literally the rule and its
application disagree — and that sentence is what the next classification is
decided against (#264).

ADR 0039 states the rule that actually decides it: every resource acted on must be
"either the value of a declared selector or derived by the server through the
HMC's own containment from one". Two of its five exhaustive composites —
`hmc_system_summary` and `hmc_lpar_summary` — only *read* children of their
declared target and still had to clear that bar, so a read of an HMC resource is
in the dimension and is bounded by containment, not by being a read. Its separate
payload-source exemption does not reach `backup_name` either: that exemption
leads with "These are not HMC resources", and a VIOS backup is one.

Under containment the two arguments separate cleanly, and #264's proposed
write-versus-read reading falls without needing anything else:

- `bkprofdata -f <file_path>` and `rstprofdata -f <file_path>` take an absolute
  path anywhere on the HMC console filesystem. The declared `-m` system does not
  contain it — the same string names the same console file whichever system is
  passed. That is true of the writing tool and of `hmc_restore_lpar_profiles`,
  which only reads and is non-exhaustive for exactly this reason.
- `chviosbackup -id <vios_uuid> -operation restore -file <backup_name>` addresses
  the backup catalog that `-id` selects, so a name in it is reached through
  containment from the declared VIOS.

The second bullet is where the honesty problem sits, and it is why this is a
record rather than a comment fix. It needs `-file` to be a name resolved inside
that catalog rather than a path that can leave it. Nothing in this checkout can
exercise an HMC, and no published `chviosbackup` reference was found stating what
the CLI does with a path-shaped value. Asserting it would repeat the substitution
ADR 0039 documents itself making three times: an assumption about a system we
cannot observe, written in the grammar of a fact about code we can.

## Decision

Membership in `UNBOUNDED_ARGUMENTS` turns on containment — whether the identity
can address a resource, read or written, that the declared selectors do not
contain. Not which filesystem the name refers to, and not whether the value is
written.

`backup_name` stays out of the list and `hmc_restore_vios` keeps
`exhaustive_targets=True` — and the server makes that true instead of assuming
it. `hmc_restore_vios` refuses a `backup_name` that is empty or whitespace-only,
contains `/` or `\`, or is `.` or `..`, before the command is built. What remains
is a bare name, which can only denote an entry in the catalog `-id` selects.

That leaves exactly one premise: that `-id` scopes the operation. It is the same
premise that makes `vios_name_or_uuid` a target selector at all, so the
classification rests on nothing the tool's existence did not already rest on. The
independent question — whether the HMC *also* refuses such a value, which would
make the guard redundant rather than necessary — is #283's, and the answer cannot
change this classification in either direction.

The refusal is deliberately narrow rather than IBM's documented 1–40-character
backup-name grammar. ADR 0039 made the same call for `job_href`: it refused
dot-segments and declined to require a UUID shape, because refusing a legitimate
identifier trades a regression for reach the narrow refusal has already removed.
A catalog entry an older HMC named outside that grammar stays restorable here.

`shlex.quote` stays and is unrelated — it governs shell metacharacters, a
different failure from leaving the catalog. No percent-decoded arm accompanies the
refusal: ADR 0039 needed one because a URL path reaches an HTTP router that may
decode before routing, and this value reaches an SSH command line that nothing
between here and the HMC CLI percent-decodes.

`hmc_restore_vios` also declares `exhaustive_targets` by omission — `tool()`
defaults it to `True` (`src/hmc_mcp/tool_registry.py:314`). So the guardrail
comment is rewritten to state the containment question, and a test pins the
classification against the guard, since nothing at the tool itself shows a
classification was made.

## Consequences

- One rule decides the next classification, and it is ADR 0039's own. The
  side-of-the-filesystem proxy is gone, and `hmc_restore_lpar_profiles` — a
  read-only tool that is non-exhaustive — stops looking like a contradiction.
- A `targets` table can still grant `hmc_restore_vios`. The tool is `destructive`
  and its declared effect lands on the declared VIOS, so classifying it unbounded
  would force the strictly wider `all-targets` grant to reach a strictly narrower
  operation.
- A caller can no longer pass a separator-bearing `backup_name`. No HMC-created
  name takes that shape under IBM's documented grammar, so this is expected to be
  unreachable; the error names the constraint, and `all-targets` plus the HMC CLI
  remain as a fallback if it is ever reached.
  `tests/unit/test_ssh_quoting.py::test_restore_vios_quotes_hostile_backup_name`
  passes `/backups/vios;id` and moves to a separator-free hostile value, so the
  quoting property stays proven independently of the containment property.
- Two findings would reverse parts of this, and neither is speculative:
  if #282 establishes that restoring an `ssp`-type entry reconfigures the cluster,
  `hmc_restore_vios`'s own declaration is wrong and flips to `False` — a per-tool
  change that leaves `backup_name` where this record puts it. If #283 establishes
  that the HMC resolves even a bare `-file` outside the selected catalog, the
  guard is insufficient and this record's membership decision reopens.
- The containment is now a property of this repository, tested here. Nothing
  downstream has to trust the HMC's own validation for the classification to hold.

## Considered & rejected

- **Do nothing.** The divergence is not exploitable — `shlex.quote` blocks
  injection and the restore lands on the declared VIOS. But the rule text is what
  the next classification is decided against, and a rule that already answers
  wrongly for a name in the tree spends that ambiguity later, at a worse moment.
- **Add `backup_name` to `UNBOUNDED_ARGUMENTS`** and declare `hmc_restore_vios`
  non-exhaustive. The fail-closed-looking option, and it needs no guard. Rejected
  because containment holds once the guard does, and the cost is real: operators
  would need `all-targets` — every VIOS on every system — to permit one declared
  restore.
- **Narrow the rule to "a file the operation writes on the HMC".** #264's own
  proposal. Rejected on the evidence: `hmc_restore_lpar_profiles` writes no HMC
  file and is non-exhaustive, so the narrowed rule contradicts a live declaration
  the moment it is written down.
- **Classify `backup_name` as a payload source**, alongside `repository` and
  `iso_source`, on the ground that the restore only reads it. Rejected because
  ADR 0039's exemption leads with "These are not HMC resources" and a VIOS backup
  is one — and because two of 0039's own exhaustive composites only read HMC
  resources and were still argued through containment. Dropping the conjunct would
  loosen that exemption for every future name, which is a wider change than this
  record is entitled to make.
- **Record the containment as an HMC-side fact and change no code.** The cheapest
  option. Rejected because the fact is not established: the storage layout and the
  name grammar are published, the CLI's treatment of a path-shaped `-file` is not
  (#283). This is the substitution ADR 0039 names as its own recurring error.
- **Validate against IBM's documented 1–40-character grammar** instead of the
  narrow refusal. Stronger than containment needs, and it can refuse a legitimate
  catalog entry the HMC would restore. ADR 0039 rejected the analogous UUID-shape
  validation for `job_href` on the same reasoning.
