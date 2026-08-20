# 0044 — `backup_name` is bounded by containment, and the server is what holds it there

## Status

Accepted (2026-08-19)

## Context

`UNBOUNDED_ARGUMENTS`, in `src/hmc_mcp/tool_registry.py`, lists public argument
names a `targets` allowlist cannot bound. A tool accepting one cannot declare
`exhaustive_targets=True`, so only `all-targets` grants it.

The guardrail comment carrying the rule, above `_PAYLOAD_SOURCE_ARGUMENTS` in
`tests/app/test_tool_security.py`, said:

> The line against UNBOUNDED_ARGUMENTS is *which side* the named thing lives on.
> `file_path` is a file on the **HMC's own** filesystem, so the policy is meant
> to bound it and cannot — that makes its tool unbounded.

`hmc_restore_vios` takes `backup_name`, also a file on the HMC, and is bounded.
Read literally the rule and its application disagree, and that sentence is what
the next classification is decided against (#264).

Which side the value lives on is neither necessary nor sufficient, and the list's
own members show it. Two of the four are unbounded for a reason unrelated to any
filesystem: `cmd` is free-form command text and `vios_partition_id` is a slot
number reused across every system in a fleet, so *no allowlist entry can be
written* that names one VIOS. `file_path` and `job_href` fail differently — an
entry can be written, but the value designates something the declared selectors do
not contain. So a name is unbounded when a `targets` table cannot bound it, by
either route: the identity cannot be written down, or it can be written and is not
contained.

For an HMC-side file the first route does not apply, so the question reduces to
containment — which is what ADR 0039 already requires of every exhaustive tool:
every resource acted on must be "either the value of a declared selector or
derived by the server through the HMC's own containment from one"
(`docs/adr/0039-dispatch-time-target-scope.md:379-383`).

#264 proposes instead that the distinction is write-versus-read — of the *file*,
not of the target. It is not that:
`hmc_restore_lpar_profiles` only reads its `file_path` and is non-exhaustive
(`server_profiles.hmc_restore_lpar_profiles`). Containment explains that directly —
`rstprofdata -f` takes an absolute path anywhere on the console filesystem, and
the declared `-m` system does not constrain it, so the same string names the same
console file whichever system is passed. Reading an uncontained resource is still
reaching it; two of ADR 0039's five exhaustive composites only read children of
their declared target and were argued through containment for that reason.

`chviosbackup -id <vios_uuid> -operation restore -file <backup_name>` is the
other case: the value names an entry in the backup catalog that `-id` selects.
That is containment — provided `-file` is a name resolved in that catalog rather
than a path that can leave it, which nothing in this checkout can establish.

## Decision

Membership in `UNBOUNDED_ARGUMENTS` turns on whether a `targets` table can bound
the identity — by either route above — not on which filesystem the value refers
to and not on whether it is written.

`backup_name` stays out of the list and `hmc_restore_vios` keeps
`exhaustive_targets=True`, and the server closes the gap that classification
would otherwise leave open. `server_vios._validate_backup_name` refuses four
shapes before the command is built. The claim is not that no catalog could hold
such a name — it is that the tool cannot treat any of them as one, because each
can denote something else:

- empty, or differing from its own stripped form — a padded `" .. "` would
  otherwise slip a dot-segment past a naive check;
- containing `/` or `\`;
- consisting only of dots;
- starting with `-`. This one is refused for what is *unknown* about it: how the
  HMC CLI parses a bare leading dash in this position is not established here, and
  `shlex.quote` offers no cover, since such a value carries no shell metacharacter
  and is emitted unquoted.

What survives is a bare name, which can denote only an entry in the catalog
`-id` selects.

**What this still rests on, stated rather than buried.** That `-id` scopes the
operation, and that the call this reasons about is the one the repository builds
(#289 asks whether `chviosbackup` is even the HMC's command name). Neither is
verifiable here, and the first is not eliminable: it is the same premise that
makes `vios_name_or_uuid` a target selector at all, so a record refusing it would
be refusing the tool's existing classification rather than this one. The second
changes the spelling of the call, not its shape — a VIOS selector plus a backup
name — which is what the classification turns on. What the guard removes is the
*additional* premise the first draft needed, that the HMC would itself reject a
path-shaped value. #283 owns confirming these against a real HMC.

The refusal is narrow by choice: those four shapes and no character-set or length
rule. ADR 0039 made the same call for its dot-segment guard on caller-supplied
request paths — the guard that closes the `job_href` variant — refusing
dot-segments while declining to require a UUID shape, because refusing a
legitimate identifier trades a regression for reach the narrow refusal has
already removed. A catalog entry
named outside whatever grammar the HMC enforces stays restorable here.

`shlex.quote` stays and is unrelated — it governs shell metacharacters, a
different failure from leaving the catalog. No percent-decoded arm accompanies the
refusal: ADR 0039 needed one because a URL path reaches an HTTP router that may
decode before routing, and this value reaches an SSH command line that nothing in
between decodes.

`hmc_restore_vios` declares `exhaustive_targets` by omission — `tool_module`'s
`tool()` decorator defaults it to `True` — so nothing at the tool shows a
classification was made. The guardrail comment is therefore rewritten to state the
rule above, and a test asserts the declaration and the guard together, so removing
either fails on the other.

## Consequences

- One rule decides the next classification and it covers all four current
  members, where the side-of-the-filesystem proxy covered two.
  `hmc_restore_lpar_profiles` — which only reads its `file_path`, though its
  effect on the system's profiles is `destructive` — stops looking like a
  contradiction.
- A `targets` table can still grant `hmc_restore_vios`. The tool is `destructive`
  and its declared effect lands on the declared VIOS, so classifying it unbounded
  would force the strictly wider `all-targets` grant to reach a strictly narrower
  operation.
- A caller can no longer pass a `backup_name` of any of those four shapes. If
  some HMC does hold a catalog entry named that way, restoring it needs the HMC
  CLI directly; #283 owns finding out whether that is reachable.
  `tests/unit/test_ssh_quoting.py::test_restore_vios_quotes_hostile_backup_name`
  moves from `/backups/vios;id` to `vios;id`, so the quoting property stays
  proven independently of containment.
- The guard is invisible to `_unbounded_identities`, the mechanical check that
  catches the next unbounded argument: that check matches argument *names*, and a
  name bounded by a per-tool guard looks identical to one that needs no guard.
  The pairing is held by a test asserting the classification and the refusal
  together instead, and by the rule text now naming `backup_name` explicitly —
  which is itself checked, since the guardrail comment is what drifted last time.
- A refused `backup_name` is *not* invisible to the audit layer. `authorized()`
  runs the dispatch authorizer before the handler, in `tool_registry.authorized`,
  so the authorization decision for `vios.restore` against the declared VIOS is
  recorded and only then does the refusal fire. What that record does not carry is
  the refusal itself or the rejected value, so the stream shows an authorized
  dispatch and nothing distinguishing it from one that ran. Making a rejected
  argument legible in the audit stream is a broader change than this record owns.
- The command this reasons about is the one this repository builds. Whether
  `chviosbackup` is the HMC's actual command name is a separate open question
  (#289); the classification turns on the call's shape — a VIOS selector plus a
  backup name — which that question does not change.
- Two open findings would reverse parts of this, and neither is speculative. If
  #282 establishes that restoring an `ssp`-type entry reconfigures the cluster,
  `hmc_restore_vios`'s own declaration is wrong and flips to `False` — a per-tool
  change leaving `backup_name` where this record puts it. If #283 establishes that
  the HMC resolves even a bare `-file` outside the selected catalog, the guard is
  insufficient and this membership decision reopens.

## Considered & rejected

- **Do nothing.** Not exploitable — `shlex.quote` blocks injection and the restore
  lands on the declared VIOS. Rejected because the rule text is what the next
  classification is decided against, and one that already answers wrongly for a
  name in the tree spends that ambiguity later, at a worse moment.
- **Add `backup_name` to `UNBOUNDED_ARGUMENTS`** and declare `hmc_restore_vios`
  non-exhaustive. The fail-closed-looking option, needing no guard. Rejected
  because containment holds once the guard does, and the cost is real: operators
  would need `all-targets` — every VIOS on every system — to permit one declared
  restore.
- **Narrow the rule to "a file the operation writes on the HMC".** #264's own
  proposal. Rejected on the evidence: `hmc_restore_lpar_profiles` writes no HMC
  file and is non-exhaustive, so the narrowed rule contradicts a live declaration
  the moment it is written down.
- **Classify `backup_name` as a payload source**, beside `repository` and
  `iso_source`, on the ground that the restore only reads it. Rejected because
  that exemption's stated reason is "These are not HMC resources"
  (`docs/adr/0039-dispatch-time-target-scope.md:366-373`) and a VIOS backup is
  one. Dropping the conjunct would loosen the exemption for every future name — a
  wider change than this record is entitled to make.
- **Verify the name against the catalog** — run `lsviosbackup -id <uuid>` first,
  which this repository already does and parses in `hmc_list_vios_backups`, and
  refuse a `backup_name` the listing does not contain. Rejected, and not because the premise
  makes it unnecessary — that would be circular. It is rejected because it does
  not establish the premise either: a name present in the listing is still
  resolved by `-file` however the HMC resolves it, so a listing check would be
  evidence about the catalog's *contents*, never about where `-file` looks. Only
  a real HMC settles that, which is #283. What the round-trip does buy is a
  narrower window if the premise turns out false — worth weighing there, and
  recorded on #283 for that reason, but not worth an SSH call and a TOCTOU gap on
  every restore to hedge a premise the tool's own target selector already rests
  on.
- **Record the containment as an HMC-side fact and change no code.** The cheapest
  option, and the first draft of this record. Rejected because the fact is not
  established (#283) — the substitution ADR 0039 names as its own recurring error,
  an assumption about a system we cannot observe written in the grammar of a fact
  about code we can.
