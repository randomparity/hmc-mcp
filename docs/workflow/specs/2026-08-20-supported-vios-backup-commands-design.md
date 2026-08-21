# Supported VIOS backup command design

Issue: [#289](https://github.com/randomparity/hmc-mcp/issues/289)
Decision: [ADR 0060](../../adr/0060-use-supported-vios-backup-commands.md)

## Goal and constraints

Replace three command forms proven absent on HMC V10R3 SP1060 and V11R2 SP1120 with the supported
VIOS backup CLI. Python 3.11 remains the floor. Host verification is arm64; declared targets are
amd64, arm64, and ppc64le; the host is included. Add no dependency, compatibility shim, live-HMC
mutation, authorization change, or full-image restore workflow. `just verify` is the final local
guardrail.

IBM's command references and the issue's live-HMC evidence govern the exact shapes:

- `lsviosbk --filter "vios_uuids=<uuid>"`
- `mkviosbk -t <vios|viosioconfig|ssp> -m <system-name> --uuid <vios-uuid> -f <backup-name>`
- `rstviosbk -t <viosioconfig|ssp> -m <system-name> --uuid <vios-uuid> -f <backup-name> [-r]`

## Public interfaces

Keep `hmc_list_vios_backups(vios_name_or_uuid, profile=None)` unchanged.

Replace the other signatures with:

```python
def hmc_backup_vios(
    system_name_or_uuid: str,
    vios_name_or_uuid: str,
    backup_name: str,
    backup_type: BackupType = "vios",
    profile: str | None = None,
) -> str: ...

def hmc_restore_vios(
    system_name_or_uuid: str,
    vios_name_or_uuid: str,
    backup_name: str,
    backup_type: Literal["viosioconfig", "ssp"],
    restart_if_required: bool = False,
    profile: str | None = None,
) -> str: ...
```

The order is the user-approved system, VIOS, backup-name shape. Restore type is required because no
safe default is evidenced. Backup retains `vios` as its default because that is the existing public
default and `mkviosbk` supports it. `restart_if_required` maps only to `-r`; false emits no flag.

## Resolution and command construction

Listing retains the existing VIOS-name-to-UUID resolution and result parser. Its builder changes to
the supported command and quotes the complete `vios_uuids=<uuid>` filter value.

Backup and restore use one async helper that opens the selected profile's REST client and resolves
the selectors before SSH. A direct system name remains the CLI `-m` value. A system UUID is fetched
once and converted to its `MachineTypeModelSerialNumber`; nested machine-type/model/serial fields
serialize as `tttt-mmm*sssssss`, while an already rendered nonblank MTMS passes through. Missing or
malformed MTMS fails before SSH rather than degrading a unique UUID to a possibly duplicated name.
The helper resolves `vios_name_or_uuid` through
`resolve_vios_uuid(..., system_name_or_uuid=system_name_or_uuid)`. The system CLI identity, VIOS
UUID, type, and backup name are each shell-quoted where they enter the SSH string. REST resolution
completes before SSH runs. An unknown system UUID or a VIOS name absent from that system fails with
an actionable selector error.

The existing backup-name validator becomes command-neutral and runs for backup and restore. It
rejects empty or padded names, `/`, `\\`, dots-only values, and a leading dash. It deliberately
does not add IBM's documented length or character grammar, preserving ADR 0044's narrow-refusal
decision. Backup type retains `vios`, `viosioconfig`, and `ssp`; restore type accepts only
`viosioconfig` and `ssp`. Invalid values fail before REST or SSH.

## Error behavior and compatibility

All validation errors name the operation, rejected input, and permitted values or repair. REST and
SSH failures retain existing exception behavior. Raw successful HMC stdout remains the return value
for backup and restore; listing retains `list[dict[str, str]]`.

This is a replacement, not a migration. Old positional calls fail at Python/MCP validation rather
than being reinterpreted. Generated MCP descriptions and schemas must show the new requiredness and
must contain no old command names. Existing Python exports remain under the same function names.

## Threat model

### Boundaries and actors

Authenticated MCP, CLI, and Python callers control system, VIOS, type, name, restart flag, and
profile. REST responses supply resolved HMC names and UUIDs. Those values cross into an SSH command
executed with configured HMC credentials. This design widens the existing command-building boundary
with system name, backup name on creation, restore type, and restart choice; it adds no new entry
point. The authenticated caller is untrusted for command text and target choice. The configured HMC
and its REST identity data are trusted peers; credentials are trusted configuration.

### Controls

- Exact `Literal`/set validation bounds both type arguments before external calls.
- The existing narrow catalog-name validator prevents option and path-shaped file values.
- Existing system/VIOS resolvers bind a VIOS name to the explicit managed-system scope.
- UUID-to-MTMS conversion preserves a unique managed-system selector when user-defined names
  collide and fails closed if the unique CLI identity cannot be obtained.
- `shlex.quote` encodes every caller-controlled or HMC-returned string as one remote-shell word.
- Existing tool metadata and dispatch authorization continue to govern targets; restore remains
  non-exhaustive because `ssp` can affect a cluster.
- Errors may disclose public selectors and HMC diagnostics but never credentials.

No new authorization guarantee is claimed. Races with another HMC operator, rollback of backup or
restore, availability of catalog entries, HMC-side retention, and full-image restoration are out of
scope. Live mutation is excluded; mocked exact-command tests plus the recorded HMC help are the
available proof.

## Verification

Focused tests must first fail against the old commands and signatures, then pass after the change.
They cover exact list filtering, direct system-name and UUID-to-MTMS resolution, duplicate-name
safety, missing/malformed MTMS failure, system-scoped VIOS-UUID resolution, all valid backup types,
both restore types, required restore type, optional `-r`, invalid type/name refusal before external
calls, shell quoting for every dynamic field, raw-output preservation, profile routing, destructive
scope forwarding, and rendered lifecycle/schema descriptions. Sweep all repository callers so no
old positional form or command spelling remains outside historical design records that explicitly
describe the defect. Run `just test`, `just smoke`, and bare `just verify`.
