"""Tool-layer tests for media repository and optical media tools."""

import httpx

VIOS_UUID = "00000000-0000-0000-0000-000000000003"
VG_UUID = "vg-uuid-0001"


def _hmc_env(monkeypatch) -> None:
    """Set env vars so HMCConfig() succeeds inside the tool."""
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "abc123")


def _feed(uuid: str, rtype: str, **fields: str) -> str:
    """A single-resource Atom feed; {fields} render as resource elements."""
    body = "\n".join(
        f'        <{k} xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">{v}</{k}>'
        for k, v in fields.items()
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:{uuid}</id>
    <title>{rtype}:{uuid}</title>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <{rtype} xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
{body}
      </{rtype}>
    </content>
  </entry>
</feed>
"""


VG_FEED_WITH_REPO = _feed(
    VG_UUID,
    "VolumeGroup",
    VolumeGroupUUID=VG_UUID,
    GroupName="VMLibrary",
)


def test_get_media_repository(monkeypatch, mock_hmc):
    """hmc_get_media_repository GETs the VolumeGroup and returns the repository."""
    _hmc_env(monkeypatch)

    route = mock_hmc.get(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup/{VG_UUID}"
    ).mock(return_value=httpx.Response(200, text=VG_FEED_WITH_REPO))

    from hmc_mcp.server_storage import hmc_get_media_repository

    result = hmc_get_media_repository(VIOS_UUID, VG_UUID)

    assert route.called
    assert result is not None
    assert result["UUID"] == VG_UUID


def test_list_optical_media(monkeypatch, mock_hmc):
    """hmc_list_optical_media GETs the VolumeGroup and extracts optical media."""
    _hmc_env(monkeypatch)

    media_feed = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:{VG_UUID}</id>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <VolumeGroup xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <VolumeGroupUUID>{VG_UUID}</VolumeGroupUUID>
        <VirtualMediaRepository schemaVersion="V1_0">
          <RepositoryName>VMLibrary</RepositoryName>
          <RepositorySize>40960</RepositorySize>
          <VirtualOpticalMedia schemaVersion="V1_0">
            <MediaName>aix.iso</MediaName>
            <MediaSize>1400</MediaSize>
            <MediaType>BLANK</MediaType>
          </VirtualOpticalMedia>
          <VirtualOpticalMedia schemaVersion="V1_0">
            <MediaName>linux.iso</MediaName>
            <MediaSize>2048</MediaSize>
            <MediaType>BLANK</MediaType>
          </VirtualOpticalMedia>
        </VirtualMediaRepository>
      </VolumeGroup>
    </content>
  </entry>
</feed>
"""

    route = mock_hmc.get(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup/{VG_UUID}"
    ).mock(return_value=httpx.Response(200, text=media_feed))

    from hmc_mcp.server_storage import hmc_list_optical_media

    media_list = hmc_list_optical_media(VIOS_UUID, VG_UUID)

    assert route.called
    assert len(media_list) == 2
    assert media_list[0]["MediaName"] == "aix.iso"
    assert media_list[1]["MediaName"] == "linux.iso"


def test_get_media_repository_not_found(monkeypatch, mock_hmc):
    """hmc_get_media_repository returns None when VG not found."""
    _hmc_env(monkeypatch)

    route = mock_hmc.get(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup/missing-uuid"
    ).mock(return_value=httpx.Response(404, text=""))

    from hmc_mcp.server_storage import hmc_get_media_repository

    result = hmc_get_media_repository(VIOS_UUID, "missing-uuid")

    assert route.called
    assert result is None


def test_list_optical_media_empty(monkeypatch, mock_hmc):
    """hmc_list_optical_media returns empty list when no media."""
    _hmc_env(monkeypatch)

    route = mock_hmc.get(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup/{VG_UUID}"
    ).mock(return_value=httpx.Response(200, text=VG_FEED_WITH_REPO))

    from hmc_mcp.server_storage import hmc_list_optical_media

    media_list = hmc_list_optical_media(VIOS_UUID, VG_UUID)

    assert route.called
    assert media_list == []