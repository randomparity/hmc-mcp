# PCIe and SR-IOV capability contract design

**Issue:** #211  
**Decision:** [ADR 0053](../../adr/0053-evidence-backed-pcie-capability-contract.md)  
**Base branch:** `main`  
**Guardrail:** `just verify`

## Goal and scope

Establish the repository-backed command, field, identity, capacity-unit, LPAR-state, and
capability-unavailable contract required by issues #212–#216. This change adds version-labelled
sanitized evidence and tests that pin its parsing. It does not expose normalized inventory tools,
implement mutation, or perform a live HMC mutation.

The repository documents HMC V8–V11 compatibility. IBM command references for Power8, Power9,
Power10, and Power11 are the allowed evidence source in this change. No supported live HMC was
available to this run, so fixtures must say `documentation` rather than imply captured live
output. A future live capture may add another labelled fixture without changing the identities.

## Evidence representation

Create one JSON fixture per documented command family and version family under
`tests/fixtures/pcie/`. Every fixture contains:

- `evidence_kind`: `documentation`;
- `hmc_family`: one of `V8`, `V9`, `V10`, `V11`;
- an IBM documentation URL;
- the exact read-only command with explicit `-F` field order;
- `fields`, matching that order exactly;
- sanitized `stdout` rows using `|` as delimiter;
- `capacity_unit` where capacity fields occur.

The fixtures are executable evidence, not opaque snapshots. Tests load every file, validate its
metadata, and parse every row against the declared field count. This prevents field-order drift,
accidental identity substitutions, and undocumented unit changes.

## Read command contract

| Resource | Command shape | Stable identity | Required evidence fields |
|---|---|---|---|
| Dedicated slot | `lshwres -r io --rsubtype slot -m SYSTEM -F ...` | system + `drc_index` | `drc_index`, `drc_name`, `lpar_id`, `lpar_name`, `pci_class` |
| SR-IOV adapter | `lshwres -r sriov --rsubtype adapter -m SYSTEM -F ...` | system + `adapter_id` | `adapter_id`, `slot_id`, `mode`, `state` |
| Physical port | `lshwres -r sriov --rsubtype physport --level eth -m SYSTEM -F ...` | system + `adapter_id` + `phys_port_id` | IDs, location/label, configured and available capacity, minimum capacity granularity |
| Logical port | `lshwres -r sriov --rsubtype logport --level eth -m SYSTEM -F ...` | system + `adapter_id` + `logical_port_id` | IDs, owning LPAR ID/name, current/desired capacity, current/desired max capacity |

Exact fixture field names are the compatibility boundary for #212. Additional HMC fields are
ignored until a new labelled fixture and contract test admits them. Empty owner columns mean
unassigned only when the command succeeded and the row otherwise has its required identity.

The parser is presentation-neutral: `parse_hmc_delimited_rows(text, fields, delimiter="|")`
returns dictionaries with whitespace preserved only inside values and empty strings retained.
It rejects an empty field list, duplicate/blank field names, a non-single-character delimiter,
and rows whose column count differs from the field count. It is used for fixture proof now and
can be reused by #212's explicit `-F` read commands.

## Identity and units

Selectors always include managed-system scope. `drc_index`, `adapter_id`, `phys_port_id`, and
`logical_port_id` retain their CLI spelling as strings at the parsing boundary; normalization may
validate numeric forms later without losing leading or hexadecimal notation. `logical_port_id`
is the logical-port DRC index. Display names, DRC names, location codes, labels, MAC addresses,
and owner names are never selectors by themselves.

`capacity`, `max_capacity`, `curr_capacity`, `desired_capacity`, and
`min_eth_capacity_granularity` are decimal percentages in the inclusive range 0–100 where IBM
permits zero, with up to two decimal places. Downstream code must use decimal semantics and must
not interpret `10` as bytes, Mbps, or an arbitrary weight. The sum of configured logical-port
capacity on one physical port cannot exceed 100 percent.

## LPAR-state-by-operation matrix

| Operation | Create time | Inactive/shut down | Running | Capability unavailable |
|---|---|---|---|---|
| Assign/unassign dedicated slot | Include/remove `io_slots` in the created profile | Change `io_slots` in the selected profile; effective state changes only when the profile is applied or activated | Use dynamic `chhwres -r io --rsubtype slot` only when HMC reports DLPAR support; otherwise reject as unsupported, never silently edit only the profile | Return capability-unavailable; do not mutate |
| Assign/unassign SR-IOV logical port | Include/remove the documented SR-IOV logical-port profile property | Change the selected profile; effective state changes only when applied or activated | Use `chhwres -r sriov --rsubtype logport -o a/-o r -p LPAR`; read back the logical port | Return capability-unavailable; do not mutate |
| Switch adapter shared/dedicated mode | Not an LPAR create operation | System-scoped only after dependent logical ports/owners are absent | Same precondition; never infer safety from one LPAR's state | Return capability-unavailable; do not mutate |

Create-time and profile operations are declarative. Dynamic operations are effective-state
operations. Downstream result schemas must report these two states separately. `--force` is not
part of the ordinary contract; conflict override requires an explicit later policy decision.

## Capability and error behavior

Command success with no non-empty rows means `available` with an empty collection. This change
admits no HMC error signature as proof of `capability-unavailable`: every non-success remains an
error unless later version-labelled evidence supplies the exact command, exit status, diagnostic,
and classifier test. Authentication, authorization, transport, timeout, malformed successful
output, and apparently unsupported-resource failures therefore must not be rewritten as
capability absence. A required unavailable capability fails closed without mutation. A partial row
missing any stable-identity component is malformed evidence, not an unavailable capability and
not an unassigned resource.

## Testing and acceptance

Tests must prove:

1. all evidence fixtures use allowed version labels, HTTPS IBM sources, read-only `lshwres`
   commands, exact declared columns, and explicit capacity units;
2. each resource's stable identity fields survive parsing, including empty owner attributes;
3. decimal capacity examples retain two-decimal precision and are labelled `percent`;
4. malformed column counts, duplicate/blank fields, and invalid delimiters fail clearly;
5. the state matrix contains create-time, inactive, running, and capability-unavailable outcomes
   for dedicated slots, logical ports, and adapter-mode transitions;
6. `just verify` passes.

## Security boundary

The generic parser will eventually consume SSH output that the repository did not produce. It
does not execute values, build shell commands, or deserialize objects; it only separates bounded
fixture rows into caller-supplied columns and rejects shape mismatches. Existing SSH quoting and
attribute-record validation remain unchanged. Mutation commands and authorization are outside
this change and remain owned by #213–#216.
