# 0164 — LPAR PowerOff operation is a closed vocabulary with an opt-in for the dumping member

## Status

Accepted (2026-09-22)

## Context

`power_off_lpar_job(immediate)` (`jobs/requests.py:107-116`) emits `restart="false"` and
`operation="shutdown"` as literals, so the only choice a caller has is `immediate`. The job
documents three parameters —
`docs/refs/hmc-rest-api-p11/jobs/logicalpartition-jobs/038-poweroff_logicalpartition-job.md:28-30`
lists `immediate`, `restart`, and `operation` ∈ {`shutdown`, `osshutdown`, `dumprestart`,
`dumpretry`}. Issue #872, under epic #871, makes `restart` and `operation` reachable so
kdive's `PowerAction` has a `cycle`/`reset` path (`restart=true`), a graceful path
(`osshutdown`), and the force-crash POWER offers (`dumprestart`).

Three questions have viable answers. Which members of the vendor vocabulary this package
admits; who refuses a non-member, and when; and what — if anything — stands between a caller
and `dumprestart`, which crashes a running partition and takes a platform dump. ADR 0158
governs the `/do/{operation}` **path segment** and not these job parameters, so no closed-set
change is needed there; ADR 0161 settled the same three questions for PowerOn one issue
earlier and is the shape this record follows where it can.

## Decision

**`jobs/requests.py` owns `PowerOffOperation` as a `Literal` alias with a matching
`frozenset`, and `validate_power_off_operation` holds both the permitted set and the refusal
wording** — the shape `validate_power_on_activation` already uses at `jobs/requests.py:65-81`.
Refusal is `ValueError` naming the sorted permitted set and not the rejected value, matching
ADR 0158's style.

**`dumpretry` is excluded from the admitted set.** The alias is `Literal["shutdown",
"osshutdown", "dumprestart"]`; epic #871 requirement 1 makes the exclusion an epic-level
requirement rather than this change's discretion.

**`dumprestart` requires `allow_dump_restart=True` and is refused when absent**, on the
builder, on `power_lpar`, on `hmc_power_off_lpar`, and as `--allow-dump-restart` on the CLI.
The refusal is a second `ValueError` from the same validator. It is patterned on
`ownership_override`: a named boolean that says what it authorizes, defaulting to the safe
answer, so an unadorned call cannot reach the dangerous one.

**Both the builder and `power_lpar` call the validator.** `power_lpar` calls it before the
ADR 0092 ownership leg that can write an audited override record and before any REST read, so
a refused request performs no side effect; the builder calls it again because
`jobs.power_off_lpar_job` is re-exported for direct use and cannot trust its arguments. Same
both-sites answer as ADR 0161, for a different reason: PowerOff has no already-running early
return, so here the builder alone would be *sufficient* but not *early*.

**`hmc_power_off_lpar` keeps `exhaustive_targets=True` and gains no containment read.** ADR
0039 requires every resource acted on to be a declared selector's value or derived from one
through the HMC's own containment. `restart`, `operation` and `allow_dump_restart` name no
resource: they are modes applied to the partition the declared `lpar` selector already bounds.
That is what distinguishes them from `partition_profile_uuid`, which named a second HMC-side
resource and is why ADR 0161 added a feed read.

**Every new parameter defaults so a call passing none of them emits today's document byte for
byte.** `src/hmc_mcp/operations/lpar/decommission.py:479` shares the builder and passes only
`immediate`; its emitted document is unchanged, and a test pins that rather than trusting the
default to stay put.

## Consequences

`osshutdown` needs an active RMC connection to the partition's operating system; without one
the job fails at the HMC, not here, and this package has no RMC-state read to pre-check it
with. `dumprestart` is now reachable from the MCP tool, so an LLM client holding a `targets =
{lpar = [...]}` grant can crash a partition it may already power off — `allow_dump_restart`
is the whole of what stands between the two, and it is an argument the same client supplies.
The `effect` classification does not change: `destructive` already covered a PowerOff.

Changing the tool's parameters restates its signature, which two committed artifacts
record: `docs/capabilities/operations.json` stores `str(inspect.signature(handler))`, compared
by string equality inside `just verify`, and the `Args:` block is the source of the rendered
MCP schema descriptions that `tests/app/test_lifecycle_schema_descriptions.py` requires for
every parameter. `docs/tools/lpar.md` publishes the first docstring line and is regenerated.

Adding `dumpretry` later means editing one `Literal` and the tests that enumerate it.
`lpar.power_off` has no entry in `docs/capabilities/maturity.json`, so this change strands no
live observation; #879 owns the live window that would promote it, and whether `dumprestart`
behaves as documented stays unproven until then.

## Considered & rejected

- **Admit `dumpretry` with the other three.** verified: the reference lists it as a fourth
  member (`038-poweroff_logicalpartition-job.md:30`) and describes no precondition, so nothing
  in the corpus refuses it. judgment: it retries a dump a previous `dumprestart` left
  incomplete, so it is meaningless without operator context this package cannot see, and epic
  #871 requirement 1 excludes it.
- **Ship `dumprestart` with no opt-in, relying on the CLI's `typer.confirm`.** verified: the
  prompt exists at `cli_commands/lpar/lifecycle.py:121` and `--yes` skips it; the MCP tool and
  `power_lpar` have no confirmation mechanism at all, and `server_tools/lpar/lifecycle.py:395`
  is reached by an LLM client, not a human. judgment: the one surface that asks a human is the
  one surface that was already safe; the gate has to live where the confirmation does not.
- **Give `dumprestart` its own MCP tool instead of a flag.** verified: the registry exposes
  155 tools (`just smoke`, this branch), so one more is structurally unremarkable. judgment: a
  separate grant target is a real benefit, but it duplicates the whole PowerOff argument list
  to distinguish one job parameter, and a grant that already admits `hmc_power_off_lpar` is
  not made narrower by the split.
- **Validate in `power_lpar` only, leaving the builder as it is.** verified: unlike PowerOn,
  `power_lpar`'s PowerOff arm reaches the builder on every path — the already-running early
  return at `operations/lpar/core.py:585-601` is guarded by `power_on`. judgment: sufficient,
  but `jobs.power_off_lpar_job` is re-exported in `src/hmc_mcp/jobs/__init__.py:42` for direct
  use, so the builder still cannot trust its arguments.
- **Reuse `ownership_override` as the `dumprestart` gate.** verified: it is already a
  parameter of both. judgment: it authorizes bypassing an ADR 0011 ownership rejection — a
  different question with its own audit record — and overloading it would let a caller who
  needed one bypass acquire the other silently.
- **Add `restart` and `operation` to the decommission tool as well.** verified:
  `decommission.py:479` calls the same builder. judgment: that workflow deletes the partition
  afterwards, so `restart=true` contradicts it and a dump has nowhere to go; epic #871 freezes
  that path's document.
- **Do nothing.** verified: `restart` and `operation` are fixed at `"false"` and `"shutdown"`
  at `jobs/requests.py:113-114`, so `osshutdown`, `dumprestart` and every restarting PowerOff
  are unreachable, and kdive's `cycle`, `reset` and graceful actions have no mapping.
