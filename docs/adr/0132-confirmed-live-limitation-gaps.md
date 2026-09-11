# ADR 0132: Record confirmed live limitations as missing scope

## Status

Accepted

Amends ADR 0127 only by adding a separate limitation channel. Its evidence shape,
currency and promotion rules remain unchanged.

## Context

Issue #769 requests durable gaps for declared limitations confirmed by the live
runner. Expected outcomes also include transient states such as an already-deleted
repository; remembering those as limitations could suppress necessary cleanup.

## Decision

Maturity format 3 adds optional `confirmation` to an implementation `missing_scope`
entry. Its closed shape is `tested_commit`, `observed_at`, `hmc_release`,
`hardware_family`, and `closure_fingerprint`, using ADR 0127's existing grammars.
Existing entries need no confirmation, and evidence keeps its existing shape.
Confirmed entries require `parameters: []`; constrained unconfirmed scopes stay valid.
An implemented scope cannot carry confirmation. A gap is not evidence and cannot
promote verification or change implementation state automatically.

Every ExpectedOutcome declares a registered operation and a closed-shape variant
token. Firmware/license/endpoint limitations are durable; explicitly transient
outcomes remain ordinary skips. Invalid dispatches never become gaps. Declaration
identities are checked before hardware execution, including tool/operation agreement.
Preflight inspects the bounded literal declared dispatch sites and resolves their
module-level expectation constants; an unreadable declaration fails closed.

The runner writes confirmed limitations as operation-keyed `missing_scope` rows
alongside, but structurally separate from, evidence rows in its ignored observation
output. A maintainer reviews and copies the gap into maturity.json; the runner never
edits the catalog. No raw error, credentials or hardware identifiers enter this row.

A run recognizes a recorded gap only for the same declared operation and variant,
the same HMC release and hardware family, an unchanged operation import closure,
and an age from zero through 90 days inclusive. Other records remain historical
gaps but do not suppress a new attempt. Missing environment labels also disable
reuse. The operator removes a confirmation to force immediate revalidation.
Reusing a gap returns SKIP without a new observation or refreshed timestamp.
Transient outcomes never suppress a future call. Dispatch validation precedes reuse.
ST11 user-list calls explicitly disable reuse: listing supplies the UUID needed to
delete a user created during that scenario. Real limitations still produce gaps.

## Consequences

Existing format-2 catalogs move to format 3 without changing their records. Readers
of format 2 must upgrade before accepting confirmations. Catalog review remains the
trust boundary; environment labels denote release/family, not universal firmware
or per-machine proof. A bounded 90-day cache can conceal a newly fixed limitation
until revalidation; the explicit removal path permits an earlier check.
Implementation metadata and runtime eligibility retain their current behavior.

## Considered & rejected

- **Never expire gaps.** judgment: a fixed limitation would suppress its own
  rediscovery indefinitely when firmware changes without a new coarse release label.
- **Expire only on HMC release.** judgment: licenses and implementation changes can
  remove limitations while the release label remains unchanged.
- **Remember transient outcomes too.** verified: scripts/live_test/vmedia.py at
  11892cc4 declares already-powered-off and already-gone cleanup conditions; those
  resource states can change during a subsequent lifecycle run.
- **Store failed evidence or promote a matching skip.** verified: ADR 0127 permits
  promotion only for current passed observations with verified assertions.
- **Do nothing.** verified: record_with_expected at 11892cc4 only records SKIP;
  no missing-scope output reaches the manually maintained catalog.
