# System inventory recipe design

## Goal

Provide a copy-and-adapt, read-only capture workflow for one named managed
system before any later provisioning decision.

## Constraints

- Use existing `hmc-mcp` read commands and `--json` output; `raw get` is the
  only raw escape hatch and is explicitly labelled XML.
- Write only local capture files under an operator-chosen directory.
- Do not issue HMC mutations, raw POSTs, resource selection, or depend on PR #777.
- Use generic placeholders and warn against committing or publishing captures.

## Design

The recipe sets `umask 077`, creates a private local directory, resolves the system UUID from
`systems show "$SYSTEM_NAME" --json`, which captures the system in the same
command, and records the console identity and the system summary. Resolution
fails loudly rather than leaving `$SYSTEM` empty for every later command. It then gathers
system-scoped network resources, LPAR and VIOS lists, and per-resource details.
For each LPAR it captures the full document, summary, and supported adapter
types. It also captures the system-wide NPIV/vFC port list. For each VIOS it
captures volume groups, mappings, and the full raw VIOS document; the latter
preserves physical-volume and VIOS Fibre Channel mapping detail that has no
stable CLI projection. Each volume group and Shared Storage Pool is also
captured with its documented full raw GET response, preserving physical-volume,
virtual-disk, media, and logical-unit detail. A small shell helper records
unavailable or permission-denied command failures in an adjacent `.error.txt`
file, preserving the category rather than treating it as absent.

The recipe captures dedicated PCIe slots, SR-IOV adapters, Shared Ethernet
Adapters, and vFC ports with their existing stable CLI projections, and SR-IOV
physical and logical ports once per adapter, which those two commands require. Raw
GET XML is reserved for physical I/O or other fields that those projections do
not expose. The POST-only GetFreePhysicalVolumes job is excluded because the
recipe's read-only contract permits only GETs.
The selector
worksheet names values an operator may take from captures for a future workflow,
but instructs the operator to choose them; it does not name an unmerged recipe.

## Acceptance criteria

- The recipe resolves a UUID from a managed-system name and captures each
  required category in JSON or labelled raw XML.
- Every documented `hmc-mcp` invocation matches an exact allowlist of read
  command forms; raw is limited to `raw get`.
- Missing optional capabilities and permission failures leave recorded evidence.
- The recipe captures documented physical-volume, virtual-disk, SSP, and
  end-to-end vFC mapping detail through read-only GETs or stable read commands.
- The worksheet covers system, VIOS, VG, VLAN, VIOS ID/slot, candidate disk,
  and free-space evidence without selecting a value.
- The documentation index and structural tests make the recipe discoverable and
  guard its read-only contract and essential sections.

## Testing

Add a structural test that reads the recipe and index. It checks the required
sections, local-data warning, capture categories, worksheet fields, expected
read commands, and absence of known mutation command forms.
