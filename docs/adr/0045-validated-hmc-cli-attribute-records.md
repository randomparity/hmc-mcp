# 0045 — One validating builder for HMC CLI `-i` attribute records

## Status

Accepted (2026-08-19)

## Context

`chsyscfg` and `mksyscfg` take their configuration as a single `-i` argument holding an
attribute record: `name=lpar1,description=web tier`. Two characters carry that record's
structure. A comma ends one attribute and starts the next; an equals sign ends an attribute
name and starts its value. The HMC splits the record itself, after the shell has finished with
the argument.

`src/hmc_mcp/ssh_commands.py` built six such records by f-string and wrapped each in
`shlex.quote`. A comment at the `mksyscfg` site said that quoting stopped special characters
from breaking "the shell command or the mksyscfg attribute string". The second clause was
false. `shlex.quote` guarantees exactly one thing: the remote shell passes the record to
`chsyscfg` as a single argument instead of splitting it or interpreting metacharacters. It says
nothing about the delimiters *inside* that argument, because the HMC's parser runs afterwards
and sees the quotes already removed.

The consequence was an attribute-injection hole. `set_lpar_description` guarded `lpar_name`
against `,`, `=`, space, and `;`, each with a message naming the `-i` parser as the reason, and
then interpolated `description` — validated only against non-ASCII and control characters — into
the same record. A description of `x,foo=bar` produced
`name=lpar1,description=x,foo=bar`, three attributes where the caller was authorized to set one.
The guard was written for one field of a two-field record and omitted at its neighbour. Four
further records — `msp`, `lpar_proc_compat_mode`, `sync_curr_profile`, and the
`chsyscfg -r prof` record carrying `profile_name`, `drc_index`, and `lpar_name` together — had
no delimiter guard at all.

The project already holds the reasoning this record generalizes. `config.py`'s
`validate_agent_id` rejects `,` and `=` with the stated reason "corrupt the HMC CLI `-i`
parser". That guard exists because an agent identifier reaches an ownership token that
`set_lpar_description` writes into a record. The rule was correct and applied at one entry
point rather than at the record.

What the HMC does on receiving a duplicate or unexpected attribute — accept it silently,
last-wins, or error — was not verified; no live HMC was available to this change. The fix does
not depend on the answer, because the value is refused before a command is built.

## Decision

**One builder owns the record grammar.** `build_attribute_record(pairs)` takes an ordered
sequence of `(attribute, value)` pairs, validates each attribute name and each rendered value
against the record grammar, and returns the joined record. All six `-i` sites call it. No site
composes a record by f-string any more.

**The grammar rejects; it does not encode.** A value carrying `,` or `=` is refused with
`HMCCLIError`, naming the field, the offending character, and what that character does to the
record. The HMC CLI's own escape convention for values containing commas (wrapping the value in
double quotes inside the record) is not adopted: it would have to survive `shlex.quote`, the
remote shell, and the HMC's parser in agreement, and none of that is testable here without a
live HMC. Refusal is verifiable from the client alone.

**Attributes carrying an HMC object name are held to the stricter set that already applied to
one of them.** `name`, `lpar_name`, and `profile_name` additionally reject a space and a
semicolon, preserving verbatim the four rejections `set_lpar_description` performed on
`lpar_name` and extending them to the object names in the other five records. Every other
attribute rejects only the two structural characters, because a description legitimately
contains spaces — the ownership token of ADR 0011 is `[hmc-mcp owner:<id> created:<date>]` — and
semicolons are not record structure.

**`validate_lpar_description` reads the same table.** It keeps raising `ValueError` for a
description carrying `,` or `=`, iterating the builder's own delimiter table rather than
restating it. The tool layer calls it before UUID resolution, so a bad description is refused
without an HMC round trip; the builder refuses the same value again inside
`set_lpar_description` for callers that bypass the tool. Two layers, one table.

**The comment at the `mksyscfg` site now says what each mechanism protects** — `shlex.quote` the
shell word, `build_attribute_record` the record's own delimiters — because it is the text
whoever writes the seventh record site will read.

**A test enforces the coupling.** `tests/unit/test_i_record_grammar.py` parses every module
under `src/hmc_mcp/` and fails any function that builds a command string containing the `-i`
flag without calling `build_attribute_record`. A new record site that skips the builder reddens
the suite rather than waiting for a reviewer to notice.

## Consequences

**`hmc_set_lpar_description` narrows its accepted input.** A description containing `,` or `=`
succeeds today and is refused after this change, with `ValueError` naming the character. This is
a public MCP tool contract change and the reason this record exists.

The narrowing is not a loss of reachable function. Such a description never reached the HMC as a
description: the HMC parsed the text after the first comma or the second equals sign as further
attributes. `description=owner=alice env=prod` set `description=owner` and then attempted an
attribute named `alice env`. The caller who wrote it got an outcome it did not ask for, or an
HMC error, never the description it typed. What changes is that the failure is now local,
immediate, and names the character.

`tests/lpar/test_lpar_description.py::test_set_lpar_description_embeds_description` pinned the
old behaviour with `owner=alice env=prod`. It now pins the refusal, and a separate test pins
that an ordinary description still reaches the record unchanged.

The same narrowing applies to `profile_name` in `create_lpar_via_cli` and
`assign_profile_io_slot`, to `mode` in `set_lpar_proc_compat`, and to `drc_index` in
`assign_profile_io_slot`. Each of those is a name or an enumerated value on the HMC side; none
has a documented form containing a record delimiter.

**Error type stays split by layer, deliberately.** `validate_lpar_description` raises
`ValueError` and the builder raises `HMCCLIError`. That is the split the module already had —
description validation is `ValueError`, `-i` record refusal is `HMCCLIError` — and the existing
tests and `stamp_lpar_ownership`'s best-effort `except (HMCCLIError, OSError, ValueError)` both
depend on it. Unifying the two would be a second contract change with no caller asking for it.

**`config.py` is unchanged.** `validate_agent_id` keeps its own forbidden table; it rejects a
superset (`[`, `]`, `/`, `:` as well) for reasons that are not record grammar — the ownership
token format and the `X-Audit-Memento` header. Importing the builder there would invert the
dependency, since `ssh_commands` imports `config`. The duplication is two characters wide and
both sites now name the same reason.

## Residuals

`chhwres -a` records share the grammar and are untouched here. `remove_vnic` interpolates a
caller-supplied `vnic_id` into `vnic_id=<id>`, and `add_vnic` builds
`capacity=…,vswitch_name=…,port_vlan_id=…[,backing_devices=…]` from caller values. They are
excluded because `backing_devices` is documented as an opaque string passed verbatim, and the
HMC's multi-device syntax for it is comma-separated — so the record grammar and the attribute's
own grammar disagree, and resolving that needs the HMC-side answer this change did not need.
Tracked as issue #285.

Whether the HMC errors or silently accepts a duplicate attribute is still unverified. It bounds
how bad the old behaviour was, not whether the new behaviour is correct.

## Considered & rejected

**Add `,` and `=` to `validate_lpar_description` and stop.** The smallest change that closes the
reported injection. Rejected: it leaves four unguarded records and the false comment standing,
which is the condition the issue reports, not a fix for it. It also keeps the guard as a
property of one field rather than of the record, so the seventh site would be written the same
way as the sixth.

**Encode instead of reject** — quote or escape a value carrying a delimiter so the caller's text
survives. Rejected: the HMC's quoting convention has to agree with `shlex.quote` and the remote
shell across three parsers, and this change had no live HMC to confirm that agreement on. An
encoder that is wrong fails open, silently, with the caller's text landing as structure — the
exact failure being fixed. A rejection that is wrong fails closed and is visible.

**One forbidden set for every value.** Simpler, and it was rejected because it is wrong in both
directions: applying the name set everywhere would refuse descriptions containing spaces,
including ADR 0011's ownership token, and applying the description set everywhere would drop
four rejections `lpar_name` performs today. The two sets exist because the two kinds of value
differ.

**A per-call-site `strict=` parameter naming which fields get the name set.** Rejected: it puts
the decision at the call site, which is where the original omission happened. Deriving it from
the attribute name keeps the record grammar's rules inside the record grammar's owner.
