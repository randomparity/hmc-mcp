# 0045 — One validating builder for HMC CLI `-i` attribute records

## Status

Accepted (2026-08-19)

## Context

`chsyscfg` and `mksyscfg` take their configuration as a single `-i` argument holding an
attribute record: `name=lpar1,description=web tier`. Three characters carry that record's
structure. A comma ends one attribute and starts the next; an equals sign ends an attribute
name and starts its value; a double quote is IBM's documented escape for a value that has to
contain a comma, so it opens a region in which the following commas are not separators — IBM's
own note gives `chsyscfg -m CS6520 -r lpar -i "name=No comma name,\"new_name=comma, name\""`. A
control character is structure by a fourth route: the record is a single line, and the `-f` file
form reads the same data format one record per line. The HMC splits all of this itself, after
the shell has finished with the argument.

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

**The grammar rejects; it does not encode.** A value carrying `,`, `=`, `"`, or a control
character is refused with `HMCCLIError`, naming the field, the offending character, and what
that character does to the record. The HMC's own escape convention — wrapping the value in
double quotes inside the record — is not adopted: it would have to survive `shlex.quote`, the
remote shell, and the HMC's parser in agreement, and none of that is testable here without a
live HMC. Refusal is verifiable from the client alone. Rejecting `"` rather than emitting it is
what makes the rejection complete: an unadopted escape character left in a caller's value is
still an escape character to the parser.

**A repeated attribute is refused.** `build_attribute_record` rejects a record naming the same
attribute twice. What the HMC does with a duplicate is the one thing this change could not
verify, so the component that owns the grammar should not be the one that produces such a
record.

**The grammar covers record structure and nothing else.** Space and semicolon are *not* record
structure: IBM's example above passes `name=No comma name` unquoted. `set_lpar_description`
has always refused both in the LPAR name it writes a description for, and it still does, at that
one site, unchanged. That rejection is deliberately not moved into the builder. Doing so would
extend it to `create_lpar_via_cli`, `set_lpar_msp`, `set_lpar_proc_compat`, `sync_lpar_profile`,
and `assign_profile_io_slot`, whose `lpar_name` and `profile_name` arrive from HMC name
resolution — so on any HMC with a space in a partition or profile name those five tools would
stop working, with no caller workaround, over a restriction whose own stated reason ("*may*
corrupt the parser") is hedged and unverified. Narrowing five public tools is not something a
record-grammar change gets to do on the way past. See the residual below.

**`validate_lpar_description` reads the same table.** It keeps raising `ValueError` for a
description carrying `,`, `=`, or `"`, iterating the builder's own delimiter table rather than
restating it. The tool layer calls it before UUID resolution, so a bad description is refused
without an HMC round trip; the builder refuses the same value again inside
`set_lpar_description` for callers that bypass the tool. Two layers, one table.

**The comment at the `mksyscfg` site now says what each mechanism protects** — `shlex.quote` the
shell word, `build_attribute_record` the record's own delimiters — because it is the text
whoever writes the seventh record site will read.

**A test enforces the coupling.** `tests/unit/test_i_record_grammar.py` parses every module
under `src/hmc_mcp/` and `scripts/`, finds each function whose own string literals name
`chsyscfg` or `mksyscfg` *and* carry the `-i` flag, and follows the expression interpolated
directly after that flag: it must be a `build_attribute_record` call, or a local name assigned
from one. A function that builds one record through the builder and a second by f-string fails,
because the check is per payload rather than per function.

Its reach is bounded and worth stating. It reads literals in the function itself, so hoisting
the command template to a module constant, or routing the record through a shared command
helper, would take a new site out of its view. It is a guard against the next author repeating
the f-string that is already in the file, not a proof that no such site can exist.

## Consequences

**`hmc_set_lpar_description` narrows its accepted input.** A description containing `,`, `=`, or
`"` succeeds today and is refused after this change, with `ValueError` naming the character. This
is a public MCP tool contract change and the reason this record exists.

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

**No tool narrows on anything that is not record structure.** The space and semicolon rejections
stay where they were, so `hmc_create_lpar`, `hmc_set_lpar_msp`, `hmc_set_lpar_proc_compat`,
`hmc_sync_lpar_profile`, and `hmc_assign_profile_io_slot` keep accepting every name they accept
today, including a name with a space. Control characters are the exception, and they are
structure: a newline can terminate the record and a NUL can truncate it inside the HMC, so the
partition acted on would differ from the one the caller named. No HMC object name has a
documented form containing one.

**Errors quote the offending value.** `build_attribute_record`'s message includes the rejected
value, which reaches MCP callers and audit logs. Every attribute routed through it today carries
a name, an enumerated mode, or a number. Its docstring says not to route a credential attribute
(`chhmcusr -i "name=…,passwd=…"`) through it without redacting first.

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

`--filter lpar_names=<name>` is the same name/value syntax and is untouched. It appears on eight
`lssyscfg`/`lshwres`/`chhwres` reads and selects which partition a command acts on, so a value
carrying its structure would misdirect a mutation rather than add an attribute to one. It is
pre-existing, outside this record's `-i` subject, and folded into #285's scope.

Two questions about the grammar remain unverified, both answered fail-closed rather than left
open. Whether the HMC treats a newline inside an `-i` record as a record separator: the `-f`
file form documents one record per line, so a control character is refused. Whether a backslash
escapes anything inside the record: no IBM source found says it does, so it is *not* refused,
and a value containing one still reaches the HMC. If a live HMC ever shows otherwise, the fix is
one entry in `_RECORD_DELIMITERS`.

The space and semicolon rejection on `set_lpar_description`'s `lpar_name` is itself unverified
and, for the space, contradicted by IBM's `name=No comma name` example. This change kept it
rather than widening a public tool's accepted input as a side effect of a security fix. Settling
it — verify against a live HMC, then either remove it or give it a sourced reason — is issue
#288.

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

**Derive the strict set from the attribute name** — treat `name`, `lpar_name`, and
`profile_name` as object names and refuse a space and a semicolon in them everywhere. This was
the first shape written here and it is wrong. It reads as a tidy generalization of the guard
`set_lpar_description` already had, but a space is not record structure, so the rule it
generalizes is not the rule this record is about. The effect would be to narrow five more public
tools on values that arrive from HMC name resolution — breaking them outright on any HMC with a
space in a partition name — on the strength of a hedged, unverified comment. A security fix does
not get to carry an unrelated restriction along with it.

**A per-call-site `strict=` parameter naming which fields get the extra rejections.** Rejected
for the same reason the previous alternative is: with space and semicolon out of the record
grammar, there is no second set for a parameter to select, and the one site that keeps them
states them itself in four lines.
