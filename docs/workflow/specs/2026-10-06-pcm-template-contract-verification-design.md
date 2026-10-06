# Verify existing PCM and partition-template contracts (V10)

Issue #634 (epic #620, entry V10; child epic #1243).

## Problem

Nine registered operations in this slice have no maturity record: `pcm.get_preferences`,
`pcm.set_preferences`, `metrics.processed_links`, `metrics.processed`,
`metrics.aggregated_links`, `metrics.aggregated`, `template.list`, `template.get` and
`template.deploy`. Their `row_ids` in `docs/capabilities/operations.json` bind every PCM row
(thresholds, energy, LTM, `chpcm`/`lspcm`), not the endpoints the handlers request.

The live harness exercises them only through non-promoting paths, and it treats outcomes that
are not limitations as limitations:

- **Stale "unlicensed" declarations.** ST5 and ST12 declare `406`, `403` and the bare token
  `PCM` as "PCM not licensed", and `406` as "templates not licensed". A 406 is a media-type
  refusal, which `operations/error_translation.py` already reports as an hmcpctl defect
  (#1202). A 403 is missing PCM authority on the connecting user, a prerequisite rather than a
  licence. The bare `PCM` token matches any message that mentions PCM, including a real
  failure.
- **Undisclosed system-wide toggle.** ST12, on round2's default path, flips the managed
  system's `LongTermMonitorEnabled` every run, records it with `state.record` (no assertion),
  and restores only that one flag. Preflight does not mention it. The harness also reads the
  flag as `long_term_monitor` first, a key the tool never returns.
- **Metric feeds answer 406 (defect).** On the boundary system (V10R3 M1060), preferences read
  `LongTermMonitorEnabled=true`, `AggregationEnabled=true`, `EnergyMonitorEnabled=true`, yet
  `hmcpctl metrics show ManagedSystem <sys>` fails with hmcpctl's own "refused the media type …
  (HTTP 406)". `PcmMixin.get_metrics_feed` issues the ProcessedMetrics / AggregatedMetrics /
  LongTermMonitor feed GET through `_get`, which sends the generic UOM Accept
  (`application/vnd.ibm.powervm.uom+xml`) and, when configured, `X-HMC-Schema-Version`. The
  captured 403 fixture `tests/fixtures/live/rest-pcm-metrics-403.json` records that exact Accept
  on a metrics request. The reference documents `application/xml` for these feeds
  (`docs/refs/hmc-rest-api-p10/performance-and-capacity-monitoring/170-processed-metrics-for-managed-system.md:40`,
  `159-aggregated-metrics-for-managed-system.md:46`), and #1202 captured V10R3 answering
  `application/xml` and the UOM type with 406 on the sibling preferences endpoint while
  serving `*/*`. The preferences GET was moved to `raw_get` (`Accept: */*`) then; the feed GET
  was not.

## Operations in scope

| Operation | Request issued | Rows bound | Implemented variant |
|---|---|---|---|
| `pcm.get_preferences` | `GET /rest/api/pcm/ManagedSystem/{uuid}/preferences` | `rest:…/managed-system-pcm-preferences` | `managed-system` |
| `pcm.set_preferences` | `POST` same path | same | `managed-system` |
| `metrics.processed_links` | `GET …/ProcessedMetrics?StartTS…` | `rest:…/processed-metrics-for-managed-system`, `rest:…/processed-metrics-for-logical-partition` | `managed-system`, `logical-partition` |
| `metrics.processed` | feed GET, then the newest JSON link | same | same |
| `metrics.aggregated_links` | `GET …/AggregatedMetrics?StartTS…` | `rest:…/aggregated-metrics-for-managed-system`, `rest:…/aggregated-metrics-for-logical-partition` | same |
| `metrics.aggregated` | feed GET, then the newest JSON link | same | same |
| `template.list` | `GET /rest/api/templates/PartitionTemplate` | `rest:template-library` | `library` |
| `template.get` | `GET …/PartitionTemplate/{uuid}` | `rest:template-library` | `by-uuid` |
| `template.deploy` | `POST …/PartitionTemplate/{uuid}/do/deploy` | `rest:template-library`, `rest:template-library/template-rest-job-api` | `draft-deploy` |

Excluded, with owners: template capture/check/transform/delete (#644); raw LTM/STM, energy,
SSP and threshold rows (#649–#652); LPAR metrics dynamic parameters (#638); `template.deploy`
positive path (#644, recorded as a gap); editing #108/#110/#111 (maintainer); PCM
authority/role changes (operator).

## Design

1. **Feed Accept (defect fix).** `PcmMixin.get_metrics_feed` fetches through
   `raw_get(path)` (`Accept: */*`, no schema-version header), exactly as
   `get_pcm_preferences` does. One client method covers the processed, aggregated and LTM
   feeds. `fetch_json` keeps `Accept: application/json`, which no capture contradicts; the live
   data fetch is the check. A unit test asserting the feed request's Accept is `*/*` fails
   first.
2. **Declarations.** The three "unlicensed" PCM declarations become one authority declaration
   per operation (`error_codes={"403"}`, variant `pcm-authority`, reason "the connecting
   user lacks PCM authority (HTTP 403)"). The template declaration is removed: the only
   outcome it matched was the 406 #1202 fixed, and a recurrence must fail.
3. **ST5 (round2, read-only)** records through `record_verified` when the call returned, and
   through `record_with_expected` otherwise (refusals stay non-promoting):
   - `pcm.get_preferences` — `five-flags-boolean` (all five fields present and boolean).
   - `metrics.processed_links` / `metrics.aggregated_links` over the last two hours —
     `links-returned` (non-empty), `links-name-feed-and-system` (each href names the feed kind
     and the system UUID, case-insensitively).
   - `metrics.processed` / `metrics.aggregated` over the same window, only after links were
     returned — `document-names-system` (`systemUtil.utilInfo.uuid` equals the system UUID),
     `samples-present` (`utilSamples` non-empty). An empty `{}` result (no sample retained)
     records a SKIP naming the prerequisite instead of a promoting row.
   - `template.list` — `entries-are-partition-templates` (non-empty; each entry's
     `ResourceType` is `PartitionTemplate` and carries a UUID). `template.get` on the first
     listed UUID — `template-identity-matches`. No template → SKIP.
   - The system UUID is `artifacts.system_uuid` when ST1 set it; otherwise one
     `hmc_get_system` read, recorded as a non-promoting row, supplies it. No UUID → the
     metric rows SKIP.
4. **ST12** keeps job inspection only; its PCM toggle is removed from round2.
5. **ST38, arm `pcm` (opt-in).** Group `pcm: [38]`, not in `all`, dispatched by
   `scripts/live_pcm.py` (tested by `tests/scripts/test_live_pcm.py`), disclosed by a
   `_pcm_verdict` in preflight: "PCM preferences on managed system X: each of the five
   collection flags toggled, then all five restored to the pre-run read". The scenario:
   1. read the snapshot; all five flags must be boolean, otherwise SKIP with nothing changed;
   2. for each flag: set it to the opposite value; read back; then restore **all five** to the
      snapshot in one call (the HMC couples flags — enabling aggregation enables LTM) and read
      back;
   3. a final read must equal the snapshot.
   One `record_verified` observation for `pcm.set_preferences` with assertions
   `<flag>-toggled` per flag that read back flipped, `snapshot-restored`; `cleanup` is
   `passed` only when the final read equals the snapshot, otherwise `failed` and a
   `MANUAL RECOVERY REQUIRED` row naming the five original values. Intermediate reads and
   writes are non-promoting `state.record` rows.
6. **Catalog.** Rebind rows as tabled. Add nine maturity records: implementation state and
   scope as tabled (`template.deploy` `partial`, missing `draft-deploy` with no
   confirmation), evidence copied from the run's observations file, gaps from its gap file.
   Regenerate `src/hmcpctl/_operation_maturity.json` and `docs/tools/`.
7. **Docs.** `docs/live-testing.md` gains the `pcm` arm row, a section with the manual restore
   (`hmc_set_pcm_preferences` with the five original values), and the recovery note
   (recovery does not witness ST38: exit 2 expected; compare a preferences read before and
   after). `CHANGELOG.md` records the feed Accept fix and the arm.

## Failure model

1. **Actors and deployments**
   - CI and developer workstations run the unit suite (no HMC).
   - A local operator runs the live arms against one lab HMC per `docs/live-testing.md`.
   - `hmcpctl` / MCP users of the metrics tools against V10R3 and V11R2 HMCs.
2. **Invariants and assets at stake**
   - The managed system's five PCM collection preferences (system-wide, shared with every
     other PCM consumer): they must equal the pre-run read when ST38 ends.
   - Catalog truth: no observation promotes without assertions over returned data; a refusal
     or prerequisite never promotes.
   - Generated docs and the runtime projection stay in step with the catalog.
   - Public evidence carries no lab identifiers.
3. **Accepted failure classes**
   - A partial toggle the HMC refuses or couples (aggregation enables LTM): accepted — the
     restore writes all five snapshot values and the final read decides cleanup.
   - An interrupted ST38 leaving a flag changed: accepted at bounded cost — the snapshot is
     printed before the first write and the runbook names the manual restore.
   - A V11R2 or other firmware answering `*/*` differently from V10R3: accepted; the live
     check runs on the boundary system only and other levels surface as FAIL rows.
   - Empty aggregated/processed data in the window: SKIP with the prerequisite, never a pass.
4. **Covered elsewhere**
   - Raw LTM/STM, energy, SSP and threshold endpoints — #649–#652.
   - Template capture/transform/deploy positive path — #644.
   - LPAR metric dynamic parameters — #638.
   - Observation staleness after a shared `src/` change — ADR 0127 (derived, reported).

## Verification

- Unit: feed Accept `*/*` (red first); declarations no longer match 406 or bare `PCM`; ST5
  assertion paths (pass, empty data → SKIP, 403 → gap); ST12 makes no `hmc_set_pcm_preferences`
  call; ST38 toggles each flag, restores all five, fails cleanup on a mismatched final read,
  SKIPs on a non-boolean snapshot; preflight lists the PCM mutation; wrapper dispatches
  `--group pcm`.
- Live (boundary system, V10R3): preflight; `live_test_runner.py 5`; `live_pcm.py`; recovery
  for each; a five-flag read before and after.
