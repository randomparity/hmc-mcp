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
  following symlinks. Both must lie outside every git work tree.
- **Raw recording.** `capture(path, raw=True)` in `scripts/live_test/capture.py` keeps
  response bodies, command output and exception text whole even when they name a
  secret keyword (the default replaces such text wholesale, which blanked user-profile
  and console bodies); logon exchanges, request bodies, commands, session headers and
  echoed session values are still redacted, and a raw destination inside any git work
  tree is refused. The default mode is unchanged. The sweep records raw; the exporter
  does the redaction.

### Exporter — `scripts/live_capture_export.py`

Three subcommands, all offline:

- `tokenize RAW.jsonl... --out corpus.json [--private REGEX]...` turns harness and tool
  records into a tokenized corpus. Rules:
  - names are collected from the captures (system, partition, profile, user, host,
    volume group, device, label and description elements; `name=`-style CLI fields),
    and never collected when the value is a structural identifier (an element,
    attribute or JSON key name, or a CLI field name), a value of any schema enum
    found in the captured `Enumerations.xsd`, or an HMC sentinel or message
    (`No results were found.`, `null`, `none`, `N/A`, `unavailable`, `Unknown`,
    `HSCL…` text);
  - replacement is whole-word, longest first;
  - SSH public keys (with their comment, up to the next `<`), the
    `PublicSSHKeyValue`/`AuthorizedKeysValue` elements whole, session tokens, cookies,
    MAC addresses, device identifiers (`VolumeUniqueID`, `UniqueDeviceID`,
    `MediaUDID`, `DescriptorPage83`, `UDID`, WWPN elements, `unique_id=`, `udid=`, `wwpn=`,
    `serial_num=`), serial numbers, address elements (`IPAddress`, `NetworkAddress`,
    …) whole, IPv4 and IPv6 addresses (but not colon-separated SR-IOV records of short
    decimal fields), e-mail addresses and every host found in an `https://` URL
    become fixed tokens;
  - a location code is replaced wherever it appears, even inside a DRC name, and only
    its `U….….…` prefix: the generic slot suffix stays;
  - replacement strings write `\g<1>`: `\1` before a digit is an octal escape;
  - the `www.ibm.com` and `www.w3.org` XML namespace hosts are preserved;
  - UUIDs map to `NNNNNNNN-abcd-4ef0-8abc-NNNNNNNNNNNN` in the case printed;
  - **fail closed:** after tokenizing, the run exits 1 without writing when any collected
    name, URL host, `--private` pattern match, IP address or location code survives,
    or when a body that parsed as XML no longer parses.
- `vocabulary CORPUS --enums ENUMS --firmware F --source TEXT [--fold D --fold-source
  TEXT]... --out V.json` derives a vocabulary; `--fold` adds the REST values and
  endpoints of an earlier derived vocabulary of the same pair.
- `enums CORPUS --firmware F --out E.json` derives the schema enum lists from the captured
  `Enumerations.xsd` record, plus element-to-enum bindings stated in any captured schema.

### Committed data — `tests/fixtures/live/vocabulary/`

- one `<release>-<family>[-<model>].json` per HMC release and system (`v10r3-p9`,
  `v11r2-p9-9009-42a`, `v11r2-p10-9028-21b`, `v11r2-p11-9824-42a`, `v11r2-p11-9242-21b`), naming its
  `sources`: `rest.endpoints` (method, path
  template, `Accept`, status, response content type); `rest.values` (per XML element
  local name, sorted observed literals or shape classes `<int>`, `<float>`, `<empty>`,
  `<uuid>`, `<text>`); `rest.element_enums` (element to enum type: stated by a captured
  schema, else the only enum whose values contain every observed literal);
  `rest.errors` (path template, status, error form, reason code, message with
  identifiers templated); `cli.commands` (command template, exit statuses, HSCL codes,
  one-line sentinels, and which streams carried output on a nonzero exit: V10R3 prints
  its HSCL error on stdout with stderr empty);
  `cli.values` (`<command key> :: <field>`). Name-bearing fields and elements are always
  shape classes. The builder runs the same fail-closed scan on its output.
- `enums-<pair>.json`, one per vocabulary: `{firmware, types: {Type.Enum: [values]},
  elements: {...}}`.
- `enums-documented-jobs.json`: the job statuses `docs/refs/hmc-rest-api-p10/016-job-status.md:16-27`
  documents, plus the bare `COMPLETED` that the ListManagementConsoleUpdates and
  ListStorageMediaDevices job pages document (`…/091-…md:39`, `…/092-…md:41`), each
  marked captured or documented-only, binding `Status`. Names only, no prose.
- `allowlist.json`: gate violations not yet fixed.

The first five come from the 2026-09-30 tokenized corpora. `v10r3-p9` also folds in
the vocabulary derived from the #1161 and #879 mutation windows on the same HMC, the
only source of job statuses.

### Gate — `scripts/check_live_vocabulary.py`, `just live-vocabulary`

Offline; a `static` member with its matching prek hook. Over `git ls-files src tests`
(excluding `tests/fixtures/live/`, which is captured already, and the gate's and
exporter's own test modules, which hold uncaptured values on purpose):

1. **Literals.** XML leaf values in string literals (`.py`) and in `.xml`/`.json` files,
   and AST comparisons and dict entries keyed by an element name
   (`x.get("PartitionState") == "running"`, `in (...)`, `{"PartitionState": "..."}`,
   with `.lower()`/`.upper()` honoured). Checked only for elements whose set is closed:
   bound to a schema enum in some vocabulary. Allowed values are the union, over every
   vocabulary and enum list, of that element's enum values and observed literals: a
   value any release answered or enumerates passes.
2. **REST read coverage.** Every `/rest/api/...` template in `src/hmcpctl` (f-strings,
   placeholders as one path segment) used by a function that issues a GET, plus
   `list_uom`/`get_uom`/`list_child`/`get_quick_property`/`search_uom` call sites with
   literal types, must match a captured endpoint. `/do/` job paths and Logon are
   excluded.
3. **CLI coverage.** Every `ls*` command template in `src/hmcpctl` must match a captured
   command on command name, `-r`, `--rsubtype`, `--level`, `-o` values and the presence
   of `-m`.

A Python line holding a value that is deliberately not an HMC answer ends with
`# live-vocabulary: allow <reason>`; an exemption without a reason, or one that
suppresses nothing, fails the gate. Violations not yet fixed live in `allowlist.json`,
which can reach empty; each entry names file, kind, subject
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
   - A raw sweep holds secret-bearing answers on the operator host until the exporter
     runs; the directory is `0700`, outside every repository, and never committed.
   - Elements with no schema enum and no documented list are not value-checked until a
     capture or binding closes their set.
   - Paths built outside the recognised helpers, and values compared through an
     intermediate variable, are not seen by the gate.
4. **Rejected.** Committing raw captures or the XSD (operator decision); a hand-written
   allowlist (it is generated from the tree).
