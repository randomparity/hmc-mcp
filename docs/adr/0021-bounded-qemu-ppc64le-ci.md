# 0021: Use bounded QEMU for ppc64le CI

## Status

Accepted

## Context

The project supports amd64, arm64, and ppc64le, but GitHub-hosted Linux runners cover only the
first two architectures natively. Native Power capacity adds external ownership, credentials,
availability, and cost. The application is Python and is not strongly coupled to system internals,
so instruction-level emulation provides useful compatibility evidence at lower operational cost.

## Decision

Run amd64 on `ubuntu-24.04` and arm64 on `ubuntu-24.04-arm`. Run one separate ppc64le job on
`ubuntu-24.04` using Docker's SHA-pinned QEMU setup action, a digest-pinned binfmt image restricted
to `ppc64le`, and a digest-pinned Ubuntu 24.04 ppc64le container. The container uses Ubuntu's
Python 3.12 and a pinned Rust toolchain so locked development tools without ppc64le wheels can be
built. It verifies `uname -m` before invoking the canonical `just verify` recipe. Every job retains read-only
repository permissions and an explicit timeout.

The repository maintainers own workflow and pin updates. GitHub owns native hosted-runner
isolation and availability; Docker and Ubuntu own the pinned QEMU and container artifacts.
Checkout disables credential persistence, the mounted checkout contains no persisted repository
credential, and the workflow passes no GitHub token or other secret into the ppc64le container.

QEMU setup performs privileged, VM-scoped binfmt registration before public pull-request code runs
in the emulated container. The ephemeral GitHub-hosted VM, not the disposable container, is the
security boundary. This job must not run on a self-hosted or persistent runner.

## Consequences

The hosted matrix supplies native amd64 and arm64 evidence plus ppc64le userspace instruction and
dependency compatibility evidence. QEMU is slower than native Power and cannot establish native
timing, kernel, device, virtualization, or performance behavior. A QEMU or registry outage fails
the bounded ppc64le job without falling back to cross-compilation or silently skipping coverage.

GitHub-hosted public-repository runners have no project-managed infrastructure charge. Registry
downloads and GitHub availability remain external dependencies. Pin updates are explicit reviewed
maintenance.

## Considered & rejected

- **IBM-hosted native Power runners.** Stronger evidence, but onboarding requires a third-party
  GitHub App, repository administration permissions, an IBMid, and external service ownership.
- **Self-hosted Power.** Native execution does not justify persistent infrastructure, credentials,
  isolation work, and cost for this application.
- **Cross-compilation only.** It never executes the package and therefore does not satisfy the
  ppc64le execution requirement.
- **No ppc64le arm.** It leaves a declared target untested.
