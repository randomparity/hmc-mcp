# Live capture pipeline and vocabulary gate

Issue #1202, part 2 and 3 of its proposed approach: a tracked read-only capture sweep, a
tokenizing exporter, committed per-firmware vocabularies, and an offline gate. The domain
fixes (part 1) land in their own pull requests and are out of scope here, as is any change
under `src/hmcpctl/`.

The operator approved this design on 2026-09-30, including two data decisions: raw
captures and the HMC's XSD files are never committed, only value lists derived from them.

## Problem

`src/` parses and `tests/` mocks HMC responses that were written from guesses. The
2026-09-30 read-only sweep showed the guesses wrong in dozens of places, but the sweep
itself was a set of untracked one-off scripts, and nothing stops a new guessed value from
landing tomorrow.

## Design

### Sweep driver — `scripts/live_capture_sweep.py`

`python scripts/live_capture_sweep.py --out <private-dir> [--profile P] [--system S]
[--lpar L] [--vios V]`. Never run by CI or any `just` recipe.

- **Read-only guard.** A context manager patches `HMCClient._request` and
  `asyncssh.SSHClientConnection.run` *inside* the capture harness, so a refused call
  never reaches transport and the harness still records the refusal. It admits a REST
  call only when the method is `GET`, or when it is `PUT`/`DELETE` on exactly
  `/rest/api/web/Logon` (log on and off). It admits a CLI command only when its first
  word starts with `ls` and it carries no shell metacharacter (`;|&<>$` backtick,
  newline). Anything else raises `ReadOnlyViolation` before the original is called.
- **Tools.** It lists the tools `served_client()` serves and calls every one whose
  `readOnlyHint` is true. Arguments come from a per-parameter resolver over what
  discovery found (system, running LPAR, VIOS, console, volume group, shared storage
  pool, template, user profile). A few tools are swept over declared variants
  (`hmc_list_resources` per resource type, PCM tools per category, list filters per
  state). A tool with a required parameter no resolver supplies is recorded as skipped,
  naming the parameter, so a new read-only tool is never silently missed.
- **Discovery.** When `--system`/`--lpar`/`--vios` are absent it picks the first
  operating system, a running LPAR on it, and a VIOS on it.
- **Raw probes.** A declared list of REST GETs (path template, `Accept`) and `ls*`
  commands that no tool issues, ported from the 2026-09-30 prototypes.
- **Output.** `<out>/sweep.capture.jsonl` (the harness's REST/SSH records) and
  `<out>/tools.capture.jsonl` (one record per tool call: tool, args, ok, data or error,
  or a skip). The directory is created `0700`; files are `0600` and opened without
  following symlinks; a destination git would let be committed is refused (the harness's
  own rule).

### Exporter — `scripts/live_capture_export.py`

Three subcommands, all offline:

- `tokenize RAW.jsonl... --out corpus.json [--private REGEX]...` turns harness and tool
  records into a tokenized corpus. Rules:
  - names are collected from the captures (system, partition, profile, user, host,
    volume group, device, label and description elements; `name=`-style CLI fields),
    and never collected when the value is a structural identifier (an element,
    attribute or JSON key name, or a CLI field name) or a value of any schema enum
    found in the captured `Enumerations.xsd`;
  - replacement is whole-word, longest first;
  - SSH public keys (with their comment), session tokens, cookies, MAC addresses, WWPNs,
    serial numbers, location codes, IPv4/IPv6 addresses, e-mail addresses and every
    host found in an `https://` URL become fixed tokens;
  - the `www.ibm.com` and `www.w3.org` XML namespace hosts are preserved;
  - UUIDs map to `NNNNNNNN-abcd-4ef0-8abc-NNNNNNNNNNNN` in the case printed;
  - **fail closed:** after tokenizing, the run exits 1 without writing when any collected
    name, URL host, `--private` pattern match, IP address or location code survives.
- `vocabulary CORPUS --enums ENUMS --firmware F --out V.json` derives a vocabulary.
- `enums CORPUS --firmware F --out E.json` derives the schema enum lists from the captured
  `Enumerations.xsd` record, plus element-to-enum bindings stated in any captured schema.

### Committed data — `tests/fixtures/live/vocabulary/`

- `<firmware>-<family>.json` (first: `v10r3-p9.json`): `rest.endpoints` (method, path
  template, `Accept`, status, response content type); `rest.values` (per XML element
  local name, sorted observed literals or shape classes `<int>`, `<float>`, `<empty>`,
  `<uuid>`, `<text>`); `rest.element_enums` (element to enum type: stated by a captured
  schema, else the only enum whose values contain every observed literal);
  `rest.errors` (path template, status, error form, reason code, message with
  identifiers templated); `cli.commands` (command template, exit statuses, HSCL codes);
  `cli.values` (`<command key> :: <field>`). Name-bearing fields and elements are always
  shape classes. The builder runs the same fail-closed scan on its output.
- `enums-<firmware>.json`: `{firmware, types: {Type.Enum: [values]}, elements: {...}}`.
- `allowlist.json`: gate violations not yet fixed.

The first vocabulary comes from the 2026-09-30 tokenized corpus and its enum list.

### Gate — `scripts/check_live_vocabulary.py`, `just live-vocabulary`

Offline; a `static` member with its matching prek hook. Over `git ls-files src tests`
(excluding `tests/fixtures/live/`, which is captured already, and the gate's and
exporter's own test modules, which hold uncaptured values on purpose):

1. **Literals.** XML leaf values in string literals (`.py`) and in `.xml`/`.json` files,
   and AST comparisons and dict entries keyed by an element name
   (`x.get("PartitionState") == "running"`, `in (...)`, `{"PartitionState": "..."}`,
   with `.lower()`/`.upper()` honoured). Checked only for elements whose set is closed:
   bound to a schema enum. Allowed values are that enum's values plus every observed
   literal across all vocabularies.
2. **REST read coverage.** Every `/rest/api/...` template in `src/hmcpctl` (f-strings,
   placeholders as one path segment) used by a function that issues a GET, plus
   `list_uom`/`get_uom`/`list_child`/`get_quick_property`/`search_uom` call sites with
   literal types, must match a captured endpoint. `/do/` job paths and Logon are
   excluded.
3. **CLI coverage.** Every `ls*` command template in `src/hmcpctl` must match a captured
   command on command name, `-r`, `--rsubtype`, `--level`, `-o` values and the presence
   of `-m`.

Violations not yet fixed live in `allowlist.json`; each entry names file, kind, subject
and value and carries a reason containing `#1202`. The gate fails on a violation that is
not allowlisted, on an entry with no `#1202` reason, and on a stale entry that matches
nothing. `--write-allowlist` regenerates the file from the tree, keeping existing reasons.

## Failure model

1. **Actors.** The operator running the sweep and exporter on the lab host; CI and
   developers running the gate, which never reaches an HMC.
2. **Invariants.** No mutating HMC call from the sweep; no hostname, IP, serial, location
   code, lab name or credential in a committed vocabulary; the gate is deterministic
   offline.
3. **Accepted failure classes.**
   - A name the exporter does not collect and that matches no generic pattern survives
     tokenization; vocabularies keep only shape classes for name-bearing fields, so it
     does not reach a committed file through them.
   - The harness replaces a body naming a secret keyword wholesale, so such bodies are
     absent from a sweep.
   - Elements with no schema enum (job `Status` included) are not value-checked until a
     capture or binding closes their set.
   - Paths built outside the recognised helpers, and values compared through an
     intermediate variable, are not seen by the gate.
4. **Rejected.** Committing raw captures or the XSD (operator decision); a hand-written
   allowlist (it is generated from the tree).
