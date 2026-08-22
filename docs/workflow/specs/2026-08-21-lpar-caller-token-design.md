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
   at tool entry, before any HMC round trip, naming the offending character or the
   violated bound. Grammar: 1–64 chars; printable ASCII only; no whitespace or control
   characters; `,`, `=`, `"`, `[`, `]` forbidden. (Criteria 3–4)
4. **Best-effort stamp.** The combined write reuses ADR 0011's single best-effort SSH
   call; a failure appends a warning and sets `ownership_stamped=False`; it never fails
   the create. (Criterion 6)
5. **Ownership parse unaffected.** `parse_lpar_ownership_owner()` still extracts the
   owner from a description carrying both segments. (ADR 0011 authorization depends on it)
6. **Machine-readable extraction.** New `parse_lpar_ownership_caller_token(description)`
   returns the caller token or `None`. (Issue intent)
7. **Documented surface.** Both tools' rendered parameter descriptions (Google-style
   `Args:` sections per ADR 0016), the README tool table rows, and the CLI help state the
   parameter and its grammar. (Criterion 8)

## Components

| File | Change |
|---|---|
| `src/hmc_mcp/ssh_commands.py` | Add `validate_caller_token()`; extend `stamp_lpar_ownership(..., caller_token=None)` to compose the full description in one write. |
| `src/hmc_mcp/operations_lpar.py` | Add `caller_token` field to `LparCreation`; add `parse_lpar_ownership_caller_token()`; thread the token through `stamp_created_lpar_ownership` and `create_and_stamp_lpar`. |
| `src/hmc_mcp/server_lpars.py` | Add `caller_token: str \| None = None` to `hmc_create_lpar`; validate at entry. |
| `src/hmc_mcp/server_provision.py`, `operations_provision.py` | Same parameter on `hmc_provision_lpar`, threaded into `LparCreation`. |
| `src/hmc_mcp/cli_lpars.py` | `--caller-token` option on the CLI create command. |
| `README.md` | Tool-table rows mention the optional token. |
| Tests | Unit + respx contract tests per repo layout. |

## Error handling

Validation failures are local `ValueError`s (message names the character/bound) raised
before name-uniqueness checks — mirroring how `set_lpar_description` layers
`validate_lpar_description`. Transport/SSH failures inside the stamp keep the existing
best-effort catch.

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
   whitespace, brackets, non-printable input before any network I/O.
2. Existing `build_attribute_record` delimiter table + `shlex.quote` at the
   `set_lpar_description` boundary — defense in depth for direct callers bypassing the
   tool layer.
3. Best-effort catch bounds blast radius: a rejected/failed stamp cannot abort a create.

**Explicitly out of scope.** Truthfulness/uniqueness of tokens (advisory metadata, like
the ownership stamp); HMC-side enforcement of who may set which token; storing tokens in
any local registry (rejected in ADR 0011); secrets embedded in tokens — callers are
documented not to place credentials in the description field.

## Testing

- Unit: grammar acceptance table (typical tracker IDs, 64-char boundary, allowed
  punctuation) and rejection table (empty, 65-char, each forbidden character,
  whitespace, non-ASCII, control char).
- Composition: `stamp_lpar_ownership` writes `[owner] [caller t]` in one
  `set_lpar_description` call; without `caller_token` the description is unchanged.
- Parse: round-trip extraction of owner and caller segments; owner regex still matches
  with a caller segment present.
- Tool contracts (respx): `hmc_create_lpar(caller_token=...)` and
  `hmc_provision_lpar(caller_token=...)` produce stamped results; invalid token raises
  `ValueError` before any HTTP request is made (no routes called); omission preserves the
  existing pinned behaviors (existing tests stay green untouched).
- Schema: rendered parameter descriptions expose the new argument (per ADR 0016 contract
  tests).

## Eval plan

Not applicable — no AI surface is added or modified; this changes tool parameters only.
