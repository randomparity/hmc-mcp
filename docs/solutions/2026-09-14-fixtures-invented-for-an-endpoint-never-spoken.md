---
title: A green, mutation-tested, five-times-reviewed client method decoded JSON from an endpoint that returns Atom XML
date: 2026-09-14
tags: [invented-fixture, false-evidence, shared-premise-error, hmc-rest-api, git-worktree, gitignore, respx, mutation-testing]
components: [src/hmc_mcp/client/core.py, tests/unit/test_client.py, docs/refs/, docs/adr/0140-quick-property-discovery-reads-atom-nicknames.md]
---

## Problem

`HMCClient.list_quick_properties` (issue #788, PR #800) read
`/rest/api/uom/{R}/quick` with `resp.json()` and required a JSON array of strings.

Against a live HMC (P10, FW950, schema `V1_0`) the endpoint answers:

```
Status: 200
Content-Type: application/atom+xml
X-HMC-Schema-Version: hmc-mcp
```

with an Atom `<entry>` wrapping a `QuickProperty_Collection`. `resp.json()` therefore raised
`json.JSONDecodeError` on **every successful call**, converted by the method's own guard into
`HMCError("... returned invalid JSON: ...")`. The method could not work on any firmware level
that had ever been observed.

A second defect shipped with it: an `all_properties=True` argument building
`/rest/api/uom/{R}/quick/all`, which answers **HTTP 400** on that firmware. The path is
documented (see below); the endpoint does not exist.

What makes this worth recording is everything that did *not* catch it:

| Gate | Result on the broken code |
|---|---|
| 20 focused unit tests (`respx`-mocked) | all passed |
| Full suite + coverage gate | passed |
| CI: 8 verify legs (amd64/arm64 x py3.11-3.14) + wheel smokes | 19 SUCCESS, 1 SKIPPED |
| Mutation testing, 5 mutants of the parse | all 5 died — read as "the tests bite" |
| 2 adversarial design-review passes | findings raised and fixed; none was this |
| 2 adversarial branch-review iterations | `approve` |
| Security pass, scope audit, dead-code pass | `approve` / no edit |
| `mergeStateStatus` | `CLEAN` / `MERGEABLE` |

The PR was handed off as merge-ready. Only a live run against real firmware found it.

## Root cause

Three layers, and the third is the transferable one.

**1. The fixtures were constructed from prose, for an endpoint this repository had never
spoken.** The vendored reference corpus lists the *path grammar* and nothing else. Both the
`-p10` and `-p11` corpora carry one line per path at
`docs/refs/hmc-rest-api-p11/000-hmc-rest-apis.md:65-66`, naming `/rest/api/uom/{R}/quick` and
`/rest/api/uom/{R}/quick/all` and describing each as yielding a type's defined quick
properties. (That corpus is vendored IBM documentation: gitignored, local-only, and never
quoted into tracked files — cite it by path and by what a search over it returns.)

The searches that matter are the ones that come back empty. Run from `docs/refs/`:

```console
$ rg -ci QuickProperty . | awk -F: '{s+=$NF} END {print s+0}'
0
$ rg -ci QuickProperty_Collection . | awk -F: '{s+=$NF} END {print s+0}'
0
$ rg -ci Nickname . | awk -F: '{s+=$NF} END {print s+0}'
0
```

No content type, no body example, no element vocabulary — anywhere in the corpus. The JSON
assumption was inferred from a *sibling* endpoint (`/rest/api/uom/ManagedSystem/quick/All`,
capital A, which genuinely does return JSON — see `client_systems.py`) plus the reference
line's use of the word *list*, which was read as a JSON array rather than as ordinary English.
Both inferences were wrong about the endpoint actually being read.

**2. The reference was never opened, because it is invisible from a worktree.**
`docs/refs/` is gitignored — deliberately, because it is vendored IBM documentation that
cannot be committed to a public repository — and so is `AGENTS.local.md`, the only file that
says the corpus exists (`.gitignore:31-32`). This project's workflow runs in a sibling
worktree (`~/src/hmc-mcp-worktrees/<branch>/`), where **both** are absent:

```console
$ ls docs/refs/ AGENTS.local.md
ls: cannot access 'docs/refs/': No such file or directory
ls: cannot access 'AGENTS.local.md': No such file or directory
```

That reads as *"this repository has no reference corpus"*, not as *"look in the main
checkout"* — and the pointer that would have corrected the reading is missing for the same
reason as the thing it points at. The corpus would not have supplied the response shape
(layer 1), but discovering that it lists paths and *no shapes* is exactly the signal that a
live capture was required before writing a single fixture.

The licensing constraint is not the bug. Keeping vendored IBM documentation out of a public
repository is correct and non-negotiable; the bug is that nothing bridges the ignored path
into the worktree where the work happens, and a gitignored pointer to a gitignored corpus is
invisible twice over.

**3. Mutation testing cannot detect a shared-premise error, and a high mutation score was
misread as evidence that it could.** Five mutants were run against the parse and all five
died. That was recorded as "the tests bite". It was true and irrelevant:

> A mutation score measures whether the tests **discriminate between implementations**.
> It says nothing about whether the **fixture matches reality**. Every mutant is evaluated
> against the same fixture, so a fixture encoding the wrong response shape is held constant
> across the entire experiment and can never be the thing that varies.

The same blind spot covers unit tests, code review, and CI: all of them vary the code and
hold the fixture fixed. When the premise is shared by the implementation *and* the tests
*and* the reviewers' reading, no amount of rigour applied to the code can surface it. Only
evidence from outside the loop — a real response — can.

## Solution

Two live rounds against real firmware, recorded on PR #800.

**Round 1 (the negative result)** established the container and killed both assumptions.
The parse was rewritten to read `Nickname` element texts, document-wide, via a new
`xmlutil.find_all_text` (the all-matches sibling of the existing `find_text`, which returns
only the first match) wrapped as `client_parse._find_all_text` so a malformed body raises
`HMCError` naming the call:

```python
names = [n for n in _find_all_text(resp.text, f"GET {path}", "Nickname") if n]
```

`src/hmc_mcp/client/core.py`, in `list_quick_properties`. `all_properties` was removed
outright rather than documented as broken.

Reading elements directly (not via `_parse_feed`) also avoids `element_to_dict` collapsing a
repeated element to a bare value at a count of one — the hazard ADR 0139 recorded for
`OperationSet`, and reachable here because `VirtualNetwork` defines exactly one quick property.

**Round 2 (the structural capture)** replaced the still-wrong fixtures. The real document
root is `<entry>`, not the `<feed>` the fixtures had guessed, with the collection under
`<content>` and each `QuickProperty` carrying `Metadata`/`Atom`, `RESTElement`, `Nickname` and
a prose `Description`.

That round produced the sharpest finding in this record:

> **The capture-derived fixtures kill two mutants the invented ones structurally could not.**
> Mutating the parse to read `Description` or `RESTElement` instead of `Nickname` fails 7
> tests each against the real shape. Against the old fixtures — which carried `Nickname`
> *alone* — both mutants were **undetectable**, because there was no wrong sibling to read.

A fixture that omits the fields you are not reading cannot prove you are reading the right
one. `LogicalPartition` defining a property whose own *name* is `Description` makes the
discriminator sharp, so the test uses the capture's verbatim text for that pair.

Verification:

```console
$ uv run --no-sync pytest tests/unit/test_client.py -k list_quick_properties --no-cov -q
22 passed, 138 deselected

$ just verify && uv run --no-sync prek run --all-files    # both exit 0
```

Six mutants now die; none survives. The live round-trip closed the last gap the parse never
could — the first five returned names were fed back through `get_quick_property` and all five
resolved, so the strings are confirmed property *names*, not merely strings.

## Prevention

Ordered by how much each would have helped.

1. **For an endpoint this repository has never spoken, get a capture before writing a
   fixture — not after.** The prior discovery read (`/operations`, PR #797) had already
   established the two-step: ship the assumption, then `test: replace the assumed fixture
   with a live capture`. This branch repeated step one and skipped step two until firmware
   forced it. Three sibling reads are queued (#789, #790, #791) and will hit the same wall.
2. **Bridge the ignored paths into the worktree, without committing anything.** Both
   `docs/refs/` and `AGENTS.local.md` are in `.gitignore`, so a *symlink* at either path
   inside a worktree is itself ignored and can never be committed — it carries no licensing
   risk. Creating those two links at worktree setup makes the corpus reachable exactly where
   the work happens. A guide invisible from there is not consulted, and its absence is
   indistinguishable from it not existing.

   **The corpus exists on one machine only** and is not replicated to other development
   hosts, so the link step has to degrade rather than fail: link when the target is present,
   no-op when it is not, never error. On a host without the corpus the right behaviour is to
   *say so* — "reference corpus not available on this host" — because that message is itself
   the signal this record exists to deliver. A developer who is told the reference is
   unavailable knows not to infer a response shape; one who sees `No such file or directory`,
   or nothing at all, concludes there was never a reference to consult. The absence must be
   announced, not merely tolerated.
3. **Write the citation rule down in a tracked file.** ADRs 0086, 0087 and 0098 already get
   this right by instinct: cite `docs/refs/<path>:<line>` and report what a search over it
   *returns*, never reproduce its prose. Nothing states the rule, so it is followed by
   imitation — and this branch broke it in ADR 0140 before anyone noticed. Naming the ignored
   path in a public file is safe; `.gitignore:31-32` already does.
4. **Label fixture provenance in the test file, at the block head.** State whether each body
   is *captured* or *constructed*, and from what. Writing that sentence honestly forces the
   question "constructed from what, exactly?" at the moment it is cheapest to answer. Both
   `list_operations` and `list_quick_properties` blocks now carry one.
5. **Carry the fields you do not read.** A fixture trimmed to just the element under test
   cannot detect a parse that reads the wrong neighbour. Include realistic siblings —
   especially any that hold similarly-shaped values.
6. **Do not report a mutation score as fixture validation.** Say what it measures: that the
   tests discriminate between implementations, given the fixture. If the fixture is
   unverified, say that separately and just as loudly.
7. **None practical** for the reference corpus being wrong. `000-hmc-rest-apis.md:66`
   documents `/quick/all` in both the P10 and P11 corpora, and firmware answers 400. A
   vendored reference can be stale or aspirational; only firmware settles it.

Sibling record, different mechanism, same family of green-tests-proving-nothing:
`kdive/docs/solutions/2026-09-04-assertions-whose-operands-share-one-source.md` — there the
assertion could not fail; here the assertion was fine and the input was fiction. Fault
injection detects the first and is blind to the second.
