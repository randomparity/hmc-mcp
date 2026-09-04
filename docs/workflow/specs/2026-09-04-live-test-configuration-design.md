# Live-test configuration design

## Goal

Make the live-test harness reusable without source edits or publication of
environment identifiers. This implements issue #609 and the user's decisions
recorded in its `WORK:SCOPE` charter.

## Configuration contract

The runner reads all scenario-specific values from `LIVE_TEST_*` entries in the
local `.env`. It requires values for the managed system, persistent and
temporary LPAR names, test user, disk and dry-run resource names, protected
LPAR list, SR-IOV adapter/port/profile inputs, and virtual-media file, names,
bind host, and port. The ISO URL is derived from its configured path, host, and
port; it is not separately configurable.

Each value is non-empty after trimming. Integer and decimal fields must parse,
the HTTP port must be in the TCP port range, the SR-IOV capacity must be
positive, and the protected list must contain at least one name. Configuration
validation reports every invalid or absent key and stops before the runner
creates `Client`. Existing `HMC_*` configuration continues unchanged.

`LiveTestContext` owns the immutable configured fields plus mutable execution
state. Scenario modules receive the existing `RunState` and obtain all
environment-specific values through that context. No scenario module reads
`os.environ` directly.

## Example file

The repository adds `.env.example` with a definition, format, and fictional
sample for every `LIVE_TEST_*` setting. Its sample system, partition, user,
disk, and media values use a unique `example-lt-609` prefix and do not repeat
current source identifiers. It may include commented `HMC_*` connection
guidance, but it does not contain credentials or site-specific endpoint values.

## Error and safety behavior

The runner prints an actionable configuration error naming each required key
that needs correction. It does not run a selected subtask as a way around this
check. Virtual-media allow-list handling continues to add only the configured
host and port. Protected-LPAR refusal uses the configured list.

## Testing

Focused tests will prove that a complete fictional mapping creates a context,
that missing and malformed values produce aggregated actionable errors, and
that configuration failure precedes client construction. Existing scenario
tests will be updated to construct contexts with fictional inputs and will
prove the SR-IOV and virtual-media paths use configured values. A structural
test will ensure `.env.example` defines exactly the required live-test keys and
contains none of the retired source identifiers.

## Scope and constraints

This design changes only the live-test harness and its direct tests and
documentation. It adds no dependency, production API, persistence, or network
behavior. The host is `x86_64`; the project declares no target architecture.
The configuration read is local operator input; strict validation prevents it
from reaching a live tool call in an incomplete form.
