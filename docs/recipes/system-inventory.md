# Read-only managed-system inventory

Capture one managed system before considering a later change. Every HMC command below is read-only; this recipe does not select or alter a resource.

> **Sensitive data:** captures can contain names, UUIDs, network topology, and storage detail. Choose a protected local directory. Do not commit or publish it.

## 1. Prepare a protected local capture directory

```bash
export HMC_PROFILE=operator-profile
export SYSTEM_NAME=example-system
export CAPTURE_DIR="$HOME/hmc-inventory-$(date +%Y%m%d-%H%M%S)"
umask 077
mkdir -p "$CAPTURE_DIR"
```

Use capture for optional categories. A denied, unavailable, or failed command leaves an error.txt file as evidence.

```bash
capture() {
  local name=$1
  shift
  if "$@" >"$CAPTURE_DIR/$name" 2>"$CAPTURE_DIR/$name.error.txt"; then
    rm -f -- "$CAPTURE_DIR/$name.error.txt"
  fi
}
```

## 2. Resolve and capture the managed system

```bash
capture systems.json hmc-mcp systems list --json
SYSTEM=$(jq -er --arg name "$SYSTEM_NAME" '.[] | select(.SystemName == $name) | .UUID' "$CAPTURE_DIR/systems.json")
printf '%s\n' "$SYSTEM" >"$CAPTURE_DIR/system.uuid.txt"
capture system.json hmc-mcp systems show "$SYSTEM" --json
capture system-summary.json hmc-mcp systems summary "$SYSTEM" --json
capture managed-system.raw.xml hmc-mcp raw get "/rest/api/uom/ManagedSystem/$SYSTEM"
```

The first three captures are stable CLI JSON for identity, firmware, RAM, CPU capacity, and partition counts. managed-system.raw.xml is **raw HMC XML** for physical-I/O fields that have no stable projection.

## 3. Capture network, PCIe, and SR-IOV inventory

```bash
capture virtual-switches.json hmc-mcp network list-switches "$SYSTEM" --json
capture virtual-networks.json hmc-mcp network list-networks "$SYSTEM" --json
capture network-bridges.json hmc-mcp network list-bridges "$SYSTEM" --json
capture dedicated-pcie-slots.json hmc-mcp network list-dedicated-pcie-slots "$SYSTEM" --json
capture sriov-adapters.json hmc-mcp network list-sriov-adapters "$SYSTEM" --json
capture sriov-physical-ports.json hmc-mcp network list-sriov-physical-ports "$SYSTEM" --json
capture sriov-logical-ports.json hmc-mcp network list-sriov-logical-ports "$SYSTEM" --json
```

These PCIe and SR-IOV files are stable CLI output. Do not replace their normalized capability state with raw XML. Empty JSON lists, unavailable capability, and error.txt files are distinct evidence.

## 4. Capture every LPAR and its adapters

```bash
capture lpars.json hmc-mcp lpars list --system "$SYSTEM" --json
jq -r '.[].UUID' "$CAPTURE_DIR/lpars.json" | while IFS= read -r lpar; do
  capture "lpar-$lpar.json" hmc-mcp lpars show "$lpar" --json
  capture "lpar-$lpar-summary.json" hmc-mcp lpars summary "$lpar" --json
  capture "lpar-$lpar-network.json" hmc-mcp adapters list "$lpar" --type ClientNetworkAdapter --json
  capture "lpar-$lpar-vscsi.json" hmc-mcp adapters list "$lpar" --type VirtualSCSIClientAdapter --json
  capture "lpar-$lpar-vfc.json" hmc-mcp adapters list "$lpar" --type ClientFibreChannelAdapter --json
done
```

Full documents and summaries retain LPAR state, memory, CPU allocation, and description. The adapter files cover network, vSCSI, and vFC types.

## 5. Capture every VIOS and storage state

```bash
capture vios.json hmc-mcp vios list --system "$SYSTEM" --json
jq -r '.[].UUID' "$CAPTURE_DIR/vios.json" | while IFS= read -r vios; do
  capture "vios-$vios-vgs.json" hmc-mcp storage list-vgs "$vios" --system "$SYSTEM" --json
  capture "vios-$vios-mappings.json" hmc-mcp storage list-mappings "$vios" --system "$SYSTEM" --json
  jq -r '.[].uuid' "$CAPTURE_DIR/vios-$vios-vgs.json" | while IFS= read -r vg; do
    capture "vios-$vios-vg-$vg-media-repository.json" hmc-mcp storage get-media-repo "$vios" "$vg" --system "$SYSTEM" --json
    capture "vios-$vios-vg-$vg-optical-media.json" hmc-mcp storage list-optical-media "$vios" "$vg" --system "$SYSTEM" --json
  done
done
```

VIOS files provide identity, partition ID, state, version, volume groups, free-space diagnostics, mappings, and media state. Do not infer a disk name from a mapping.

## 6. Complete the selector worksheet

This is a human decision record for a later workflow. Leave a field blank when evidence is missing, denied, stale, or unsuitable.

| Future input | Saved evidence | Operator choice |
|---|---|---|
| SYSTEM | system.uuid.txt, system.json | managed-system UUID |
| VIOS | vios.json | VIOS UUID and name |
| VG | vios-<UUID>-vgs.json | volume-group UUID |
| VLAN | network JSON files | VLAN and switch ID |
| VIOS partition ID / server slot | vios.json, vSCSI JSON | paired VIOS ID and slot |
| Candidate disk name | mappings and storage evidence | approved unused name |
| Free-space evidence | volume-group JSON and diagnostic | timestamped value |

Review every error.txt file and verify the capture is current before any later change. Inventory informs an operator; it never authorizes or executes a mutation.

