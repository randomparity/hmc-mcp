# 0044 — `UNBOUNDED_ARGUMENTS` asks what a call acts on, not which filesystem a name lives on

## Status

Accepted (2026-08-19)

## Context

ADR 0039 paired each tool's `exhaustive_targets` declaration with
`UNBOUNDED_ARGUMENTS`, a list of public argument names carrying an identity no
`targets` allowlist can pin down. A tool accepting one cannot declare
`exhaustive_targets=True`, so only `all-targets` grants it.

The guardrail comment carrying that rule stated the line as **which side** the
named thing lives on: `file_path` is a file on the HMC's own filesystem, "so the
policy is meant to bound it and cannot". `hmc_restore_vios` takes `backup_name`,
also a file on the HMC, and is bounded. Read literally the rule and its
application disagree — and that sentence is what the next classification gets
decided against, which is the cost (#264).

The reconciliation #264 proposes is that `file_path` is written and `backup_name`
only read. It is wrong on the evidence: `hmc_restore_lpar_profiles` only reads its
`file_path` and is declared `exhaustive_targets=False` all the same
(`src/hmc_mcp/server_profiles.py:75-83`).

Two things already recorded settle it between them.

**The list is a per-argument-*name* judgement.** ADR 0039 says so explicitly: the
check "turns a per-tool judgement into a per-*argument-name* judgement, which is a
much smaller thing to get wrong and a much easier thing to review". A name is
therefore classified once, from the strongest identity it carries anywhere, and
every tool accepting it inherits that classification.

**A payload a call reads is outside the target dimension.** ADR 0039 placed
`repository`, the NIM addresses, and `iso_source` outside it deliberately, because
each of those calls "still mutates only the resources its selectors declare", and
constraining where a granted target loads its payload from is ingress control —
a different control this policy does not offer.

Applying both:

- `file_path` earns its place through `hmc_backup_lpar_profiles`, where
  `bkprofdata -f` **creates or overwrites** a console file at a caller-chosen
  absolute path. That file is a resource the call acts on, and the declared `-m`
  system does not contain it: the same string names the same console file
  whichever system is passed. Because the list is per-name,
  `hmc_restore_lpar_profiles` inherits the refusal even though it only reads —
  which is the whole of why the write-versus-read reading looked plausible and is
  not the rule.
- `backup_name` never designates a resource acted on. In
  `chviosbackup -id <vios_uuid> -operation restore -file <backup_name>`, the
  `-file` value is the backup the operation *reads*. It is a payload identity, and
  the decision that put payload identities outside the target dimension already
  covers it.

The payload-source set was described as sources "outside the HMC", which is true
of its members but was never the criterion. `backup_name` is the case that makes
the difference visible: an HMC-side payload identity.

## Decision

`UNBOUNDED_ARGUMENTS` holds a name when the identity it carries can designate a
resource some call **acts on** that the declared selectors do not contain. Not
which filesystem the name refers to, and not whether the value is written.

Under that rule `backup_name` stays out of the list and `hmc_restore_vios` keeps
`exhaustive_targets=True`. It joins `_PAYLOAD_SOURCE_ARGUMENTS` in
`tests/app/test_tool_security.py` instead, whose enumeration test then pins the
pairing: `hmc_restore_vios: (True, ["backup_name"])` fails if the declaration
flips or the name is removed. The comment on that set is widened to state the
criterion — what is read rather than what is acted on — instead of the incidental
property its members happened to share.

"Keeps" rather than "declares", and the distinction is load-bearing.
`hmc_restore_vios` passes no `exhaustive_targets` argument at all: `tool()`
defaults it to `True` (`src/hmc_mcp/tool_registry.py:314`) and the decorator
lowers it only when a handler declares no selector (`:336`). So the bounded
classification of every tool in this position is held by *omission*, and nothing
at the tool makes a reader aware a classification was made. That is the reason
`backup_name` needed the enumeration entry rather than a comment: the entry is
the only place this tool's classification becomes visible and fails when it
changes.

Two questions this deliberately does not answer, each filed with its own owner
rather than asserted here:

- Whether `chviosbackup` resolves `-file` inside the declared VIOS's catalog or
  will follow a path out of it (#283, which carries the IBM sources and the
  experiment that would settle it). Nothing in this checkout can exercise an HMC.
  Under this decision it is a disclosure question, not a target one — the same
  split ADR 0039 made for `iso_source`, whose local-file-read concern is #261.
- Whether restoring an `ssp`-type entry — which `hmc_backup_vios` can create in
  the same catalog — reconfigures the cluster rather than only the declared VIOS
  (#282). If it does, `hmc_restore_vios`'s own `exhaustive_targets` declaration is
  wrong. That is a question about the operation's effect, not about the identity
  `backup_name` carries, and fixing it would change the declaration, not this
  name's membership.

## Consequences

- One rule decides the next classification, and it is ADR 0039's own: is the
  resource acted on the value of a declared selector or reached from one. The
  side-of-the-filesystem proxy is gone.
- No claim about HMC-side `chviosbackup` behaviour is load-bearing anywhere in
  this decision. The classification rests only on which argument the command
  reads and which one selects the VIOS it writes, both visible in this tree.
- A `targets` table can still grant `hmc_restore_vios`. That matters: the tool is
  `destructive` and its declared effect lands on the declared VIOS, so
  classifying it unbounded would have forced the strictly wider `all-targets`
  grant to reach a strictly narrower operation — subject to #282.
- `_PAYLOAD_SOURCE_ARGUMENTS` now spans two shapes: sources outside the HMC, and
  an HMC-side name that is still only read. The set's comment carries that, so
  the next HMC-side payload argument has a place to land instead of looking like
  a contradiction.
- No source behaviour changes. This is a classification and its guardrails, so
  nothing an operator has granted becomes narrower or wider.

## Considered & rejected

- **Do nothing.** The divergence is not exploitable — `shlex.quote` blocks
  injection and the restore lands on the declared VIOS. But the rule text is what
  the next classification is decided against, and a rule that already gives the
  wrong answer for a name in the tree spends that ambiguity later, at a worse
  moment.
- **Add `backup_name` to `UNBOUNDED_ARGUMENTS`** and declare `hmc_restore_vios`
  non-exhaustive. The fail-closed-looking option, and it needs no claim about the
  HMC. Rejected because it misclassifies: the list is about resources acted on,
  and the entry read is not one. It would also force operators to `all-targets` —
  every VIOS on every system — to permit one declared restore, a real widening
  bought with a wrong reason. If #282 resolves against containment, the correct
  fix is `hmc_restore_vios`'s declaration, which is a per-*tool* change and leaves
  the name where this record puts it.
- **Narrow the rule to "a file the operation writes on the HMC".** #264's own
  proposal. Rejected on the evidence: `hmc_restore_lpar_profiles` writes no HMC
  file and is non-exhaustive, so the narrowed rule contradicts a live declaration
  the moment it is written down.
- **Keep `backup_name` bounded on a containment argument, and add a server-side
  guard to make it true** — the HMC keys the backup catalog by the `-id` VIOS
  UUID, so a bare name cannot leave it, enforced here by refusing separators and
  dot-segments as ADR 0039 refuses them for `job_href`. This was the first draft
  of this record, and both halves fell together. The premise needs a fact about
  `chviosbackup` that is not published, and writing it as settled would repeat the
  substitution ADR 0039 documents itself committing three times: an assumption
  about a system we cannot observe, in the grammar of a fact about code we can.
  Once the reads-versus-acts-on rule reaches the same classification from evidence
  in this tree, the guard buys the target dimension nothing — it is the right
  remedy for #283, and is recorded there with the quoting test it would move.
- **Leave `backup_name` out of `_PAYLOAD_SOURCE_ARGUMENTS` and note it in prose.**
  Rejected because prose is what failed here. The set is enumerated precisely so a
  member cannot join silently — a threat scan once found `iso_source` missing from
  it — and an unenumerated exception is the same defect this record is fixing.
