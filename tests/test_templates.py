"""Tests for the LogicalPartition create/modify document builder."""

import pytest

from hmc_mcp.templates import PARTITION_TYPES, build_lpar_document


def test_minimal_create_document():
    xml = build_lpar_document(name="test")
    assert "<PartitionName" in xml and "test" in xml
    assert "AIX/Linux" in xml
    # No memory/proc blocks when nothing is requested
    assert "PartitionMemoryConfiguration" not in xml
    assert "PartitionProcessorConfiguration" not in xml


def test_memory_config():
    xml = build_lpar_document(
        name="m", min_memory=256, desired_memory=512, max_memory=1024
    )
    assert "<DesiredMemory" in xml and "512" in xml
    assert "<MaximumMemory" in xml and "1024" in xml
    assert "<MinimumMemory" in xml and "256" in xml


def test_shared_processor_config():
    xml = build_lpar_document(
        name="s",
        desired_procs=0.5, max_procs=2.0,
        desired_vcpus=1, max_vcpus=2,
        uncapped=True,
    )
    assert "<HasDedicatedProcessors" in xml and "false" in xml
    assert "SharedProcessorConfiguration" in xml
    assert "DesiredProcessingUnits" in xml and "0.5" in xml
    assert "DesiredVirtualProcessors" in xml
    assert "uncapped" in xml


def test_dedicated_processor_config():
    xml = build_lpar_document(
        name="d", dedicated=True, min_procs=2, desired_procs=2, max_procs=4
    )
    assert "DedicatedProcessorConfiguration" in xml
    assert "<DesiredProcessors" in xml and "2" in xml
    assert "<MaximumProcessors" in xml and "4" in xml
    assert "true" in xml  # HasDedicatedProcessors


def test_partition_id_and_type():
    xml = build_lpar_document(name="x", partition_id=25, partition_type="OS400")
    assert "<PartitionID" in xml and "25" in xml
    assert "OS400" in xml


def test_invalid_partition_type():
    with pytest.raises(ValueError, match="partition_type"):
        build_lpar_document(name="bad", partition_type="Windows")


def test_all_partition_types_accepted():
    for pt in PARTITION_TYPES:
        build_lpar_document(name="ok", partition_type=pt)


def test_modify_document_omits_name_when_none():
    xml = build_lpar_document(name=None, desired_memory=2048)
    assert "PartitionName" not in xml
    assert "2048" in xml
