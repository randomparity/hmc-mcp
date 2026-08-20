# 0049 — `hmc_upload_iso` accepts only `http(s)` URLs

## Status

Accepted (2026-08-19)

## Context

`hmc_upload_iso(vios_name_or_uuid, vg_uuid, media_name, iso_source, …)` branched
on `iso_source`'s URL scheme. An `http`/`https` value was downloaded; **anything
else was read as a path on the MCP server's own filesystem** and uploaded into
the granted VIOS's media repository:

```python
parsed_url = urlparse(iso_source_str)
is_url = parsed_url.scheme in ("http", "https")
if is_url:
    iso_path, iso_sha256, file_size = await _download_iso_from_url(iso_source_str)
else:
    iso_path = Path(iso_source)
```

The tool is `effect="mutate"`, so an `effects = ["mutate"]` grant reaches it. A
caller holding that grant could therefore have any file the server process can
read — `/etc/passwd`, the HMC credentials in `~/.config/hmc-mcp/config.toml`, an
SSH private key — materialise as an ISO in a media repository they can then list
and mount. Read-back is not even required for the credential case: the upload
itself is the disclosure.

This was reproduced before the change (#261), driving `upload_iso` exactly as the
tool does with every HMC-side call stubbed, and a temp file standing in for the
credential file. The bytes of the host-side file arrived at
`HMCClient._broker_file_upload` unchanged, and the operation returned
`status="uploaded"` with that file's SHA-256 and size.

The access policy cannot close this. No `TargetKind` names "a file on the MCP
server host", so no `targets` allowlist bounds `iso_source` under any design —
which is why `tests/app/test_tool_security.py` classifies it as a *payload
source* rather than a target, and why the classification alone was never a
defence. The behaviour predates #223; what #223 added was the classification that
surfaced it.

## Decision

**`iso_source` admits `http` and `https`, and nothing else. The local-filesystem
branch is removed.**

`operations_storage._require_http_url` is the whole of the validation, and
`upload_iso` calls it as its first statement — before `resolve_vios_uuid`, before
the download, before any `Path` is constructed. Ordering is part of the decision,
not an implementation detail: a check that stats the path first and refuses
afterwards still discloses existence and permission through its error text and
its timing. The refusal message is derived only from the caller's own input and
is identical for a readable file, an unreadable one, and a path that does not
exist.

The reasons for admitting only URLs, over bounding the path instead:

- it is the documented use, and the only source the live-test plan's HTTP arm and
  the CLI's help text now describe;
- it adds no configuration surface — no new `HMC_*` variable to define, document
  under `docs/environment-variables.md`, and keep honest;
- it closes path traversal and symlink escape **by construction**. There is no
  path branch left for either to reach, so there is no check that has to stay
  correct as the surrounding code changes.

## Consequences

**This breaks any caller passing a local path today.** That is the accepted cost,
and it is not hypothetical:

- `hmc-mcp storage upload-iso <vios> <vg> <name> <source>` accepted a local path,
  and its two `CliRunner` tests passed `/tmp/aix.iso`. The CLI is now URL-only;
  its argument help and command docstring say so.
- `scripts/live_test_runner.py` uploaded from `~/Downloads/…` in three of its
  four `hmc_upload_iso` call sites. It now publishes that directory on
  `localhost:18765` — machinery ST18 already had for its deduplication arm — and
  every call site uploads from that URL.

An operator with an ISO on disk and no web server must now serve it. That is the
change's real cost to a user, and the CLI is where it lands hardest, because
there the "MCP server host" and the operator's own machine are the same machine
and the disclosure the change prevents was never available to anyone else. The
alternative — the operation staying dual-source and only the MCP tool refusing
paths — was not taken, because it leaves the vulnerable branch alive in shared
code and makes the safety of the tool a property of its caller.

**This change does not close `hmc_upload_iso`.** It makes the URL branch the
tool's *only* input path and therefore its entire remaining attack surface. The
server still fetches a caller-supplied URL from its own network position, which
reaches instance-metadata endpoints, loopback services, and hosts inside the
server's segment. That is a real defect; it is filed as **#303** and deliberately
left for its own change so each fix stays one reviewable PR. Nobody should read
this ADR, or the PR that carries it, as having made the tool safe to grant
broadly.

`_download_iso_from_url` no longer re-checks the scheme. Its only caller is
`upload_iso`, which admits nothing it would reject, so the second check was
unreachable rather than defensive.

## Considered & rejected

**An allowlisted base directory (`HMC_ISO_DIR`-style): keep local paths, but
require the resolved path to sit under a configured root.**

Rejected. The grounds, separated by what was actually checked:

- *Verified.* No such setting exists today: `HMC_ISO_DIR` appears nowhere in the
  tree, so this is a new `HMCConfig` field, a new row in
  `docs/environment-variables.md` (enforced by `scripts/check_env_vars.py`), and
  a new deployment step for every operator — configuration surface bought to
  preserve a source the documented workflow does not use.
- *Verified.* It does not remove the branch this ADR removes. Both sources stay
  in `upload_iso`, so the tool keeps two input paths and the security of the
  local one becomes a property of a check rather than of the code's shape.
- *Design judgement, not a measurement.* Containment checks of this kind
  (`resolve()` then `is_relative_to(root)`) are a well-known source of TOCTOU and
  symlink-escape defects, and they have to stay correct as the surrounding code
  is edited by people who are not thinking about them. This is the reason the
  maintainer weighed most heavily. It is a judgement about failure modes, not a
  claim that any particular implementation of it was tried here and found broken
  — none was.

**Refusing only in the MCP tool, leaving `operations_storage.upload_iso`
dual-source.** Rejected for the reason given under Consequences: it preserves the
CLI's convenience by leaving the exploitable branch in shared code, one careless
call site away from being reachable again.

## References

- #261 — `hmc_upload_iso` reads an arbitrary path on the MCP server host
- #303 — the URL branch fetches a caller-supplied URL from the server (open)
- `docs/adr/0039-dispatch-time-target-scope.md` — why no `targets` table bounds a
  source outside the HMC
- `docs/adr/0044-containment-decides-unbounded-arguments.md` — the containment
  criterion this argument was classified against
