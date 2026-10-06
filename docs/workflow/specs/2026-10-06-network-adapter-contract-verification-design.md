# Virtual-network and adapter contract verification (V5, #629)

Part of #620 (child epic #1243). Pattern: #627 / PR #1320, #1349.

## Problem

Nineteen virtual-network, client-adapter and VIOS-label operations have no maturity
record, and their `operations.json` rows are a bulk assignment naming SR-IOV, HEA and
upgrade jobs none of them issue. Round2's ST2 and ST9 record only transport rows,
which never promote. ST9 creates a second partition to carry its client adapter, and
declares the REST VLAN create an expected HTTP 406 — a declaration that predates
ADR 0178's header fix and turns any 406 into a SKIP and a gap instead of a failure.
Neither the client nor the document builders bound a VLAN id, so `vlan_id=5000`
reaches the HMC.

## Scope

1. **Row binding.** Each operation names exactly the resource or command its handler
   issues. Selector resolution (system and partition reads) is shared plumbing and is
   not bound, as in PR #1320.

   | Operation | Rows |
   |---|---|
   | `network.list_switches` | `rest:virtual-network-management/virtual-switch` |
   | `network.list_networks`, `network.create_network`, `network.delete_network` | `rest:virtual-network-management/virtual-network` |
   | `network.list_bridges` | `rest:virtual-network-management/network-bridge` |
   | `network.list_sea`, `network.list_fc_ports` | `cli:commands/lshwres` |
   | `adapter.add_network` | `rest:managed-system/logical-partition/client-network-adapter` |
   | `adapter.add_vscsi` | `rest:managed-system/logical-partition/virtual-scsi-client-adapter` |
   | `adapter.add_vfc` | `rest:managed-system/logical-partition/virtual-fiber-channel-client-adapter` |
   | `adapter.list`, `adapter.delete` | those three plus `rest:managed-system/logical-partition/virtual-nic-dedicated` (the four `AdapterType` values) |
   | `vios_label.list_fc_ports`, `vios_label.list_vfc_groups` | `cli:commands/lslabelvios` |
   | `vios_label.set_fc_port`, `remove_fc_port`, `create_vfc_group`, `update_vfc_group`, `remove_vfc_group` | `cli:commands/labelvios` |

2. **VLAN range (defect).** `create_virtual_network` (`vlan_id`) and
   `add_network_adapter` (`port_vlan_id`) refuse a value outside 1–4094 with
   `ValueError` before any write, in `client_network.py` / `client_adapters.py`.
   IEEE 802.1Q reserves 0 and 4095; the HMC's own refusal is what callers got before.

3. **`network` live arm**, `scripts/live_network.py` (`--group network` = subtasks 2
   and 9). ST9 runs only in this group (gated on `state.group`, as ST37 is); round2
   keeps ST2's reads and SKIPs ST9. The arm creates no partition: the network-test
   partition and its create/delete are removed from ST9.

   **ST2 — reads** (scenario `st2-network-inventory`, `record_verified`, cleanup
   `not-required`). A failed call records `read-failed` as not holding. An **empty**
   listing proves no row shape, so it records a non-promoting `observed` row
   (`<tool> (empty)`), never an observation.

   | Operation | Assertions (non-empty listing) |
   |---|---|
   | `network.list_switches` | `switch-ids-integral` (every `SwitchID` an integer) |
   | `network.list_networks` | `vlan-ids-in-range` (every `NetworkVLANID` parses into 1–4094) |
   | `network.list_bridges` | `bridge-entries-identified` (every entry has a UUID) |
   | `network.list_sea` | `eth-rows-parsed` (every row names `lpar_name` and an integer `port_vlan_id`) |
   | `network.list_fc_ports` | `fc-rows-parsed` (every row names `lpar_name`, `adapter_type`, `slot_num`) |
   | `adapter.list` | all four `AdapterType` listings on the test partition read as lists; `adapter-entries-identified` (≥ 1 entry across them, every entry with a UUID) |
   | `vios_label.list_fc_ports` | `fc-port-rows-parsed` (every row names `name` and `port_name`) |
   | `vios_label.list_vfc_groups` | `group-rows-parsed` (every row has a non-empty first column) |

   ST2 picks no identity ST9 relies on: ST9 re-reads everything it acts on.

   **ST9 — round trips.** Each scenario has its own preconditions and baseline; a
   failure there SKIPs that scenario only. Where the cause is a capability (e.g.
   `lslabelvios` refused on POWER9 — ADR 0105 scopes labels to POWER10/11, or no vFC
   server slot), the gap is recorded by hand from the live run into the operation's
   `missing_scope` and the Live gaps table below; the arm emits no gap row. Shared preconditions (non-promoting rows; failure SKIPs all of ST9):
   the test partition reads `Not Activated` (`lssyscfg` through `hmc_run_command`).
   Identities come only from this run's reads, never from `artifacts` restored from
   an earlier results document.

   *Reconcile rule.* After **every** mutation call, whatever it returned, the scenario
   re-reads its state and treats any difference from its baseline as the run's own
   change: on the test VLAN, any network carrying the run's tagged name (the VLAN was
   unused at baseline; any other network on it is reported, never deleted); on the test partition, any adapter UUID absent from the
   baseline, compared with its slot and pairing so an in-place change shows; for
   labels, any label differing from the baseline. Reversal targets that difference,
   so a timed-out call, an accepted negative, or an implicitly created object is
   reversed like an intended one. A new UUID placed exactly where a vanished
   baseline adapter was is that adapter re-identified and is never deleted. A reversal that fails or cannot be confirmed by a re-read records
   `MANUAL RECOVERY REQUIRED: <what> (<command>)` and stops ST9: no later mutation runs.
   A read failing after a change counts as the change not reversed.

   a. **VLAN and client network adapter** (`st9-virtual-network-round-trip`,
      `st9-client-network-adapter`). Baseline: networks (UUID, VLAN, name), refusing on
      an unparsable VLAN; the test VLAN is the first id in `LIVE_TEST_VLAN_RANGE_*` no
      network uses; the switch id from `hmc_list_virtual_switches` (first `SwitchID`,
      else 0); the test partition's `ClientNetworkAdapter` UUIDs.
      1. `hmc_create_virtual_network(name=hmcpctl-live-vlan<id>-<8 hex>)`; read back.
      2. Collision: create on the same VLAN as `hmcpctl-live-vlan<id>-dup`; read back.
         Any new network beyond the first is reconciled away.
      3. `hmc_add_network_adapter(port_vlan_id=<id>, virtual_switch_id=<switch>)` on
         the test partition; read back.
      4. Negative: `hmc_delete_adapter` of a fresh random UUID; read back.
      5. `hmc_delete_adapter` of the new adapter; read back.
      6. `hmc_delete_virtual_network` of each run network; read back.
      - `network.create_network`: `create-accepted`, `network-listed` (exactly one
        run network, with that VLAN and name), `duplicate-vlan-refused`; cleanup per (6).
      - `network.delete_network`: `delete-accepted`, `networks-equal-baseline`.
      - `adapter.add_network`: `adapter-added` (exactly one new UUID), `pvid-matches`
        (`PortVLANID` = the test VLAN); cleanup per (5).
      - `adapter.delete`: `delete-accepted`, `unknown-uuid-refused`,
        `adapters-equal-baseline`.
   b. **vSCSI client** (`st9-vscsi-client-adapter`). Baseline: the server rows of
      `lshwres -r virtualio --rsubtype scsi --level lpar -F
      lpar_name,lpar_id,slot_num,adapter_type,remote_lpar_id,remote_slot_num`
      (read through `hmc_run_command`); the boundary VIOS is the one VIOS with a server
      adapter whose `remote_lpar_id` is the test partition's id (exactly one, else SKIP);
      its slot is preferred with `remote_lpar_id` `any`, else that slot. Also the test
      partition's storage mappings on that VIOS and its `VirtualSCSIClientAdapter` UUIDs.
      Negative (collision): the same add with `slot_number` set to the virtual slot
      the test partition's own vSCSI client already uses; read back (anything an
      accepted collision added is reversed with the rest). Then
      `hmc_add_vscsi_adapter(vios_partition_id, vios_slot)`, read back, delete, read
      back; then the VIOS's scsi server rows and the mappings must equal the baseline.
      - `adapter.add_vscsi`: `adapter-added`, `pairing-matches`
        (`RemoteLogicalPartitionID`, `RemoteSlotNumber` equal the request),
        `slot-collision-refused` (when the partition has a client slot),
        `adapters-equal-baseline`, `vios-side-unchanged`.
   c. **vFC client** (`st9-vfc-client-adapter`). Baseline: the boundary VIOS's vFC
      server rows from `hmc_list_fc_ports`; eligible only with `remote_lpar_id` equal
      to the test partition's id or `any`; none → SKIP and the gap below. Same steps as
      (b). `adapter.add_vfc`: `adapter-added`, `pairing-matches`
      (`ConnectingPartitionID`, `ConnectingVirtualSlotNumber`),
      `slot-collision-refused`, `adapters-equal-baseline`, `vios-side-unchanged`.
   d. **FC-port label** (`st9-fc-port-label`). Baseline: the boundary VIOS's FC-port
      label rows; the port is the first row; SKIP unless its `port_label` is empty or
      passes the tool's own label validation (nonblank, no control character), so it
      can be restored exactly.
      1. `hmc_set_vios_fc_port_label(hmcpctl-live-<8 hex>)`; read back.
      2. Negative: set on port `fcs9999`; read back (an accepted negative is removed).
      3. `hmc_remove_vios_fc_port_label`; read back.
      4. When the original was non-empty, set it again; read back.
      - `vios_label.set_fc_port`: `label-set`, `unknown-port-refused`,
        `labels-equal-baseline`.
      - `vios_label.remove_fc_port`: `label-removed`, `labels-equal-baseline`.
   e. **vFC group label** (`st9-vfc-group-label`). Baseline: group labels; the name
      `hmcpctl-live-<8 hex>` must be absent.
      1. `hmc_create_vios_vfc_group_label(vios_names=[boundary VIOS])`; read back.
      2. Negative: the same create again; read back.
      3. `hmc_update_vios_vfc_group_label(action="rename", new_name=<name>-r)`; read back.
      4. `hmc_remove_vios_vfc_group_label` of whichever run name is listed; read back.
      - `vios_label.create_vfc_group`: `group-created`, `duplicate-refused`.
      - `vios_label.update_vfc_group`: `group-renamed` (new name listed, old absent).
      - `vios_label.remove_vfc_group`: `group-removed`, `groups-equal-baseline`.

   *Final compare*: every scenario's baseline that was read is re-read and compared;
   each difference is one FAIL row `network baseline compare (<source>)`.

   *Rules.* An assertion is the observed state, never the call's return. A call that
   fails records its assertion as not holding; it never becomes a SKIP. A transport
   success with a failed read-back records `failed`. A reversal's cleanup disposition
   is `passed` only when the re-read equals the baseline.

   The `_REST_CREATE_UNSUPPORTED` declaration is deleted: a 406 now records a failed
   `network.create_network` observation, settled by the live run.

4. **Preflight** names the arm's mutations (the test VLAN, the client adapters on the
   test partition, the label changes on the VIOS serving it). **Recovery** does not
   witness ST9 (exit 2, as for profiles); `docs/live-testing.md` lists the manual
   checks (no `hmcpctl-live-*` network or group label; the test partition's adapters
   and the VIOS's FC-port labels as before). An interrupted run is checked by hand
   the same way.
5. **Catalog**: maturity records for the 19 operations; regenerated projection and
   `docs/tools/`.

### Live gaps

| Case | Prerequisite |
|---|---|
| vFC client adapter (if the boundary VIOS has no vFC server adapter) | a VIOS vFC server adapter on the boundary system |
| group label `add-members` / `remove-members` | a second VIOS on the boundary system |
| adapters on a running partition (DLPAR) | an activated disposable partition; excluded |
| NPIV fabric login | a zoned fabric; excluded |
| a non-empty listing for any ST2 read the boundary system returns empty | a system with that resource |
| FC-port and group labels on POWER9, if `lslabelvios` is refused there | a POWER10/11 system in the mutation boundary |

## Failure model

1. **Actors and deployments** — the operator running the arm from a lab host against
   the V10R3 / POWER9 mutation-boundary system; CI never runs it.
2. **Invariants and assets at stake** — every pre-existing virtual network (never
   changed); the boundary VIOS's SEA (never touched) and FC-port labels (restored to
   the exact value); other partitions' adapters (never touched); the test partition's
   adapter set (restored); catalog truth (no promotion without asserted
   postconditions).
3. **Accepted failure classes** — a stranded test VLAN, adapter or label after a
   failed reversal, reported as manual recovery with its command; an interrupted or
   cancelled run, which reverses nothing further and is checked by hand per
   `docs/live-testing.md`; each vFC client add consuming a WWPN pair from the system's
   pool; the HMC reordering listings (compared as sets).
4. **Covered elsewhere** — ownership and access policy: existing runtime guards;
   VLAN update and switch mutations: #664; SEA/bridge: #665; vSCSI mappings: #628;
   environment isolation: #461.

### Threat model

- Boundaries: harness-built `hmc_run_command` reads from configured names; label
  and network names generated by the arm.
- Controls: `shlex.quote` and `build_filter` on every interpolated name; generated
  names are `hmcpctl-live-` plus hex; the new VLAN range check refuses before I/O.
- Out of scope: a hostile HMC.

## Success

- The rows bind as tabled; `just capability-inventory` passes.
- Nineteen maturity records; live ones carry the run's observations, failed where an
  assertion failed.
- Unit tests pin the range check, each ST9 decision (preconditions, collision,
  stop-on-failed-reversal, SKIPs), the group gating, the preflight disclosure.
- `just verify` and `prek run --all-files` pass.

## Validation

`tests/network/` (range check), `tests/test_live_network_arm.py` (ST2/ST9 against a
scripted fake `state.call`), `tests/scripts/test_live_network.py` (wrapper),
`tests/scripts/test_live_test_preflight.py`, `tests/test_live_runner.py` (group table);
the catalog through `just capability-inventory`. The live run is the proof against
hardware.
