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

The recipe creates a private local directory, resolves the system UUID from
`systems list --json`, and captures system details and summary. It then gathers
system-scoped network resources, LPAR and VIOS lists, and per-resource details.
For each LPAR it captures the full document, summary, and supported adapter
types. For each VIOS it captures volume groups and mappings. A small shell
helper records unavailable or permission-denied command failures in an adjacent
`.error.txt` file, preserving the category rather than treating it as absent.

Physical I/O and SR-IOV/PCIe details have no required stable projection in this
scope, so the recipe records raw GET XML captures separately. The selector
worksheet names values an operator may take from captures for a future workflow,
but instructs the operator to choose them; it does not name an unmerged recipe.

## Acceptance criteria

- The recipe resolves a UUID from a managed-system name and captures each
  required category in JSON or labelled raw XML.
- Every command is a read command; no `raw post`, creation, deletion, mapping,
  attachment, power, or adapter mutation appears.
- Missing optional capabilities and permission failures leave recorded evidence.
- The worksheet covers system, VIOS, VG, VLAN, VIOS ID/slot, candidate disk,
  and free-space evidence without selecting a value.
- The documentation index and structural tests make the recipe discoverable and
  guard its read-only contract and essential sections.

## Testing

Add a structural test that reads the recipe and index. It checks the required
sections, local-data warning, capture categories, worksheet fields, expected
read commands, and absence of known mutation command forms.
