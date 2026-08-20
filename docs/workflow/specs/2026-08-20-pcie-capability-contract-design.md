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
- sanitized `stdout` rows using the command's comma delimiter;
- `capacity_unit` where capacity fields occur.

The fixtures are executable evidence, not opaque snapshots. Tests load every file, validate its
metadata, and parse every row against the declared field count. This prevents field-order drift,
accidental identity substitutions, and undocumented unit changes.

## Read command contract

| Resource | Exact `-F` field order | Stable identity |
|---|---|---|
| Dedicated slot | `drc_index,description,lpar_name` | system + `drc_index` |
| SR-IOV adapter | `adapter_id,slot_id` | system + `adapter_id` |
| Physical port | `adapter_id,phys_port_id,state,phys_port_loc,min_eth_capacity_granularity` | system + `adapter_id` + `phys_port_id` |
| Logical port | `adapter_id,phys_port_id,logical_port_id,lpar_id,lpar_name,capacity,max_capacity` | system + `adapter_id` + `logical_port_id` |

The exact commands use `-F <comma-list> --header` with that order and a comma delimiter. The
fixtures preserve the returned header and data separately, so the parser checks the HMC header
against the independent matrix before parsing data. The admitted family matrix is:

| Family | Dedicated slot | Adapter | Physical port | Logical port |
|---|---|---|---|---|
| V8 | documented | documented | documented | documented |
| V9 | documented | documented | documented | documented |
| V10 | documented | documented | documented | documented |
| V11 | documented | documented | documented | documented |

Each `documented` cell requires a fixture with the exact family-specific IBM URL and command. A
future unsupported cell is written explicitly as `unsupported` with source evidence; omission is
invalid. The common field matrix is intentionally minimal: it contains only fields admitted for
all four supported families. Later fields require a separate per-family optional-field matrix.

Exact fixture field names are the compatibility boundary for #212. Additional HMC fields are
ignored until a new labelled fixture and contract test admits them. Empty owner columns mean
unassigned only when the command succeeded and the row otherwise has its required identity.

The parser is presentation-neutral: `parse_hmc_delimited_rows(text, fields, delimiter=",")`
returns dictionaries with whitespace preserved only inside values and empty strings retained.
It rejects an empty field list, duplicate/blank field names, a non-single-character delimiter,
and rows whose column count differs from the field count. It is used for fixture proof now and
can be reused by #212's explicit `-F` read commands.

Row tokenization uses `str.splitlines()`. Empty input, a final newline, blank lines, and
whitespace-only lines between records contribute no row. Each non-blank line is parsed with
Python's `csv.reader` using the one-character delimiter and standard double-quote escaping.
Unquoted and quoted values have surrounding whitespace retained; only the blank-line predicate
uses `str.strip()`. A delimiter-only line is a row of empty values and is valid only when its
column count matches. A quoted delimiter remains inside one value. Header validation requires an
exact value-for-value match with the canonical field list before data rows are accepted.

## Identity and units

Selectors always include managed-system scope. `drc_index`, `adapter_id`, `phys_port_id`, and
`logical_port_id` retain their CLI spelling as strings at the parsing boundary; normalization may
validate numeric forms later without losing leading or hexadecimal notation. `logical_port_id`
is the logical-port DRC index. Display names, DRC names, location codes, labels, MAC addresses,
and owner names are never selectors by themselves.

`capacity`, `max_capacity`, and `min_eth_capacity_granularity` are decimal percentages with up to
two decimal places. Downstream code must use decimal semantics and must not interpret `10` as
bytes, Mbps, or an arbitrary weight. This contract does not generalize undocumented zero rules,
aliases, available-capacity fields, or aggregation behavior across families.

## LPAR-state-by-operation matrix

| Operation | Create time | Inactive/shut down | Running | Capability unavailable |
|---|---|---|---|---|
| Assign/unassign dedicated slot | Add/remove `<drc_index>//0` in profile `io_slots` before create; read back `lssyscfg -r prof -m SYSTEM -F io_slots` | `chsyscfg -r prof -m SYSTEM -i name=PROFILE,lpar_id=ID,io_slots+=/-=<drc_index>//0`; read back the profile; effective state remains unchanged until apply/activation | `chhwres -r io -m SYSTEM -o a/r --id LPAR_ID -l DRC_INDEX`; only with a verified RMC/DLPAR-capable running LPAR; read back slot owner by `drc_index` | Any unavailable precondition or command failure is an error; do not fall back to a profile-only success |
| Assign/unassign SR-IOV logical port | Add/remove a property record in `sriov_eth_logical_ports` or `sriov_roce_logical_ports`; required add selectors are `adapter_id`, `phys_port_id`, and type-specific capacity; read back the same profile property | `chsyscfg -r prof -m SYSTEM -i name=PROFILE,lpar_id=ID,<property>+=/-=<record>`; removal includes `adapter_id` and `logical_port_id`; effective state remains unchanged until apply/activation | Add: `chhwres -r sriov -m SYSTEM --rsubtype logport -o a --id LPAR_ID -a adapter_id=A,phys_port_id=P,logical_port_type=TYPE,capacity=C[,max_capacity=M]`; remove uses `-o r --id LPAR_ID -a adapter_id=A,logical_port_id=L`; read back by adapter + logical-port ID | Any unavailable precondition or command failure is an error; do not mutate |
| Switch adapter shared/dedicated mode | Not an LPAR create operation | System-scoped only after inventory proves dependent logical ports and owners absent | Shared: `chhwres -r sriov -m SYSTEM --rsubtype adapter -o a -a slot_id=DRC[,adapter_id=A]`; dedicated: same with `-o r -a slot_id=DRC`; read back adapter ID, slot ID, and mode; never infer safety from one LPAR's state | Any unavailable inventory or command failure is an error; do not mutate |

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
   plus exact selectors, attributes, preconditions, and readbacks for dedicated slots, logical
   ports, and adapter-mode transitions;
6. a diff-scoped no-mutation check confirms #211 adds no command execution, mutation function,
   effect metadata, server/CLI/API export, or parser-to-SSH call. The existing
   `set_sriov_adapter_mode` function is baseline, not a failure;
7. `just verify` passes.

## Security boundary

The generic parser will eventually consume SSH output that the repository did not produce. It
does not execute values, build shell commands, or deserialize objects; it only separates bounded
fixture rows into caller-supplied columns and rejects shape mismatches. Existing SSH quoting and
attribute-record validation remain unchanged. Mutation commands and authorization are outside
this change and remain owned by #213–#216.
