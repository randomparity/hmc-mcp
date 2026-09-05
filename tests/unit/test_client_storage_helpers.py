"""Contracts for storage-client response-shape normalization helpers."""

from hmc_mcp.client.client_storage import (
    _extract_optical_media,
    _filter_optical_mappings,
)


def test_extract_optical_media_accepts_documented_and_legacy_shapes():
    entries = [
        {
            "Resource": {
                "MediaRepositories": {
                    "VirtualMediaRepository": {
                        "OpticalMedia": {"VirtualOpticalMedia": [{"MediaName": "a.iso"}]}
                    }
                }
            }
        },
        {"Resource": {"VirtualMediaRepository": {"VirtualOpticalMedia": {"MediaName": "b.iso"}}}},
    ]

    assert _extract_optical_media(entries) == [{"MediaName": "a.iso"}, {"MediaName": "b.iso"}]


def test_filter_optical_mappings_keeps_only_requested_lpar():
    mappings = [
        {
            "Storage": {"VirtualOpticalMedia": {}},
            "AssociatedLogicalPartition": {"href": "/rest/api/uom/LogicalPartition/lpar-1"},
        },
        {"Storage": {"VirtualDisk": {}}},
        {
            "Storage": {"VirtualOpticalMedia": {}},
            "AssociatedLogicalPartition": {"href": "/rest/api/uom/LogicalPartition/lpar-2"},
        },
    ]

    assert _filter_optical_mappings(mappings, "lpar-1") == [mappings[0]]
