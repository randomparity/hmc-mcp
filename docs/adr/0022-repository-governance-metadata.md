# 0022: Keep repository governance in canonical files and package metadata

## Status

Accepted

## Context

The public repository has no license, contribution guide, or vulnerability-reporting policy.
Its package metadata and README therefore cannot direct users to those policies. The repository
owner has enabled GitHub private vulnerability reporting, providing a private channel without
publishing a security mailbox or introducing another service.

## Decision

License the project under the MIT License using `LICENSE` as the canonical text. Keep contribution
instructions in `CONTRIBUTING.md` and vulnerability-reporting instructions in `SECURITY.md`.
Security reports use this repository's GitHub private vulnerability-reporting form; public issues
are not an accepted vulnerability channel.

Declare the SPDX license expression and license file in PEP 621 metadata. Publish direct project
URLs for the repository, contribution guide, and security policy, and link the same canonical
files from a short README governance section. Focused tests enforce the presence and agreement of
these files, links, and metadata.

## Consequences

Users can discover the same policies from a source checkout, the README, and installed package
metadata. GitHub authentication and repository availability are external dependencies for private
reports. Maintainers must keep the repository setting enabled while `SECURITY.md` names it.

This decision does not create publication credentials, a package-release workflow, response-time
promises, or a second vulnerability inbox.

## Considered & rejected

- **Publish a maintainer email address.** It adds a second channel and exposes contact information
  when the repository already provides a private, access-controlled channel.
- **Accept security reports in public issues.** Disclosure before coordination can harm users and
  contradicts the requirement for a genuinely private path.
- **Document policies only in the README.** Package indexes and repository conventions discover
  canonical governance files and project URLs; README-only prose is easier to drift.
- **Add a metadata-validation dependency.** The required contract is deterministic TOML and file
  content that existing Python tooling can test without expanding the dependency surface.

