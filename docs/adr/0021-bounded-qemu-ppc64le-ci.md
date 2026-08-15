# 0021: Retain bounded QEMU for release-artifact CI

## Status

Accepted

## Context

The project supports amd64, arm64, and ppc64le, but GitHub-hosted Linux runners cover only the
first two architectures natively. Native Power capacity adds external ownership, credentials,
availability, and cost. Bounded QEMU proved real ppc64le userspace execution, but building locked
development tools from source exceeded its 30-minute pull-request budget. That recurring cost is
not justified on every pull request and is better reserved for later release artifacts and wheels.

## Decision

Run active pull-request checks for amd64 on `ubuntu-24.04` and arm64 on `ubuntu-24.04-arm`. Do not
execute or require a ppc64le pull-request check. Retain the reviewed ppc64le job as explicitly
delimited commented YAML in the workflow for later release-artifact and wheel verification.

The retained template runs on `ubuntu-24.04` using Docker's SHA-pinned QEMU setup action, a
digest-pinned binfmt image restricted to `ppc64le`, and a digest-pinned Ubuntu 24.04 ppc64le
container. Its Ubuntu package view is fixed to a snapshot timestamp. The container verifies
`uname -m` before invoking the canonical `just verify` recipe. The template retains read-only
permissions, disabled checkout credential persistence, no secret forwarding, and a 30-minute
timeout. Executing it again requires a reviewed change that removes the comment prefix and adapts
the verification input to bounded release artifacts or wheels.

The repository maintainers own workflow and pin updates. GitHub owns native hosted-runner
isolation and availability; Docker and Ubuntu own the pinned QEMU and container artifacts.
Checkout disables credential persistence, the mounted checkout contains no persisted repository
credential, and the workflow passes no GitHub token or other secret into the ppc64le container.

When activated for later release work, QEMU setup performs privileged, VM-scoped binfmt
registration before repository code runs in the emulated container. The ephemeral GitHub-hosted
VM, not the disposable container, is the security boundary. The template must not be activated on
a self-hosted or persistent runner.

## Consequences

Pull requests receive native amd64 and arm64 evidence only; they make no ppc64le coverage claim.
The inactive template preserves the reviewed QEMU trust boundary without consuming per-PR runner
time. Later release work can reactivate it for bounded artifact execution. QEMU remains unable to
establish native timing, kernel, device, virtualization, or performance behavior.

GitHub-hosted public-repository runners have no project-managed infrastructure charge. The Ubuntu
snapshot makes package resolution repeatable while Canonical retains it; Canonical promises at
least two years of retention, so snapshot refreshes are explicit reviewed maintenance. Registry
downloads and GitHub availability remain external dependencies.

## Considered & rejected

- **IBM-hosted native Power runners.** Stronger evidence, but onboarding requires a third-party
  GitHub App, repository administration permissions, an IBMid, and external service ownership.
- **Self-hosted Power.** Native execution does not justify persistent infrastructure, credentials,
  isolation work, and cost for this application.
- **Cross-compilation only.** It never executes the package and therefore does not satisfy the
  ppc64le execution requirement.
- **Active ppc64le QEMU on every pull request.** Hosted evidence reached real ppc64le execution,
  but source-building the locked verification tools exceeded the 30-minute budget.
- **Delete the ppc64le job.** That discards reviewed pins and isolation controls needed by later
  release-artifact and wheel verification.
