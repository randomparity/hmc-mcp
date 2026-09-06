# CLI/REST capability inventory design

Status: approved by the maintainer in the active quest conversation on 2026-09-06.
Authority: issue #621, parent #620 requirements 1–5, and frozen scope annotation
`q621-b3084fca` at https://github.com/randomparity/hmc-mcp/issues/621#issuecomment-5558655309.

## Outcome

Produce the finite reference inventory at operation, mode and parameter granularity.
Every reference capability maps to an existing supported implementation, a concrete
coverage child, or a proposed exclusion. Every registered operation maps back to
reference capabilities or an explained repository-specific composite. Unknowns remain
visible and prevent a full-coverage claim.

F1 owns reference scope and implementation links. F2 (#622) owns maturity and promotion
policy; F3 (#623) owns runner evidence; F4 (#624) owns discovery integration. This change
does not add HMC operations or change runtime authorization, admission or registry behavior.

## Observed inputs

The supplied Markdown corpora are available locally under the ignored `docs/refs/`
directory; original copies and four tar archives are retained outside the checkout.

| Corpus | Captured content pages | Additional local documents |
| --- | ---: | --- |
| POWER10 commands | 181 | index and overview |
| POWER11 commands | 179 | index and overview |
| POWER10 REST | 219 | index |
| POWER11 REST | 247 | index |

The 826 captured pages match #620's stated counts. Local navigation documents are
accounted for separately; they do not enlarge the operation denominator. REST content
includes overview and schema topics, so 466 REST pages do not mean 466 operations.
All four index files carry explicit capture timestamps. Only the two command overview
files lack them; record that absence, never a filesystem modification time or an invented
capture time. Source pages carry IBM URLs, and captured content carries timestamps. Accept
the index files' capitalized `Captured:` metadata as well as front matter `captured:`.
Record each original file's SHA-256.

## Proposed representation

Use UTF-8 JSON artifacts under `docs/capabilities/`, with an explicit integer format
version. Keep records separate by responsibility, with no database or new dependency:

- `corpora.json`: four corpus identities, source roots, original archive SHA-256 values,
  and the complete topic and source-unit manifest. A topic contains its corpus-relative
  path, source URL, capture timestamp or explicit unknown, file SHA-256, and classification
  (`operation`, `schema`, `overview`, or `navigation`) with a reason. Its source units
  enumerate every structured semantic candidate in source order: command synopsis lines,
  option/attribute table rows, REST resource blocks, method rows, property/field rows, job
  names, and version/capability statements. Each unit carries a stable ID, kind, source
  line, and normalized text hash.
- `rows.json`: stable capability IDs and their semantic units. Each row contains an
  operation, explicit mode/selectors, parameter names and constraints, cited topic IDs
  and source locations, documented release/capability prerequisites or explicit unknown,
  and a disposition. Different transport spellings may share a row only with an explicit
  equivalence explanation grounded in both sources. Distinct behavior stays distinct.
- `operations.json`: every current registry operation ID, linked capability IDs and
  supported callable/CLI/MCP paths, relevant existing test references, and any explained
  repository-specific composite. A reference to a test is an index, not passing evidence.

Each row disposition is `supported`, `coverage-child`, `proposed-exclusion`, or `unknown`.
`supported` requires exact implemented parameter/mode scope and a supported installable
surface; partial support is represented by separating supported and uncovered scope.
`coverage-child` requires an existing issue number and the criterion it owns.
`proposed-exclusion` requires its reason and owner and stays in scope until explicit
approval. `unknown` requires the unresolved question; it cannot silently become supported.
No arbitrary-command wrapper or generic transport method counts as dedicated coverage.

POWER10/POWER11 presence is recorded through corpus citations. Absence from one corpus
means only absence from that snapshot, never an unsupported firmware version. Explicit
source constraints are recorded separately from document generation names.

## Validation and data flow

Add one repository script, `scripts/check_capability_inventory.py`, and its matching
test module `tests/scripts/test_check_capability_inventory.py`. Use standard-library JSON,
hashing and path handling; reuse existing registry discovery for operation IDs.

The default offline validation checks version/shape, duplicate IDs and JSON keys,
references, dispositions, prerequisite unknowns, complete topic accounting, and exact
source-unit accounting. Every source unit must map to exactly one capability row or an
explicit non-operation classification. An added or removed registered operation fails with
an actionable record ID until reconciled. Unknown rows are allowed in the honest ledger but
listed in the report; they cannot produce a complete-coverage verdict. The checked-in unit
manifest is regenerated from the same retained corpus bytes during explicit source
verification, so deleting a real mode or parameter unit from both the manifest and rows fails.

An explicit corpus-verification invocation accepts four named corpus directories and checks
all and only each manifest subroot's source files, byte hashes, and derived source units. It
rejects path traversal and non-regular source files, reports missing/extra/changed files,
and never downloads, executes or rewrites a source. Unrelated siblings outside those four
subroots are outside the check. Ordinary CI needs only the checked-in artifacts; this quest
must also run the corpus arm against the four actual supplied directories from the source
checkout because ignored corpora are not copied into a Git worktree.

For implementation joins, the validator discovers a structured view of every registered
tool: tool name, operation ID, unwrapped handler module/name/signature, and registered MCP
surface. It validates every claimed module and test path as a regular repository file.
Each `supported` mapping accounts for every source parameter as a real handler parameter,
an explicit constant/default, or a cited translation in an existing implementation path.
The validator mechanically checks real handler parameters and paths; explicit constants and
translations remain reviewable assertions. CLI exposure is recorded only when a concrete CLI
registration can be discovered and validated; otherwise the ledger makes no CLI claim.

Add a `capability-inventory` recipe to `static` and the corresponding prek hook. The
validator prints separate structural-validity and implementation-completeness results.
Structural validity does not assert semantic correctness, test success, or live maturity.
Review must inspect the source-to-row decomposition and equivalence judgments directly.

The manifest is derived from the source corpus, independently of operation mappings.
Do not derive the reference denominator from the current registry or a list of implemented
tools. The source-to-row review covers modes and nested parameter/attribute lists, not
just command headings and REST method tables.

## Verification

Focused tests exercise missing/extra topics and source units, duplicate IDs/keys, dangling
references, missing registry operations, nonexistent handlers/tests/surfaces, handler
signature mismatches, malformed supported/disposition records, unsafe paths and hash
mismatches. Fault injection must show the tests reject omitted real-shape modes/parameters,
topics, registry operations, and supported-surface evidence. A corpus fixture with multiple
modes and nested parameters proves that topic-level accounting alone cannot substitute for
the source-unit and capability rows. Fixtures cover both lowercase front matter `captured:`
and the navigation indexes' capitalized `Captured:` form; only genuinely absent metadata
becomes explicit unknown.

Run the real validator against the committed inventory and current registry, then verify
all four retained corpora. Run `just verify` and `uv run --no-sync prek run --all-files`
before shipping. CI targets amd64 and arm64 on Python 3.11–3.14. This local host is
x86_64 on Python 3.11; local success covers that arm only. No hardware test is required
or authorized by this inventory change.

## Alternatives

- **Checked-in JSON and one validator (recommended):** reviewable diffs, stable IDs and
  offline CI; requires deliberate source-to-row curation and review.
- **Markdown tables only:** easier prose editing, but weak joins and structural checks
  for the F2/F4 consumers; large mode/parameter records are awkward to maintain.
- **A database or runtime registry extension:** introduces storage/runtime behavior and
  coupling that F1's offline inventory does not need.

## Publication boundary

Publish source URLs, timestamps, hashes, parameter names and concise semantic summaries.
Do not copy complete IBM pages or publish raw local paths, credentials or identifiers
from source examples. Preserve original corpora locally for source verification. Scan
the entire authored artifact set for private identifiers before any push.
