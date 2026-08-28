# VIOS FC label administration design

Issue: [#556](https://github.com/randomparity/hmc-mcp/issues/556)
Decision: [ADR 0105](../../adr/0105-explicit-vios-fc-label-administration.md)

## Goal and frozen scope

Provide safe administration of individual VIOS FC-port labels and vFC group labels through MCP
and CLI surfaces. The approved operations are:

- list FC-port labels;
- set or remove one FC-port label;
- list vFC group labels;
- create one vFC group label;
- add or remove named members from one vFC group label;
- rename one vFC group label; and
- remove one named vFC group label.

MSP, vNIC, and vSCSI group labels, managed-system label override defaults, bulk deletion,
label-driven vFC adapter mapping/add/remove, and changes to existing adapter mutation contracts
are excluded. The IBM POWER10 and POWER11 `labelvios` and `lslabelvios` command references are the
external behavior authority. Both generations document the same command grammar. The repository
operator approved this exact narrow scope interactively on 2026-08-28.

Python 3.11 remains the floor. Declared targets are amd64 and arm64; the x86_64 host is included
through the amd64 alias. No dependency, schema migration, persisted format, or reusable
`hmc_mcp.api` export is added. The base branch is `main`; final local guardrails are `just verify`
and `uv run --no-sync prek run --all-files`.

## Public contract

The MCP surface adds seven explicit tools:

| Tool | Effect | Inputs beyond `profile` | Result |
|---|---|---|---|
| `hmc_list_vios_fc_port_labels` | read | `system_name_or_uuid`, optional `vios_name`, optional `vios_id` | label rows |
| `hmc_set_vios_fc_port_label` | mutate | `system_name_or_uuid`, `label`, `port_name`, exactly one VIOS selector | receipt |
| `hmc_remove_vios_fc_port_label` | destructive | `system_name_or_uuid`, `port_name`, exactly one VIOS selector | receipt |
| `hmc_list_vios_vfc_group_labels` | read | `system_name_or_uuid` | label rows |
| `hmc_create_vios_vfc_group_label` | mutate | `system_name_or_uuid`, `label`, exactly one non-empty member list | receipt |
| `hmc_update_vios_vfc_group_label` | mutate | `system_name_or_uuid`, `label`, one of rename/add-members/remove-members and its value | receipt |
| `hmc_remove_vios_vfc_group_label` | destructive | `system_name_or_uuid`, `label` | receipt |

`system_name_or_uuid` accepts the managed-system name or UUID used by existing public tools. The
operation layer resolves a UUID to its HMC CLI name before command construction. A
VIOS selector is exactly one of `vios_name: str` and `vios_id: int`; both set or both omitted is a
pre-dispatch error. A group member selector is exactly one non-empty list, `vios_names: list[str]`
or `vios_ids: list[int]`. Empty strings, non-positive VIOS IDs, empty member lists, duplicate
members, HMC record delimiters, ASCII controls, and ambiguous selector combinations fail before
SSH dispatch.

The group update tool uses `action: Literal["rename", "add-members", "remove-members"]`.
`rename` requires `new_name` and forbids member lists. Member actions require exactly one
non-empty member list and forbid `new_name`. This one operation matches IBM's single set command
while retaining a closed JSON schema; create and remove remain separate because they have
different required fields and effects.

The CLI exposes matching commands under `hmc-mcp vios` with `--json` on reads, `--yes` on every
mutation, and confirmation prompts on stderr. Repeated `--vios-name` or `--vios-id` options form
group member lists. Mutations print the structured receipt as JSON so automation receives the
same evidence as MCP.

List results are `list[dict[str, str]]`. Each command requests all documented attributes with
`-F --header`; the parser treats the first nonblank row as the HMC-supplied header, requires
unique nonblank lower-case attribute names, and requires every later CSV row to have the same
width. Empty output and the exact HMC `No results were found.` sentinel return `[]`; malformed
output raises an actionable error rather than returning partial rows.

A mutation receipt is a dictionary containing `operation`, `system_name`, `label`, `port_name`,
`vios_name`, `vios_id`, `action`, and `output`. Fields irrelevant to an operation are absent, not
null. `output` is the stripped HMC stdout and may be empty. The receipt proves what was dispatched;
it does not claim post-command state or idempotency that the documented commands do not establish.

## Command construction and data flow

`src/hmc_mcp/ssh/vios_labels.py` owns the command grammar. It uses the existing
`build_attribute_record` and `build_filter` controls and wraps every standalone argument and the
complete `-i` record with `shlex.quote` before `run_hmc_command`.

Reads issue:

```text
lslabelvios -r fcport -m <system> [--filter <one VIOS selector>] -F --header
lslabelvios -r group -m <system> --filter resources=vfc -F --header
```

FC-port list filters admit only one VIOS name or ID. The vFC group filter is fixed by the
implementation and cannot select another resource family.

Mutations construct only these records:

```text
labelvios -m <system> -o s -l <label> -i resource=fcport,port_name=<port>,vios_names=<name>
labelvios -m <system> -o r -i resource=fcport,port_name=<port>,vios_ids=<id>
labelvios -m <system> -o a -l <label> -i resource=vfc,"vios_names=<name,...>"
labelvios -m <system> -o s -l <label> -i "vios_ids+=<id,...>"
labelvios -m <system> -o s -l <label> -i new_name=<new-label>
labelvios -m <system> -o r -l <label>
```

The opposite VIOS selector family and `vios_names-=`/`vios_ids-=` are symmetric documented forms.
List-valued pairs are always final, satisfying the existing record builder's admitted grammar.
Callers cannot supply resource type, operation code, attribute name, list operator, or raw record.

`src/hmc_mcp/operations/vios_labels.py` resolves the public managed-system selector with the
existing SSH selector resolver and delegates to the SSH module. MCP tools adapt scalar/list inputs
to that operation layer through `with_config`, retain the exact `system_name_or_uuid` parameter
required by managed-system target authorization, and use `target_kind="managed_system"`. Reads
use `effect="read"`; set, create, update, and rename use `effect="mutate"`; removals use
`effect="destructive"`. CLI commands call the same operation functions through `ssh_config`; no
second command builder exists.

## Error handling

Local validation errors identify the operation and offending field without dispatch. HMC command
errors retain the existing `HMCCLIError` behavior. A successful SSH exit with malformed list
output is an error because the response cannot be represented truthfully. Mutation command
success returns a receipt even when stdout is empty. No automatic retry, pre-read, post-read,
rollback, or compensating command is added: IBM documents command actions but the available
evidence does not define a transaction or stable read-after-write identity contract.

The delete tools never expose IBM's `resource=<family>` removal form without `-l`; therefore they
cannot delete all labels in a family. FC-port removal always includes `resource=fcport`, one port,
and one VIOS selector. Group removal always includes one nonblank `-l` label.

## Threat model

### Boundary inventory and actors

The added boundaries are authenticated MCP or local CLI operator input entering HMC command
construction, HMC stdout entering structured parsing, and new label mutation capability entering
the existing access-policy registry. The widened boundary is the configured SSH transport, which
already carries HMC commands. Callers may control every public string and list. The configured HMC
and credential provider are trusted peers; authenticated callers, HMC output shape, and concurrent
HMC operators are not trusted to preserve this request's intent.

### Controls

- Public values are checked for requiredness, selector exclusivity, positive IDs, duplicate
  members, record delimiters, and controls before dispatch.
- Fixed operation/resource/attribute names prevent callers from reaching other `labelvios`
  families or bulk removal.
- `build_attribute_record` controls the HMC CSV grammar; `shlex.quote` separately controls the
  remote shell boundary.
- Structured reads reject malformed headers, duplicate columns, malformed CSV, and width drift.
- Registry metadata applies existing policy ceilings and managed-system target authorization;
  destructive label removals require the destructive effect grant.
- Errors and receipts contain selectors and HMC diagnostics but never SSH credentials.

### Explicitly out of scope

The design does not prevent a separately authorized HMC operator from racing or reversing the
request, verify SAN zoning, establish matching labels on a migration destination, roll back a
successful HMC mutation, or make label operations transactional. Those behaviors require external
state and evidence not authorized by issue #556. Existing transport controls continue to own SSH
host authentication and credential secrecy.

## Verification

Sanitized synthetic POWER10/POWER11 fixtures cover FC-port rows, vFC group rows, empty results,
malformed headers, malformed CSV, and row-width drift. Command tests assert exact documented
commands for every operation and both VIOS selector families. Table-driven rejection tests cover
empty and duplicate members, both/neither selectors, non-positive IDs, every HMC record delimiter,
ASCII controls, incompatible update arguments, and label/port shell metacharacters remaining
quoted data. A controlled fault changes one expected command and proves the new tests fail before
the implementation is retained.

MCP tests assert names, schemas, effects, operation names, managed-system targets, authorization,
and receipts. CLI tests assert registration, JSON output, confirmation refusal, and that prompts do
not contaminate stdout. Existing adapter tests assert their public signatures remain unchanged.
Generated tool documentation is refreshed with `just tool-docs` and checked with
`just tool-docs-check`. Focused tests run red before implementation and green afterward, followed
by `just verify` and `uv run --no-sync prek run --all-files` bare.
