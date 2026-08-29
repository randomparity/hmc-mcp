# ADR 0071: Bulk LPAR Ownership Read over the REST List Feed

## Status

Accepted (2026-08-22)

## Context

Reading LPAR ownership means reading the partition description, where ADR 0011
stamps the advisory ownership token (`[hmc-mcp owner:<agent_id>
created:<date>]`, optionally followed by an ADR 0064 caller segment). Until
now the only read path was `get_lpar_description` (SSH `lssyscfg -r lpar
--filter lpar_names=<lpar> -F description`) — one full SSH login per
partition. An estate sweep was O(LPARs) HMC logins per pass, and its docstring
claimed "the description is not exposed via the HMC REST API", which made a
bulk REST design look impossible.

Issue #375 sketched an SSH bulk form (`lssyscfg -F name,description`, one SSH
login per managed system) but gated it on #374: is the description readable
over REST at all?

The #374 live-REST survey (2026-08-22, across FW950/fw1110 generations)
answered definitively:

- The `<Description>` element is exposed over REST **and fully inlined in the
  bulk list feed** `GET /rest/api/uom/ManagedSystem/<uuid>/LogicalPartition` —
  each `<entry>` carries the complete LogicalPartition object, so one list
  call covers every partition with no per-partition detail calls.
- Values are byte-for-byte identical to the SSH `-F description` output; the
  ownership-token characters (`[`, `=`, `:`) round-trip unescaped.
- An empty description is signaled by **element absence**, not an empty
  element.
- The attribute has been present since REST schema version V1_2_0
  (`ksv="V1_2_0"`).

## Decision

`operations.lpar` gains one presentation-neutral async operation:

```python
async def list_lpar_ownership(
    hmc: HMCClient,
    system_name_or_uuid: str | None = None,
) -> list[dict[str, Any]]
```

It reads the REST bulk list feed — one request per named managed system, or
one fleet-wide `GET /rest/api/uom/LogicalPartition` when the selector is
omitted, mirroring the `hmc_list_lpars` selector convention (ADR 0063) — and
returns one dict per partition:

- `lpar_name`, `lpar_uuid` — identity;
- `description` — raw Description text; `None` when the element is absent;
- `owned` / `owner` — set when `parse_lpar_ownership_owner` finds a
  well-formed ADR 0011 stamp;
- `unparsed` — a description is present but carries no such stamp.

**Parse-failure honesty policy.** No partition is ever dropped from the
result. "No description" (`description=None`), "description without an
hmc-mcp token" (`unparsed=True`), and "owned by `<agent>`" are three distinct
facts; a reconciliation consumer must be able to tell them apart. Parsing goes
through the existing `parse_lpar_ownership_owner` — there is exactly one
ownership-token grammar (ADR 0011) and this change adds no second one.

The MCP surface gains `hmc_list_lpar_ownership(system_name_or_uuid=None,
profile=None)` (`effect="read"`, `target_kind="managed_system"`), and
`list_lpar_ownership` joins the ADR 0029 facade manifest.

This supersedes the issue's original N×SSH sketch entirely: with descriptions
inlined in the list feed, the SSH bulk form buys nothing — the REST path does
one HTTPS request on an already-pooled session versus one SSH key exchange
per managed system, and reuses the client's XML parsing instead of adding a
second `lssyscfg` row parser beside `_parse_admitted_rows`.

`get_lpar_description`'s docstring claim that the description is not exposed
via REST is corrected in the same change, citing the #374 survey.

## Consequences

- A fleet-wide ownership reconciliation costs one REST GET per managed system
  (or one in total fleet-wide), not N SSH logins.
- Feed entries do not carry their parent system's name, so fleet-wide results
  identify partitions only by name/UUID; per-system attribution requires the
  selector. Recorded in the operation and tool docstrings rather than papered
  over with N parent-discovery calls that would defeat the bulk win.
- The single-LPAR authorization path (`authorize_lpar_mutation`,
  decommission snapshots) keeps its SSH description read for now: those flows
  are keyed by CLI names inside SSH write commands, and moving authorization
  reads to REST would change the trust path of every mutation tool. That is a
  separate decision, not smuggled in here. The parser is shared either way,
  so no grammar drift is possible.
- The absent-vs-empty distinction is preserved end to end because the shared
  feed parser maps an absent `<Description>` element to a missing dict key;
  the survey's finding means `None` reliably means "no description set".
- Facade export added → minor release due at next release cut per ADR 0029;
  CHANGELOG `[Unreleased]` records it.
