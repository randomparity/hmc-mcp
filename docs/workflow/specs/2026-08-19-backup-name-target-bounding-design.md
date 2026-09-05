# `backup_name` and the `UNBOUNDED_ARGUMENTS` line

Issue: [#264](https://github.com/randomparity/hmc-mcp/issues/264)
Decision: [ADR 0044](../../adr/0044-containment-decides-unbounded-arguments.md)

The reasoning lives in ADR 0044 and is not restated here — this issue is about one
argument's classification being stated in two places that drifted, so a spec
paraphrasing the record would recreate exactly that. This document holds the
requirements, the change list, and the test plan.

## Goal

Make the rule that decides `exhaustive_targets` state the distinction actually
applied — ADR 0039's containment question — and classify `backup_name` under it,
so the guardrail comment and the code can be read together without inferring an
unstated rule and neither can drift from the other unnoticed.

## Requirements

- **R1** The rule text states containment: whether the identity can address a
  resource, read or written, that the declared selectors do not contain. Not which
  filesystem the name refers to, and not whether the value is written.
- **R2** `backup_name`'s classification agrees with that rule text.
- **R3** The containment `backup_name`'s classification depends on is enforced
  here, not assumed of the HMC. No unpublished claim about `chviosbackup` is
  load-bearing.
- **R4** A test pins the classification together with the guard that makes it
  true, so removing either fails on the other.
- **R5** The refusal covers only shapes that could denote something other than a
  catalog entry — no character-set or length rule, so a catalog entry named
  outside whatever grammar the HMC enforces stays restorable.

## Changes

| File | Change |
|---|---|
| `docs/adr/0044-containment-decides-unbounded-arguments.md` | The decision record. |
| `src/hmc_mcp/server_tools/vios.py` | `_validate_backup_name` refuses a `backup_name` that is empty or differs from its stripped form, carries `/` or `\`, is made only of dots, or starts with `-`; docstring states the refusal. |
| `src/hmc_mcp/tool_registry.py` | `UNBOUNDED_ARGUMENTS` comment states the containment criterion and points at ADR 0044. |
| `tests/app/test_tool_security.py` | Guardrail comment rewritten to the containment rule; a test pinning `hmc_restore_vios`'s classification to the guard; a test requiring every `UNBOUNDED_ARGUMENTS` member to carry its reason beside the set. |
| `tests/vios/test_vios_backup.py` | The refused shapes, and a legitimate name still reaching the command unchanged. |
| `tests/unit/test_ssh_quoting.py` | `test_restore_vios_quotes_hostile_backup_name` moves to a separator-free hostile value. |

`UNBOUNDED_ARGUMENTS` itself is unchanged — `backup_name` does not join it.

The `tests/unit/test_ssh_quoting.py` edit is outside the surface frozen at the
start and is recorded here deliberately. It is a necessary consequence of R3: the
existing hostile value is `/backups/vios;id`, which the guard R3 requires now
refuses before the command is built. The quoting property it proves is preserved
by moving to a hostile value without a separator, so nothing that test asserted
is lost.

## Out of scope, with owners

- Whether the HMC *also* refuses a path-shaped `-file`, which would make the guard
  redundant rather than necessary —
  [#283](https://github.com/randomparity/hmc-mcp/issues/283).
- Whether restoring an `ssp`-type entry reaches the cluster, which would make
  `hmc_restore_vios`'s own declaration wrong —
  [#282](https://github.com/randomparity/hmc-mcp/issues/282).
- Whether `chviosbackup`/`lsviosbackup` are the HMC's actual command names — IBM's
  HMC command index names `chviosbk`/`lsviosbk`, and its guidance names
  `rstviosbk` for restore rather than an `-operation restore` mode —
  [#289](https://github.com/randomparity/hmc-mcp/issues/289). The classification
  turns on the call's shape, which that question does not change.
- `file_path`'s classification on the profile pair, settled by ADR 0036 and
  ADR 0039.

## Threat model

**Boundary inventory.** One existing boundary is narrowed, none added: the
`backup_name` argument of `hmc_restore_vios`, which crosses from an MCP caller
into an HMC CLI command string built by `_run_vios_backup_command` and run over
SSH. No new entry point, no new grant, no dependency change.

**Actor model.** The untrusted party is an MCP client authorized for
`hmc_restore_vios` under a `targets` table naming some VIOS. Trust is placed in
the access policy to have bound `vios_name_or_uuid`, and in the HMC to run the
command as the configured SSH user. The client is *not* trusted to supply a
`backup_name` that stays inside the catalog — that is the assumption the guard
stops making.

**Control per boundary.** Three controls meet at this boundary and none replaces
another. `shlex.quote` governs shell metacharacters and is unchanged. The access
policy authorizes `vios_name_or_uuid` before the handler runs, also unchanged.
The new control is the containment refusal, raising `ValueError` naming the
constraint and echoing the value no further than the caller's own error.

One shape needs naming separately because the first control does not reach it: a
value like `-operation` carries no shell metacharacter, so `shlex.quote` emits it
bare and the CLI reads it as a flag rather than as a file name. The refusal is
what covers argument injection here, which is why it refuses a leading `-` and
not only separators.

**Explicitly out of scope.** Whether a caller granted one VIOS should reach *any*
backup of that VIOS — every entry in the catalog belongs to the declared VIOS, so
it is inside the grant by construction. Whether the HMC imposes further validation
(#283). Local-file disclosure through a different argument — `iso_source` is #261.

**Observability.** `tool_registry.authorized` calls the dispatch authorizer
before the handler, so the authorization decision for `vios.restore` against the
declared VIOS is recorded before a refused `backup_name` raises. The record does
not carry the refusal or the rejected value, so the stream shows an authorized
dispatch and nothing marking it as one that never ran. Accepted as-is: making a
rejected argument legible in the audit stream is a broader change than this issue
owns, and the grant is still required to reach the handler at all.

## Testing

- The classification pin, in one test: `hmc_restore_vios.exhaustive_targets` is
  true, `backup_name` is absent from `UNBOUNDED_ARGUMENTS`, **and** each escape
  shape is refused. Deleting the guard reddens the test that asserts the
  classification, which is what R4 asks for.
- Each refused shape exercised: empty, whitespace-only, dot-segment, absolute
  path, backslash, `.`, `..`, padded `..`, padded ordinary name, option-shaped.
- Ordinary catalog names — `vios1_backup_001`, `nim_resources.tar`,
  `cfgbackup.tar.gz`, `a-b_c.1` — reach the command unchanged (R5), and the
  existing restore tests keep passing untouched.
- Both pins watched biting rather than assumed: flip `exhaustive_targets` to
  `False` and remove the guard in turn, confirm each reddens a test, revert.
- `tests/unit/test_ssh_quoting.py` still proves `shlex.quote` is applied to
  `backup_name`, using a hostile value the guard admits.
- The full suite green with no other test changed — the check that R5 held and
  that nothing else depended on the old permissiveness.
