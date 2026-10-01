# Captured HMC fixtures

Each file here is one request and the HMC's answer, taken for issue #1161:
REST answers and the refcode listing from captures recorded with
`scripts/live_test/capture.py`, and two CLI listings (`cli-io-slots.json`,
`cli-lpar-uuid-name.json`) from raw read-only command output. The captures were
taken on an HMC at V10R3 managing POWER9 hardware; `docs/api-patterns.md`
records the patterns they show. CLI captures whose `capture` begins `2026-09-30-ro/` come
from the read-only sweep for #1202, on the same HMC and system; a nonzero-exit one is
raised in tests with `live_process_error(name)`. Captures whose `capture` begins
`2026-09-30-v11r2-p9/` come from the same sweep on an HMC at V11R2 SP1120 managing a
POWER9 9009-42A.

## Format

Every file is a JSON object with these keys:

- `capture`: the source record, as `<capture file>#<line>` or a named raw
  output. The raw captures stay private to the operator, so this field is the
  citation, not a link.
- A REST capture has `method`, `path`, `status`, `content_type` and `body`.
- A CLI capture has `command`, `exit_status`, `stdout` and `stderr`.

Tests read a file with `live_fixture(name)` from `tests/conftest.py`, or with
`live_response(name)`, which also returns the captured path and an
`httpx.Response` for a respx route.

## Tokenization

Identifiers are replaced consistently across files, so a value that appears in
two captures has the same token in both:

- UUIDs become `NNNNNNNN-abcd-4ef0-8abc-NNNNNNNNNNNN`, keeping the case the HMC
  printed. LPAR UUIDs are upper case, as V10R3 prints them.
- The managed system is `sys-R1`, and partition names keep their suffix
  (`sys-R1-lp3`, `sys-R1-vios1`).
- The HMC host is `hmc.test:443` (the port in an echoed `Host` header is the
  one the capture used), and the client IP address is `192.0.2.1`.
- Serial numbers, session tokens and cookies are `<REDACTED-SERIAL>`,
  `<REDACTED-SESSION>` and `<REDACTED-COOKIE>`. Inside an XML body the token is escaped
  (`&lt;REDACTED-SESSION&gt;`), so the body stays well-formed XML and parses
  as the HMC's original did.

Nothing else in a body is changed. A new or changed HMC-shaped fixture cites the
capture it came from.

## The 2026-09-30 export (#1202)

Fixtures whose `capture` begins `2026-09-30-ro/` come from a read-only sweep of
that day: every read-only MCP tool and about 70 raw GETs and `ls*` commands,
against the same V10R3 HMC and POWER9 system as the #1161 captures. The raw
records were tokenized as one corpus by the prototype of
`scripts/live_capture_export.py`, so tokens are consistent across fixtures from
that export, and the system and test partition keep the names above (`sys-R1`,
`sys-R1-lp3`).

**Its UUID token map is separate from the #1161 one.** Both use the
`NNNNNNNN-abcd-4ef0-8abc-NNNNNNNNNNNN` form, numbered from 1 in each export, so
the same token in a #1161 fixture and in a `2026-09-30-ro/` fixture can name
different objects, and the same object can carry different tokens. Never join
fixtures across the two exports by UUID. Other names in that export (`lpar-N`,
`prof-N`, `vg-N`, `dev-N`, ...) are tokens of its own map too.

`vocabulary/` holds what was derived from the 2026-09-30 corpora, not captured
records: one vocabulary and one schema enum list per HMC release and system
(`v10r3-p9.json` with `enums-v10r3-p9.json`, and the three V11R2 pairs
`v11r2-p9-9009-42a`, `v11r2-p11-9824-42a` and `v11r2-p11-9242-21b`), plus the
documented job statuses (`enums-documented-jobs.json`, names cited to
`docs/refs/`, each marked captured or documented-only) and the allowlist of
`just live-vocabulary`. Each vocabulary names its sources;
`v10r3-p9.json` also folds in the REST values and endpoints derived from the
#1161 and #879 mutation windows on the same HMC, which is where its job statuses
come from. Regenerate them with `scripts/live_capture_export.py`, as
`docs/live-testing.md` ("Capturing an HMC's vocabulary") describes; do not edit
them by hand, apart from an allowlist entry's `reason`.
