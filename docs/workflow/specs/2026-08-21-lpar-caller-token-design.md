# Spec: Caller Token in the LPAR Description (issue #358)

Decision record: [ADR 0064](../../adr/0064-caller-token-in-lpar-description.md)

## Outcome

Callers of `hmc_create_lpar` and `hmc_provision_lpar` may pass an optional
`caller_token` that is written into the created LPAR's description field so the
LPAR can be tracked back to the caller's tracking protocols.

## Charter (frozen)

- interaction: interactive
- scope identity: https://github.com/randomparity/hmc-mcp/issues/358 — token q358-db4ba1c1
- outcome / criteria / provenance / exclusions / surface / ambiguities: as posted in the
  `WORK:SCOPE` annotation on issue #358; the token format is delegated to this design.

## Normative guarantees

1. **Optional and backward compatible.** Omitting `caller_token` produces byte-for-byte
   today's behavior — same description, same result fields, same warnings. (Criterion 5)
2. **Format.** With `caller_token` supplied, the description is exactly
   `[hmc-mcp owner:<agent> created:<date>] [caller <token>]`. (Criteria 1–2)
3. **Fail-fast validation.** A `caller_token` violating the grammar raises `ValueError`
   before any HMC round trip, naming the offending character or the violated bound.
   Grammar: 1–64 chars; printable ASCII only; no whitespace or control characters;
   `,`, `=`, `"`, `[`, `]`, `\` forbidden (`\` because ADR 0045 records its `-i`
   behaviour as unverified). The empty string is a grammar violation, not an omission
   (codebase falsy-string idioms do not apply); a non-`str` value raises the same
   `ValueError` before any character check, since the second site exists for callers
   bypassing MCP tool typing. Two named sites: tool entry first — applying regardless
   of `dry_run`, so even a provision dry run fails fast rather than reporting steps for
   a token that can never land — then `create_and_stamp_lpar` re-validates as its
   *first statement*, before `find_partition_by_name` and outside the best-effort
   catch, so no create can precede rejection and a malformed token cannot silently
   discard the ownership stamp. The swallow inside `stamp_lpar_ownership` stays
   defensive-only for transport errors. (Criteria 3–4)
4. **Best-effort stamp.** The combined write reuses ADR 0011's single best-effort SSH
   call; a failure appends a warning and sets `ownership_stamped=False`; it never fails
   the create. (Criterion 6)
5. **Ownership parse unaffected.** `parse_lpar_ownership_owner()` still extracts the
   owner from a description carrying both segments. (ADR 0011 authorization depends on it)
6. **Machine-readable extraction.** New `parse_lpar_ownership_caller_token(description)`
   returns the caller token or `None`. Contract: matches the literal case-sensitive
   prefix `[caller ` followed by one token of the extractor class (everything except
   whitespace and brackets) and `]`; anchored to a well-formed ADR 0011 ownership
   stamp — the segment must directly follow the stamp's closing bracket plus one space,
   so spoofed, duplicated, or misordered segments yield `None`; returns `None` when the
   segment is absent or empty. Tokens containing `:` round-trip. (Issue intent)
7. **Documented surface.** Both tools' rendered parameter descriptions (Google-style
   `Args:` sections per ADR 0016), the README tool table rows, the CLI help, and the
   ADR 0016 schema contract tests state the parameter and its grammar. Documentation
   also states how a caller learns whether the token landed: with one combined write,
   `ownership_stamped=True` confirms both segments; `False` means both were lost;
   `None` means the stamp was skipped (no partition name, unresolvable system name, or
   CLI fallback without a body) — warnings carry the reason. (Criteria 6–7)

## Components

| File | Change |
|---|---|
| `src/hmc_mcp/ssh_commands.py` | Add `validate_caller_token()`; extend `stamp_lpar_ownership(..., caller_token=None)` to compose the full description in one write. |
| `src/hmc_mcp/operations/lpar.py` | Add `caller_token` field to `LparCreation`; add `parse_lpar_ownership_caller_token()`; validate as the first statement of `create_and_stamp_lpar`; thread through `stamp_created_lpar_ownership`. |
| `src/hmc_mcp/server_tools/lpars.py` | Add `caller_token: str \| None = None` to `hmc_create_lpar`; validate at entry. |
| `src/hmc_mcp/server_tools/provision.py`, `operations/provision.py` | Same parameter on `hmc_provision_lpar`, threaded into `LparCreation`. |
| `src/hmc_mcp/cli_commands/lpars.py` | `--caller-token` option on the CLI create command. |
| `README.md` | Tool-table rows mention the optional token. |
| Tests | Unit + respx contract tests per repo layout; ADR 0016 schema contract tests for the new parameters. |

## Error handling

Validation failures are local `ValueError`s raised at the two named sites in
guarantee 3 — tool entry first (before name-uniqueness checks, precondition
checks, or any HMC call, and regardless of `dry_run`), then again as the first
statement of `create_and_stamp_lpar`. Transport/SSH failures inside the stamp
keep the existing best-effort catch.

## Threat model

**Boundary inventory.** One added boundary: an untrusted MCP caller's `caller_token`
string reaches SSH command construction for `chsyscfg -i` (via `set_lpar_description`,
which already shlex-quotes and record-validates). No new REST boundary; the token never
touches headers or XML builders.

**Actor model.** The untrusted party is the MCP client process invoking the tools (it may
be an LLM agent following untrusted content). The HMC credential holder is the local
operator.

**Controls per boundary.**
1. Grammar validation at tool entry (`validate_caller_token`) — rejects delimiters,
   whitespace, brackets, backslash, non-printable input before any network I/O.
2. Existing `build_attribute_record` delimiter table + `shlex.quote` at the
   `set_lpar_description` boundary — defense in depth for direct callers bypassing the
   tool layer.
3. Best-effort catch bounds blast radius: a rejected/failed stamp cannot abort a create,
   and the operations-layer validation keeps that catch from swallowing a malformed
   token into a lost ownership stamp.

**Explicitly out of scope.** Truthfulness/uniqueness of tokens (advisory metadata, like
the ownership stamp); HMC-side enforcement of who may set which token; storing tokens in
any local registry (rejected in ADR 0011); secrets embedded in tokens — callers are
documented not to place credentials in the description field; HMC-side description
length/truncation behaviour (documented residual in ADR 0064).

## Testing

- Unit: grammar acceptance table (typical tracker IDs, tokens containing `:` and other
  allowed punctuation, 64-char boundary) and rejection table (empty string, 65-char,
  each forbidden character including `\`, whitespace, non-ASCII, control char, non-`str`
  input raising `ValueError` not `TypeError`).
- Composition: `stamp_lpar_ownership` writes `[owner] [caller t]` in one
  `set_lpar_description` call; without `caller_token` the description is unchanged.
- Parse: round-trip extraction of owner and caller segments; owner regex still matches
  with a caller segment present; caller tokens containing `:` extract intact.
  Adversarial parse rows: caller segment before the ownership stamp → `None`;
  duplicate caller segments → `None`; `[caller]`, `[caller ]`, and lowercased-prefix
  variants → `None`; canonical pair extracts both segments.
- Validation layering: an invalid token raises from `create_and_stamp_lpar` as its
  first statement (zero HTTP routes called, asserted via respx `assert_all_called`)
  and from both tools before any HTTP request; tool-entry validation fires even with
  `provision(dry_run=True)` — precondition routes stay uncalled; `""` and non-`str`
  inputs raise `ValueError`.
- Tool contracts (respx): `hmc_create_lpar(caller_token=...)` and
  `hmc_provision_lpar(caller_token=...)` produce stamped results; omission preserves the
  existing pinned behaviors (existing tests stay green untouched).
- Schema: rendered parameter descriptions expose the new argument (per ADR 0016 contract
  tests).

## Eval plan

Not applicable — no AI surface is added or modified; this changes tool parameters only.

## Carried design-review record

Two `$trial-loop` runs on this design (2026-08-21), both exiting
**converged-with-deferrals**; every finding dispositioned `accepted-fixed` by editing
this spec and ADR 0064 before implementation:

- ADR run (4 iterations): race-window acknowledgment plus create-time-write alternative
  weighed; unevidenced `verified:` retagged `judgment:`; description-length residual
  stated; fold-in-brackets failure mode corrected to fail-closed `PermissionError`;
  colon round-trip fixed via a dedicated extractor class; validation site named outside
  the best-effort swallow; ADR 0016 contract-test obligation recorded; backslash
  forbidden in the grammar.
- Spec run (4 iterations): extractor anchoring/prefix/multiplicity/empty-segment
  contract pinned; second validation site pinned as `create_and_stamp_lpar`'s first
  statement; caller-token outcome semantics (`True`/`False`/`None`) added to the
  documentation obligation; `dry_run` interaction pinned with a contract test;
  non-`str` failure mode specified; empty string fixed as a grammar violation.
  One finding disposed `rejected-with-evidence`: the grammar-rationale asymmetry
  (backslash vs other shell-active characters) is cosmetic precision on settled ADR
  ground; the grammar set itself is chartered server-defined and unchanged.

No deferral records were created; nothing remains outstanding from either run.
