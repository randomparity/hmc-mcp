# Encoded uom path values carry a length bound

Issue #827. Lane: light-spec. Record: `docs/adr/0150-encoded-uom-path-values-are-bounded.md`.

## Problem

`search_uom` percent-encodes `property_value` and `UsersMixin._child_path` percent-encodes
`console_uuid` into a uom request path. Encoding makes a value safe, not small: `quote` emits three
characters per UTF-8 byte and a character can be four bytes, so one accepted character becomes up
to twelve on the wire.

Reproduced at `101f2117` on the locked httpx 0.28.1 with a stubbed transport:
`search_uom("ManagedSystem", "SystemName", "A" * 65_000)` sends a 65,065-character URL and
`list_hmc_users("A" * 10_000)` a 10,060-character one. The only thing stopping either is
httpx's `MAX_URL_LENGTH = 65536`, which since ADR 0148 surfaces as an `HMCError` naming neither the
argument nor the size. Because that ceiling applies to the *encoded* URL, the input length at which
it fires is not a constant: `"\U0001F600" * 6_000` is already refused where `"A" * 65_000` is sent.

## Scope

In: a shared bound and predicate in `client_contracts.py`; one call each in `search_uom`
(`core.py`) and `_child_path` (`client_users.py`); tests; ADR 0150.

Out, each a follow-up candidate: `search_uom`'s `property_name`; `get_hmc_user`'s
`user_profile_uuid`; `get_remote_access` and `configure_remote_access`, which encode
`console_uuid` without `_child_path`; `operations/updates/service.py`'s `console_uuid`. Out by the
charter: `client_lpm._lpar_job`'s `operation` (#828), ADR 0147's other unclassified segments, and
UUID *shape* validation (#262/#278) — this bounds length only.

### Failure model

- **A legitimate value is refused** — the expensive direction ADR 0147 records, since neither
  argument has an authoritative maximum here. Mitigated by deriving the number from a wire budget
  rather than fitting it to observed values, and by keeping the remedy one constant.
- **The refusal is unattributable** — the failure ADR 0148 records at the waist. Mitigated by
  refusing per-argument, as `ValueError`, before any request is built.
- **The refusal leaks the value**, which carries an operator's own resource names. Mitigated by
  naming the argument and the two lengths only, the rule `_reject_unknown_uom_type` follows.
- **The bound is read as closure.** `property_name` stays unbounded and `console_uuid` is bounded
  on seven of nine `UsersMixin` entry points. Mitigated by stating both in ADR 0150.

## Success

1. A `property_value` or `console_uuid` over the bound raises `ValueError` before any request is
   built, naming the argument and both lengths and not the value.
2. A value at exactly the bound is accepted and still sent — the bound is inclusive.
3. The encoded contribution is capped at a constant, whatever characters the caller passed.
4. No wire-format change for any value this repository passes; no test input is newly refused.

## Validation

`just verify` and `uv run --no-sync prek run --all-files`, both bare, exit 0.

Contract inventory — one `focused-test` entry: the bound and its two call sites, tested in
`tests/unit/test_request_path_safety.py` beside the ADR 0147 bound section. Cases: the inclusive
edge accepted and sent; the first refused length for each argument, raising `ValueError` with no
request sent; the message naming the argument and both lengths but not the value; and the
worst-case encoded expansion. Expected red before the predicate exists: `ImportError` for
`_MAX_UOM_PATH_VALUE_LENGTH`. Green:
`uv run --no-sync pytest tests/unit/test_request_path_safety.py --no-cov -q`.
