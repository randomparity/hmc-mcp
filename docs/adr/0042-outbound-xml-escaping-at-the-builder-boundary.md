# 0042 — Outbound XML escaping at the builder boundary

## Status

Accepted (2026-08-19)

## Context

Every request document this package sends to an HMC is rendered by string interpolation.
`documents.py` uses f-strings; `jobs.py` uses module-level `.format()` templates. Until this
record, nothing between a caller's argument and the wire escaped anything: `rg -n
"saxutils|quoteattr|xml.sax" src/hmc_mcp/` returned no hit against 23 `build_*_document`
builders and one `build_job_request`.

The visible consequence needs no attacker. Probing each public builder in `documents.py` with
`R&D` in each string parameter produced `ParseError: not well-formed` from **15 of the 16
string-taking builders**. The exception is `build_boot_order_document`, whose input is a closed
vocabulary. Two of those failures are ordinary use rather than edge cases:

- `build_ldap_config_document(search_filter="(&(objectClass=person)(uid=*))")` — `&` is the
  conjunction operator in LDAP filter syntax, so `hmc_configure_ldap` fails for essentially any
  filter an operator would write.
- `build_hmc_user_document(password=...)` — neither `&` nor `<` is unusual in a generated
  password.

`jobs.py` fails the same way and an f-string scan misses it, because `_PARAM_TEMPLATE`
interpolates `{name}` and `{value}` through `.format()`. `_migrate_job` places
`target_profile_name` in that `value` position on a path whose first parameter is
`TargetManagedSystemName` — the identity `hmc_migrate_lpar` exists to authorize.

The second consequence is that an unescaped value can close its own element and open siblings,
producing a document that is *well-formed*, so nothing downstream rejects it. Two instances were
confirmed at the document level: `build_hmc_user_document(description=...)` emitting a second
`UserID` into a POST to `/rest/api/web/HmcUser`, which carries no identity in its URL; and
`build_vscsi_mapping_document(target_device=...)` emitting a second `AssociatedLogicalPartition`
href, crossing the LPAR dimension.

**Which duplicate a real HMC's unmarshaller honours is unverified in both directions.** No live
HMC was reachable from this work. Last-wins is typical of JAXB, but that is a guess, not a
result. The encoding defect is demonstrated; the escalation is its unbounded consequence, and
this record does not depend on the answer — the fix removes the ability to emit the duplicate at
all.

This boundary is not the one ADR 0035 through ADR 0041 built. The authorizer inspects declared
tool arguments and the request URI; it never reads the rendered body. ADR 0039 names this
boundary at lines 561-599 and explicitly declines to close it: "a second boundary this record
does not close … It is owned separately." This is that record.

Issue #143 closed an instance of this class for one field, `sharing_mode`, on the assessment that
it was "the one free-string parameter that reaches generated XML unvalidated". The sweep above
falsifies that closing claim. The `Literal` remedy was right for a closed vocabulary and cannot
generalize: `search_filter`, `description`, `password`, `media_name`, `storage_name`, and
`target_device` have no vocabulary to constrain them to.

## Decision

**One escaping primitive, applied at each builder's parameter boundary, with the existing
`Literal` validations left in place.**

`xmlutil.escape_xml` escapes all five XML metacharacters — `&`, `<`, `>`, `"`, `'` — through
`xml.sax.saxutils.escape` with the two attribute entities added. Escaping all five, rather than
splitting `escape` for text and `quoteattr` for attributes, means one escaped form is safe as
character data *and* inside a single- or double-quoted attribute value. A builder therefore never
chooses an encoding per interpolation site, and moving a value from an element to an `href`
cannot introduce a defect. The cost is that `>` and the quote characters are escaped in text
positions where XML does not require it; that is invisible after parsing.

`xmlutil.escapes_string_arguments` is a decorator that applies `escape_xml` to every string a
caller passes — including strings inside a `list` (`physical_volumes`) or a `dict` (the
job-parameter mapping) — before the wrapped builder runs. Every other argument type passes
through untouched.

**Escaping at the boundary rather than per interpolation site is the whole point.** After the
decorator runs, the function body holds no unescaped caller value, so the number of times it
interpolates one, and whether it interpolates into an element or an attribute, stop being facts
anyone has to check. Per-site escaping would leave 40-plus sites where a single miss is silent.

`escape_xml` is idempotent, and it carries that fact in the type rather than by inspection: it
returns a private `str` subclass and returns an instance of that subclass unchanged. Without
this, `build_vios_document` — which delegates to `build_lpar_document`, both decorated — would
escape `name` twice and send `R&amp;amp;D`.

**`documents.py` decorates all 23 builders; `jobs.py` decorates one function.** They differ
because `jobs.py` renders XML in exactly one place: every `*_job` builder returns
`build_job_request(...)`, so one decorator is the module's whole encoding boundary. `documents.py`
has no such choke point — each builder renders its own template — so the invariant there is
"every public builder carries the decorator", and a test asserts exactly that by reflection.

**Closed-vocabulary parameters keep their `ValueError`.** Escaping and validation are not
alternatives: validation gives a better message and rejects a value the HMC would refuse anyway,
while escaping covers the free-text parameters no vocabulary can constrain. The contract each
string-carrying parameter now meets is *escape or reject*, and the harness asserts that disjunction
rather than assuming which arm applies.

**The harness discovers builders by reflection, not from a list.** `tests/unit/test_xml_escaping.py`
enumerates the public string-returning `build_*` and `*_job` functions of both modules, reads each
parameter's annotation, and synthesizes a call. For each string-carrying parameter it asserts
either a `ValueError`, or all three of: the document parses, the payload parses back exactly, and
the document's element-and-attribute structure is identical to one built from a benign value. That
third assertion is what makes element injection impossible rather than merely unlikely — a value
that added a sibling element or an attribute would change the structure even when the result is
well-formed. A parameter whose annotation the harness cannot synthesize is a collection error, so
a builder cannot be silently skipped.

## Consequences

A document built from metacharacter-free input is byte-for-byte what it was before this change,
which is asserted directly. That property is why this record does not rewrite the builders onto
`ElementTree` — see below.

A rejection message for a closed-vocabulary parameter now quotes the *escaped* form of an illegal
value, because the decorator runs before the builder validates. `build_lpar_document(os_type="a<b")`
reports `'a&lt;b'`. The value is still recognizable and the existing tests that assert an illegal
value does not appear verbatim in a message continue to hold for `sharing_mode`, which travels
inside `LparResources` and is not touched by the decorator.

The decorator escapes strings in `list` and `dict` arguments but does not recurse into dataclass
or `TypedDict` arguments. Nothing needs it today: `LparResources.sharing_mode` is the only string
field on either dataclass and it is a closed vocabulary, and `RepositorySource` reaches the wire
through `build_job_request`'s `dict`, which is covered. If a free-text string field is added to
`LparResources` or `PasswordPolicySettings`, the harness synthesizes it and the case fails — the
recurrence guard reports the gap rather than the decorator silently growing to cover it.

`build_vscsi_mapping_document` and `build_virtual_optical_mapping_document` lose `vios_lpar_link`.
Both accepted it and neither rendered it, and no caller in the tree passed it; the harness surfaced
it as a parameter that cannot round-trip because it never reaches the document. ADR 0029 places
document builders outside the supported reusable API, so removing it is not a contract break. ADR
0039's prose at line 578 names that parameter as a free string the body carries; that detail is
superseded here.

Three sibling interpolation sites remain outside this record's surface, all in `client.py`:
`LOGON_REQUEST_TEMPLATE` (`{user}`, `{password}`), `_broker_file_create` (`{filename}`), and
`_broker_import` (`{media_name}`, `{broker_uri}`). They are the same defect — a logon password
containing `&` cannot authenticate — and they are tracked separately rather than folded in here,
because #263's scope is the document and job builders and the fix for `client.py` is the same
primitive applied to a different module. `pcm.py` was checked and is not affected: it interpolates
only a validated field name and a literal `true`/`false`.

## Considered & rejected

**Escape at each interpolation site.** This is what issue #263 proposes first, and it is what #143
did for one field. It is 40-plus sites across two modules and two interpolation mechanisms, every
one of which is a place to forget, and forgetting is silent. Rejected on the same evidence that
reopened this class: the last fix of this shape covered one field and the class stayed open for
120 issues.

**Rebuild the documents with `ElementTree`.** This is the textbook correct-by-construction answer
and it was rejected on one fact: **no live HMC is reachable from this work.** Re-serializing 23
documents through `ElementTree` changes indentation, self-closing-tag rendering, namespace
declaration placement, and the XML declaration, and the HMC's tolerance for each of those is
unverified — the documents' field names and shapes came from IBM's `HmcRestClient` reference
implementation, not from a schema this repo can validate against. That trades a defect that is
demonstrated and fixable for a wire-format risk that cannot be tested until someone has hardware.
The boundary decorator keeps every byte of a valid document identical, which is a property this
change can actually prove. If a live HMC becomes available (issues #121, #217), the `ElementTree`
path becomes testable and this decision is worth revisiting; nothing here blocks it.

**A `Literal` or regex for every free-text parameter.** #143's remedy, generalized. There is no
vocabulary for a description, a password, an LDAP filter, or a media name, and a regex that
excluded XML metacharacters from them would reject `(&(objectClass=person)(uid=*))` — the exact
input this record exists to make work.

**A hand-maintained list of builders in the test.** The acceptance criterion is that a *newly
added* builder that forgets to escape fails CI. A list satisfies today's builders and fails the
criterion, because the same commit that adds a builder is the one that would have to remember the
list. Reflection over the module is what makes the guard hold for code nobody has written yet.

**An AST lint asserting every f-string interpolation is escaped.** It would catch a missing
decorator at the mechanism level, but it has to distinguish caller values from module constants,
integers, and internally-derived element names, which makes it a second rule set to maintain
alongside the one it guards. The behavioral harness plus the "every builder is decorated"
reflection test cover the same ground by observing what the builders actually emit.
