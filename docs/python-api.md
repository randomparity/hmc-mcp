# Python library

[Documentation index](index.md)

Install the library from a local checkout into your application's virtual environment:

```bash
uv pip install /path/to/hmc-mcp
```

The bare package includes the reusable API. The `app` extra is only needed for the CLI and
MCP server. Configure the [HMC environment variables](environment-variables.md) before
running the example below.

## Reusable Python API

Import reusable library code only from `hmc_mcp.api`. This example reads connection settings from
the `HMC_*` environment variables linked above, constructs a client, and runs an exported domain
operation:

```python
import asyncio

from hmc_mcp.api import HMCClient, HMCConfig, fetch_capacity_report


async def main() -> None:
    async with HMCClient(HMCConfig()) as hmc:
        for system in await fetch_capacity_report(hmc):
            print(system)


asyncio.run(main())
```

`hmc_mcp.api` is the only supported reusable-library import path. Its explicit `__all__` is the
complete compatibility manifest. For `HMCClient`, only `__init__`, `__aenter__`, `__aexit__`,
`is_logged_on`, `logon`, and `logoff` are supported lifecycle members. Other import paths, generic
UOM helpers, inherited mixin methods, XML and parser helpers, SSH primitives, and CLI and MCP
presentation modules are implementation details. They may remain importable or discoverable, but
they are unsupported and may change without a compatibility release.

The distribution ships a PEP 561 `py.typed` marker, so a type-checker reads the facade's inline
annotations instead of treating every value as `Any`. That covers exactly the surface `__all__`
declares: each export's call signature, the fields and constructor of each exported package-owned
model, the keys of each exported `TypedDict`, each exported exception type, and the members and
values of each exported enum and literal alias. Modules outside `hmc_mcp.api` carry annotations
too, but they are implementation details and their types are not part of the contract.

What the marker does not do is make the open-ended HMC payloads specific. Operations that return a
raw resource mapping are annotated `dict[str, Any]`, and ADR 0029 keeps them that way deliberately
so an IBM-side field addition is not a breaking change — the call is typed, the payload contents
stay opaque. Operations that return a package-owned result model are typed all the way down.

One consequence is worth planning for: the operations annotate the concrete `HMCClient`, so a
type-checker now rejects a duck-typed fake passed in a consumer's own tests even though the call
still runs. ADR 0029 deliberately promises no alternate-client protocol, so pass such a fake through
`typing.cast(HMCClient, fake)` at the call site, or silence that call with
`# type: ignore[arg-type]`.

While hmc-mcp is in `0.x`, strict SemVer applies to this supported surface: removing or renaming an
export, invalidating a compatible call, changing an owned model incompatibly, changing an exported
enum or literal value set, or adding a facade export requires a minor release. Patch releases are
limited to compatible fixes that change neither the export set nor enum and literal value sets.
See [ADR 0029](adr/0029-supported-reusable-python-api-contract.md) for the complete contract.
