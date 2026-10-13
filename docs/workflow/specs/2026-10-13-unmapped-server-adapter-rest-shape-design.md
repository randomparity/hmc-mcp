# Unmapped vSCSI server adapter: REST shape and witness (#1250)

## Problem

A failed mapping create can leave a VIOS server adapter with no mapping (#1237). No capture
said whether the REST VIOS document shows one. An operator-authorized window on 2026-10-13
(V10R3 M1060, POWER9 8375-42A) answered it. The window DLPAR-added one server adapter
toward the test partition, captured it, removed it, and read back the baseline. In both the
`ViosSCSIMapping` group and the full document, the adapter is a `VirtualSCSIMapping` holding
only a `ServerAdapter`. That element carries `AdapterName`, `VirtualSlotNumber`,
`RemoteLogicalPartitionID` and `RemoteSlotNumber`. The mapping has no `ClientAdapter`,
`TargetDevice`, `Storage` or `AssociatedLogicalPartition`. With the adapter present,
`list-mappings --lpar` was unchanged, and recovery's count check reported it (exit 1).

## Scope

Decision: REST does show the residue, but the witness stays the `lshwres` listing. ADR 0168
already lists such a mapping with `id: null`, and the lack of a partition link drops it from
an `--lpar`-scoped list. Joining by slot would need the slot in the public projection, which
is the excluded identity change. The count check is proven live.

Changes:
- Fixtures `rest-vios-scsi-mapping-unmapped-adapter` and `cli-vios-scsi-unmapped-adapter`
  (tokenized, cited in the fixture README), and the window's rows folded into
  `vocabulary/v10r3-p9.json`.
- `_ADAPTER_SIDE_EFFECT` keeps naming only the `lshwres` listing: an unscoped null-id row
  names no slot or partition and has other causes (ADR 0168), so it would not help.
- Text only: the `_unmapped_server_adapters` docstring, `docs/live-testing.md`, ADR 0168's
  consequences, and the 2026-10-01 recovery spec's two #1250 references.

No transition is needed. No product behaviour, identity or projection changes.

### Failure model

1. Actors and deployments: an operator reading an error or running recovery; CI over fixtures.
2. Invariants and assets: fixtures stay tokenized; ADR 0168 identity unchanged.
3. Accepted failure classes: other firmware may render the residue differently. This is
   accepted because the capture covers one pair, and the vocabulary records which.
4. Covered elsewhere: `just live-vocabulary`; tokenization (`live_capture_export.py`).

## Success

1. Over the captured REST fixture, an unscoped list returns three mappings, and the adapter-only
   one has no identity and no partition. A list scoped to the test partition returns only the
   two identified mappings.
2. The adapter-only mapping pins the observed shape: slot, remote partition and remote slot
   are present; client adapter, target, storage and partition link are absent.
3. Recovery fed the captured listing, with its mapping list projected from the captured REST
   fixture, reports the unmapped adapter naming slots 3 and 5 against one mapped adapter.
4. Both new fixtures carry no lab identifier.
5. `just verify` and `prek run --all-files` pass.

## Validation

- Success 1–2: focused-test, `tests/storage/test_mapping_inventory.py` (fixture-driven).
- Success 3: focused-test, `tests/scripts/test_live_test_recovery.py`.
- Success 4: task-test-not-applicable — the exporter's fail-closed scan plus a manual `rg`
  for lab names, hosts, serials and addresses over both files before commit.
- Vocabulary, docs and ADR prose: task-test-not-applicable. They are prose and data that
  `just live-vocabulary`, `just doc-freshness` and `just adr-numbering` gate.
