import json
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
RECIPE = ROOT / "docs/recipes/system-inventory.md"
READ_COMMANDS = {
    "systems list --json",
    'systems show "$SYSTEM" --json',
    'systems summary "$SYSTEM" --json',
    'raw get "/rest/api/uom/ManagedSystem/$SYSTEM"',
    'network list-switches "$SYSTEM" --json',
    'network list-networks "$SYSTEM" --json',
    'network list-bridges "$SYSTEM" --json',
    'network list-dedicated-pcie-slots "$SYSTEM" --json',
    'network list-sriov-adapters "$SYSTEM" --json',
    'network list-sriov-physical-ports "$SYSTEM" --json',
    'network list-sriov-logical-ports "$SYSTEM" --json',
    'network list-fc-ports "$SYSTEM" --json',
    'lpars list --system "$SYSTEM" --json',
    'lpars show "$lpar" --json',
    'lpars summary "$lpar" --json',
    'adapters list "$lpar" --type ClientNetworkAdapter --json',
    'adapters list "$lpar" --type VirtualSCSIClientAdapter --json',
    'adapters list "$lpar" --type ClientFibreChannelAdapter --json',
    'vios list --system "$SYSTEM" --json',
    'raw get "/rest/api/uom/VirtualIOServer/$vios"',
    'storage list-vgs "$vios" --system "$SYSTEM" --json',
    'storage list-mappings "$vios" --system "$SYSTEM" --json',
    'storage get-media-repo "$vios" "$vg" --system "$SYSTEM" --json',
    'storage list-optical-media "$vios" "$vg" --system "$SYSTEM" --json',
    'raw get "/rest/api/uom/VirtualIOServer/$vios/VolumeGroup/$vg"',
    'cluster list --json',
    'cluster list-ssps --json',
    'raw get "/rest/api/uom/SharedStoragePool/$ssp"',
}


def test_system_inventory_recipe_has_required_read_only_contract() -> None:
    recipe = RECIPE.read_text()
    for marker in ("Sensitive data", "umask 077", "raw HMC XML", "error.txt",
                   "dedicated-pcie-slots.json", "sriov-logical-ports.json",
                   "vfc-ports.json", "vios-$vios.raw.xml",
                   "vg-$vg.raw.xml", "shared-storage-pools.json",
                   "ssp-$ssp.raw.xml",
                   "VIOS partition ID / server slot", "Candidate disk name",
                   "Free-space evidence"):
        assert marker in recipe
    commands = set(re.findall(r"hmc-mcp ([^\n\x60]+)", recipe))
    assert commands == READ_COMMANDS


def test_system_inventory_recipe_is_linked_from_documentation_index() -> None:
    assert "[Read-only system inventory recipe](recipes/system-inventory.md)" in (
        ROOT / "docs/index.md"
    ).read_text()


def test_system_name_selector_matches_the_system_list_json_shape() -> None:
    recipe = RECIPE.read_text()
    systems = json.loads(
        '[{"UUID": "system-uuid", "Resource": {"SystemName": "example-system"}}]'
    )

    selected = next(
        item["UUID"]
        for item in systems
        if item["Resource"]["SystemName"] == "example-system"
    )

    assert selected == "system-uuid"
    assert 'select(.Resource.SystemName == $name) | .UUID' in recipe
