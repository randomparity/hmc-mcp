"""End-to-end CLI tests: the real client against a mocked HMC over HTTPS.

The unit tests mock at the client boundary (``client_from_env`` / respx), so
the full config → HMCClient → httpx → TLS → XML-parse → CLI-output stack is
only exercised here, against a threaded mock HMC speaking TLS with a
throwaway self-signed cert.

This replaces the old ``scripts/manual_e2e.py`` harness, which CI never ran and
which had rotted: the client forces ``https://`` (``HMCConfig.base_url``) but
the mock spoke plain HTTP, so every invocation failed with an SSL error.
"""

from __future__ import annotations

import ipaddress
import ssl
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hmc_mcp import cli

RUNNER = CliRunner()

LOGON = b"""<?xml version="1.0"?>
<LogonResponse xmlns="http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/">
  <X-API-Session>tok</X-API-Session>
</LogonResponse>"""

SYSTEMS = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:sys-uuid-1</id>
    <title>ManagedSystem:9179-MHD*06064FV</title>
    <link rel="SELF" href="https://hmc/rest/api/uom/ManagedSystem/sys-uuid-1"/>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <ManagedSystem xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <SystemName>server1</SystemName>
        <State>operating</State>
        <MachineTypeModelSerialNumber>9179-MHD*06064FV</MachineTypeModelSerialNumber>
        <IPAddress>10.0.0.11</IPAddress>
      </ManagedSystem>
    </content>
  </entry>
</feed>"""

LPARS = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:11111111-1111-1111-1111-111111111111</id>
    <title>LogicalPartition:aixprod</title>
    <link rel="SELF" href="https://hmc/rest/api/uom/LogicalPartition/11111111-1111-1111-1111-111111111111"/>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <LogicalPartition xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <PartitionName>aixprod</PartitionName>
        <PartitionID>3</PartitionID>
        <PartitionState>running</PartitionState>
        <PartitionType>AIX/Linux</PartitionType>
        <OperatingSystemVersion>AIX 7.3</OperatingSystemVersion>
        <ResourceMonitoringControlState>active</ResourceMonitoringControlState>
      </LogicalPartition>
    </content>
  </entry>
  <entry>
    <id>urn:uuid:22222222-2222-2222-2222-222222222222</id>
    <title>LogicalPartition:linuxdev</title>
    <link rel="SELF" href="https://hmc/rest/api/uom/LogicalPartition/22222222-2222-2222-2222-222222222222"/>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <LogicalPartition xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <PartitionName>linuxdev</PartitionName>
        <PartitionID>4</PartitionID>
        <PartitionState>not activated</PartitionState>
        <PartitionType>AIX/Linux</PartitionType>
      </LogicalPartition>
    </content>
  </entry>
</feed>"""

JOB_FEED = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:job-uuid-999</id>
    <title>Job:PowerOn</title>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <Job xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <JobID>job-uuid-999</JobID>
        <Status>RUNNING</Status>
        <RequestedOperation>PowerOn</RequestedOperation>
      </Job>
    </content>
  </entry>
</feed>"""


class _MockHMC(BaseHTTPRequestHandler):
    """Minimal HMC REST stand-in: logon, the two list feeds, logoff.

    ``protocol_version`` is HTTP/1.1 and every response carries an explicit
    ``Content-Length``: httpx (the real client) needs the length to know where
    the body ends, and the request body must be drained before the next request
    on the keep-alive connection.  Without these the TLS read fails with
    ``httpx.ReadError``.
    """

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _drain_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def do_PUT(self):
        body = self._drain_body()
        request_log = getattr(self.server, "request_log")
        request_log.append(
            {
                "path": self.path,
                "session": self.headers.get("X-API-Session"),
                "content_type": self.headers.get("Content-Type"),
                "accept": self.headers.get("Accept"),
                "body": body,
            }
        )
        if self.path == "/rest/api/web/Logon":
            self._send(LOGON)
        elif self.path == (
            "/rest/api/uom/LogicalPartition/"
            "11111111-1111-1111-1111-111111111111/do/PowerOn"
        ):
            self._send(JOB_FEED, 202)
        else:
            self._send(b"", 404)

    def do_DELETE(self):
        self._send(b"", 204)

    def do_GET(self):
        # Strip query string for routing — the client may append ?group=...
        path = self.path.split("?")[0]
        if path == "/rest/api/uom/ManagedSystem":
            self._send(SYSTEMS)
        elif path == "/rest/api/uom/LogicalPartition":
            self._send(LPARS)
        else:
            self._send(b"", 404)

    def _send(self, body, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()


def _self_signed_cert(tmp_path: Path) -> tuple[str, str]:
    """Generate a throwaway self-signed cert for 127.0.0.1 (mock HMC TLS)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    certfile = tmp_path / "mock_hmc_cert.pem"
    keyfile = tmp_path / "mock_hmc_key.pem"
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return str(certfile), str(keyfile)


@pytest.fixture
def mock_hmc(tmp_path):
    """A threaded HTTPS mock HMC on an ephemeral port; yields the server."""
    certfile, keyfile = _self_signed_cert(tmp_path)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=certfile, keyfile=keyfile)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _MockHMC)
    httpd.request_log = []
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()


def _env(port: int) -> dict[str, str]:
    return {
        "HMC_HOST": "127.0.0.1",
        "HMC_PORT": str(port),
        "HMC_USER": "hscroot",
        "HMC_PASSWORD": "abc123",  # fake credential for the mock HMC
        "HMC_VERIFY_SSL": "false",
    }


def test_systems_list_e2e(mock_hmc):
    result = RUNNER.invoke(
        cli.app, ["systems", "list"], env=_env(mock_hmc.server_address[1])
    )

    assert result.exit_code == 0
    assert "server1" in result.stdout
    assert "operating" in result.stdout
    assert "9179-MHD*06064FV" in result.stdout
    assert "10.0.0.11" in result.stdout


def test_lpars_list_e2e(mock_hmc):
    result = RUNNER.invoke(
        cli.app, ["lpars", "list"], env=_env(mock_hmc.server_address[1])
    )

    assert result.exit_code == 0
    assert "aixprod" in result.stdout
    assert "linuxdev" in result.stdout
    assert "running" in result.stdout


def test_lpars_list_json_e2e(mock_hmc):
    result = RUNNER.invoke(
        cli.app,
        ["lpars", "list", "--json"],
        env=_env(mock_hmc.server_address[1]),
    )

    assert result.exit_code == 0
    assert "11111111-1111-1111-1111-111111111111" in result.stdout
    assert "22222222-2222-2222-2222-222222222222" in result.stdout


def test_lpar_power_on_e2e(mock_hmc):
    lpar_uuid = "11111111-1111-1111-1111-111111111111"
    result = RUNNER.invoke(
        cli.app,
        ["lpars", "power-on", lpar_uuid, "--yes"],
        env=_env(mock_hmc.server_address[1]),
    )

    assert result.exit_code == 0
    assert lpar_uuid in result.stdout
    power_on_request = next(
        request
        for request in mock_hmc.request_log
        if request["path"].endswith("/do/PowerOn")
    )
    assert power_on_request["path"] == (
        f"/rest/api/uom/LogicalPartition/{lpar_uuid}/do/PowerOn"
    )
    assert power_on_request["session"] == "tok"
    assert power_on_request["content_type"] == (
        "application/vnd.ibm.powervm.web+xml; type=JobRequest"
    )
    assert power_on_request["accept"] == "application/atom+xml"
    body = power_on_request["body"].decode()
    assert "<OperationName" in body and ">PowerOn<" in body
    assert "<GroupName" in body and ">LogicalPartition<" in body
