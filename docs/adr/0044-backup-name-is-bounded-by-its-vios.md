# 0044 — `backup_name` is bounded by its VIOS, and the server is what makes that true

## Status

Accepted (2026-08-19)

## Context

ADR 0039 gave each tool an `exhaustive_targets` declaration and paired it with
`UNBOUNDED_ARGUMENTS`, a list of public argument names carrying an identity no
`targets` allowlist can pin down. A tool accepting one of those names cannot
declare `exhaustive_targets=True`, so only `all-targets` grants it.

The guardrail comment that carries that rule stated the line as **which side** the
named thing lives on: `file_path` is a file on the HMC's own filesystem, "so the
policy is meant to bound it and cannot". `hmc_restore_vios` takes `backup_name`,
also a file on the HMC, and is bounded. Read literally, the rule and its
application disagree — and that sentence is what the next classification gets
decided against, which is the actual cost (#264).

The obvious reconciliation is that `file_path` is written and `backup_name` only
read. It is wrong. `hmc_restore_lpar_profiles` only reads its `file_path` and is
declared `exhaustive_targets=False` all the same. Whatever separates the two
arguments, writing is not it.

ADR 0039 already states the rule that does separate them, in its exhaustiveness
argument: every resource acted on must be "either the value of a declared
selector or derived by the server through the HMC's own containment from one".
The guardrail comment had drifted from it into a proxy — HMC-side versus
elsewhere — that happens to agree on `file_path` and disagrees on `backup_name`.

Applying the real rule:

- `bkprofdata -f <file_path>` and `rstprofdata -f <file_path>` take an absolute
  path anywhere on the HMC console filesystem. The declared `-m` system
  constrains it not at all: the same string names the same console file whichever
  system is passed. The object addressed is neither a declared selector's value
  nor reached from one — for the reading tool exactly as for the writing one.
- `chviosbackup -id <vios_uuid> -operation restore -file <backup_name>` addresses
  the per-VIOS backup catalog that `-id` names. IBM documents the store as
  `/data/viosbackup/<MTMS>/<VIOS-UUID>/<name>` and documents a backup name as
  1–40 characters drawn from `A-Z a-z 0-9 . - _`. A bare name is an entry inside
  the container the declared selector names; a different `-id` reaches a
  different catalog.

That second bullet has a soft joint, and it is the reason this record exists
rather than a one-line comment fix. IBM publishes the storage layout and the name
grammar — the grammar for the HMC's own backup-management UI — but this checkout
found no published `chviosbackup` reference stating that the CLI refuses a
path-shaped `-file` value, and nothing here can exercise an HMC. Asserting
containment as an HMC-side fact would repeat the substitution ADR 0039 names as
its own recurring error: an assumption about a system we cannot observe, written
in the grammar of a fact about code we can.

## Decision

`backup_name` stays out of `UNBOUNDED_ARGUMENTS` and `hmc_restore_vios` stays
`exhaustive_targets=True`, on containment rather than on which filesystem the
name refers to — and the server enforces the containment instead of assuming it.

`hmc_restore_vios` refuses a `backup_name` that could leave the catalog directory
before it builds the command: empty or whitespace-only, containing `/` or `\`, or
equal to `.` or `..`. With those refused, a `backup_name` is a name resolved
inside the directory the declared `-id` selects, whatever the HMC's own
validation does or does not do.

The refusal is deliberately the narrow one and not the documented grammar.
ADR 0039 made the same call for `job_href`: it refused dot-segments and declined
to require a UUID shape, because refusing a legitimate identifier would trade a
regression for reach the narrow refusal had already removed. Validating against
the full 1–40-character grammar would refuse any catalog entry an older or
differently-behaved HMC named outside it, and buy nothing over the separator ban.

No percent-decoded arm accompanies it — that guard exists in ADR 0039 because a
URL path reaches an HTTP router that may decode before routing. This value
reaches an SSH command line, and no component between the tool and the HMC CLI
percent-decodes it. `shlex.quote` stays where it is and is unrelated: it governs
shell metacharacters, a different failure from leaving the catalog.

The guardrail comment in `tests/app/test_tool_security.py` is rewritten to state
the containment question rather than the side-of-the-filesystem proxy, and a test
pins the classification and the guard together, so removing the guard fails the
test that asserts the classification.

## Consequences

- The rule text and every current classification agree, and the reason a future
  `backup_name`-shaped argument is decided against is the same one ADR 0039
  already uses for composites. One rule, not two.
- A `targets` table can still grant `hmc_restore_vios`, which matters: the tool is
  `destructive` and its effect lands squarely on the declared VIOS. Classifying it
  unbounded would have forced `all-targets` — a strictly wider grant — to reach a
  strictly narrower operation.
- A backup whose catalog name contains `/` or `\`, or is exactly `.` or `..`,
  cannot be restored through this tool. No HMC-created name can take those shapes
  under IBM's documented grammar, so this is expected to be unreachable; if it is
  reached, the error names the constraint and the operator has `all-targets` and
  the HMC CLI as the fallback.
- The containment claim is now a property of this repository's code and is tested
  here. It does not depend on the HMC's own validation, and it does not assert
  anything about that validation.
- `backup_name` is not added to the payload-source set either. That set is
  documented as sources *outside* the HMC, and widening it to hold an HMC-side
  name would blur the one distinction it exists to make.

## Considered & rejected

- **Do nothing.** The divergence is not exploitable — `shlex.quote` blocks
  injection and the restore lands on the declared VIOS. But the rule text is what
  the next classification is decided against, and leaving a rule that gives the
  wrong answer on a name already in the tree spends the ambiguity later at a
  worse moment.
- **Add `backup_name` to `UNBOUNDED_ARGUMENTS`** and declare `hmc_restore_vios`
  non-exhaustive. This is the fail-closed-looking option and it was seriously
  considered, since it needs no claim about the HMC at all. Rejected because it
  is wrong about what the tool acts on: the resource destroyed is the VIOS the
  `-id` selector names, and the catalog entry is contained by it. It would also
  force operators to grant `all-targets` — every VIOS, every system — to permit a
  restore on one declared VIOS, which is a real widening bought with a
  misclassification.
- **Narrow the rule to "a file the operation *writes* on the HMC".** The
  reconciliation the issue proposed. Rejected on the evidence:
  `hmc_restore_lpar_profiles` writes no HMC file and is non-exhaustive, so the
  narrowed rule would immediately contradict a live declaration.
- **Validate `backup_name` against IBM's documented 1–40-character grammar.**
  Stronger than containment needs, and it can refuse a legitimate catalog entry
  the HMC will happily restore. ADR 0039 rejected the analogous UUID-shape
  validation for `job_href` on the same reasoning, and consistency with that call
  is worth more here than the marginal narrowing.
- **Record the containment as an HMC-side fact and change no code.** The cheapest
  option, and the one this record most wanted to take. Rejected because the fact
  is not established: the storage layout and the name grammar are published, the
  CLI's treatment of a path-shaped `-file` is not. A rule resting on an unverified
  claim about an unobservable system is exactly the failure ADR 0039 documents
  itself committing three times.
- **Extend the guard to every tool taking a backup name.** There is one:
  `hmc_restore_vios`. `hmc_backup_vios` takes a `backup_type`, not a name, and
  `hmc_list_vios_backups` takes none. A shared helper for a single call site is
  the abstraction that outlives its second caller never arriving.
