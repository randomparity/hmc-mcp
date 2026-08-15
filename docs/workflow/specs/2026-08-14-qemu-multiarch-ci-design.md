# QEMU multi-architecture CI design

## Scope

Issue #161 adds architecture execution to hosted CI. GitHub-hosted `ubuntu-24.04` and
`ubuntu-24.04-arm` provide native amd64 and arm64 execution. The reviewed bounded ppc64le QEMU job
is retained as commented workflow YAML for later release-artifact and wheel testing, but does not
execute or become required on pull requests. Artifact build/install work remains owned by #162
and #163. [ADR 0021](../../adr/0021-bounded-qemu-ppc64le-ci.md) governs the runner decision.

## Workflow

The native matrix contains exactly five entries: the existing amd64 coverage on Python
3.11–3.14, all paired with `ubuntu-24.04`, plus one arm64 entry on Python 3.11 paired with
`ubuntu-24.04-arm`. Every native entry runs `just verify`; the Python lifecycle drift job remains
unchanged. Issue #161 does not create the full architecture-by-Python Cartesian product; #163 owns
that expansion.

The inactive ppc64le template runs on `ubuntu-24.04`, installs only the ppc64le binfmt handler through
`docker/setup-qemu-action`, and builds a digest-pinned Ubuntu 24.04 ppc64le verification image.
The action commit, binfmt image, base image, Ubuntu package snapshot, uv version, uv archive
checksum, and Rust toolchain version are immutable in the repository. Canonical promises at least
two years of snapshot retention; refreshing the timestamp is reviewed maintenance. Ubuntu's
Python 3.12 executes the project. The Rust toolchain builds locked development tools that do not
publish ppc64le wheels. The container
asserts `uname -m` equals `ppc64le`, trusts only the fixed `/workspace` bind mount for Git's
cross-UID ownership check, synchronizes the locked verification dependencies, then runs the
canonical `just verify` recipe.
Cross-compilation never counts as execution.

All active verification jobs have explicit timeouts. The ppc64le template retains its 30-minute
timeout but is wholly commented and therefore creates no check. Reactivation belongs to reviewed
release-artifact or wheel work; a missing runner, artifact, emulator, package, architecture
assertion, dependency, or test must then fail its exact named arm. There is no fallback
architecture, retry loop, or publication step.

## Ownership and operations

- Repository maintainers own workflow changes, digest/action updates, and failure triage.
- GitHub owns native runner lifecycle, VM isolation, availability, and public-project runner cost.
- Docker and Ubuntu own availability of the immutable QEMU and Ubuntu artifacts.
- Each active GitHub-hosted job receives a fresh VM; an activated ppc64le container is discarded
  after the job.
- Workflow permissions remain `contents: read`. Checkout does not persist credentials, and the
  container receives no GitHub token, SSH material, cloud credential, or host Docker socket.
- Native runner outages are visible as failed architecture arms. When the ppc64le template is
  activated, registry or emulator outages must likewise fail its named arm; maintainers verify
  replacements and update pins in review.
- GitHub-hosted runners are free for this public repository. Network transfer and registry limits
  are provider-controlled residuals; the repository provisions no paid runner capacity.

## Emulation limits

The retained template is capable of userspace instruction execution, Python dependency
installation, tests, MCP smoke behavior, and CLI loading under the ppc64le ABI. Pull requests do
not run it and therefore claim no ppc64le evidence. When activated, it does not prove native Power
timing, performance, kernel behavior, devices, hardware virtualization, or concurrency behavior
that depends on native scheduling. Failures suspected to be QEMU-specific require native Power
reproduction before claiming a product defect; passing QEMU does not claim native equivalence.

## Threat model

Pull-request code is untrusted and executes only in the active native GitHub-hosted jobs. The
commented ppc64le template performs no privileged registration and executes no code. If activated
for release work, it adds VM-scoped binfmt registration bounded to the single job and `ppc64le`
platform. Immutable action and image references prevent tag movement; the uv archive checksum
prevents substituted tool bytes. The container gets source content but no repository credential,
and global workflow permissions remain read-only.

The design trusts GitHub's VM isolation, Docker's pinned action and binfmt image, Ubuntu's pinned
base image, and TLS availability for Ubuntu packages and the pinned uv archive. It does not defend
against compromise of bytes already named by an accepted digest/SHA or flaws in GitHub's host
isolation. Native microarchitectural and kernel-specific threats are outside the evidence QEMU can
provide and are stated as residuals rather than silently claimed as covered.

## Verification

Workflow tests prove the exact five-entry active native matrix, absence of an active ppc64le job,
and the exact delimited commented template. They recover the commented YAML as text and prove its
runner, timeout, action and image pins, ppc64le-only registration, runtime architecture assertion,
and `just verify` delegation. Credential-boundary assertions require `persist-credentials: false`,
exactly `contents: read` permissions, no `secrets.*` reference or token/credential environment or
action input in the ppc64le job, and no GitHub token, SSH/cloud credential, Docker socket, or other
host credential mount in its container invocation. Tests also require the ppc64le Python and Rust
build prerequisites and explicit emulation limits.
The repository guardrail remains `just verify`.

The retained ppc64le environment synchronizes the locked dependency graph without `prek`, which is used
only by the separate `just setup` recipe to install local Git hooks. Disabling uv's implicit
resynchronization then runs the canonical `just verify` recipe unchanged. This avoids compiling a
hook installer under QEMU without removing any command or dependency exercised by the guardrail.
