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

import hashlib
import ipaddress
import secrets
import ssl
import struct
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from conftest import live_fixture
from typer.testing import CliRunner

from hmcpctl import cli

RUNNER = CliRunner()

LOGON = b"""<?xml version="1.0"?>
<LogonResponse xmlns="http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/">
  <X-API-Session>tok</X-API-Session>
</LogonResponse>"""

SYSTEMS = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:33333333-3333-4333-8333-333333333333</id>
    <title>ManagedSystem:9179-MHD*06064FV</title>
    <link rel="SELF" href="https://hmc/rest/api/uom/ManagedSystem/33333333-3333-4333-8333-333333333333"/>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <ManagedSystem xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <SystemName>server1</SystemName>
        <State>operating</State>
        <MachineTypeModelAndSerialNumber>
          <MachineType>9179</MachineType>
          <Model>MHD</Model>
          <SerialNumber>06064FV</SerialNumber>
        </MachineTypeModelAndSerialNumber>
        <IPAddress>10.0.0.11</IPAddress>
      </ManagedSystem>
    </content>
  </entry>
</feed>"""

SYSTEM_SUMMARIES = (
    b'[{"UUID":"33333333-3333-4333-8333-333333333333","SystemName":"server1",'
    b'"State":"operating","StateDetail":""}]'
)

LPARS = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:11111111-1111-1111-1111-111111111111</id>
    <title>LogicalPartition</title>
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
    <title>LogicalPartition</title>
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

# The captured V10R3 answer to a PowerOn submission (#1161).
JOB_ENTRY = live_fixture("rest-poweron-submit")["body"].encode()


class _MockHMC(BaseHTTPRequestHandler):
    """Minimal HMC REST stand-in: logon, quick/All, one system and its LPARs, logoff.

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
        request_log = self.server.request_log
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
            self._send(JOB_ENTRY)
        else:
            self._send(b"", 404)

    def do_DELETE(self):
        self._send(b"", 204)

    def do_GET(self):
        # Strip query string for routing — the client may append ?group=...
        path = self.path.split("?")[0]
        if path == "/rest/api/uom/ManagedSystem/33333333-3333-4333-8333-333333333333":
            self._send(SYSTEMS)
        elif path == "/rest/api/uom/ManagedSystem/quick/All":
            self._send(SYSTEM_SUMMARIES)
        elif (
            path
            == "/rest/api/uom/ManagedSystem/33333333-3333-4333-8333-333333333333/LogicalPartition"
        ):
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
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
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
    """A threaded HTTPS mock HMC and its trusted certificate path."""
    certfile, keyfile = _self_signed_cert(tmp_path)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=certfile, keyfile=keyfile)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _MockHMC)
    httpd.request_log = []
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd, certfile
    finally:
        httpd.shutdown()
        httpd.server_close()


def _env(port: int, certificate_path: str) -> dict[str, str]:
    return {
        "HMC_HOST": "127.0.0.1",
        "HMC_PORT": str(port),
        "HMC_USER": "hscroot",
        "HMC_PASSWORD": secrets.token_hex(8),  # the mock HMC accepts any logon
        "HMC_VERIFY_SSL": "true",
        "SSL_CERT_FILE": certificate_path,
    }


def test_systems_list_e2e(mock_hmc):
    server, certificate_path = mock_hmc
    result = RUNNER.invoke(
        cli.app,
        ["systems", "list"],
        env=_env(server.server_address[1], certificate_path),
    )

    assert result.exit_code == 0
    assert "server1" in result.stdout
    assert "operating" in result.stdout
    assert "9179-MHD*06064FV" in result.stdout
    assert "10.0.0.11" in result.stdout


def test_lpars_list_e2e(mock_hmc):
    server, certificate_path = mock_hmc
    result = RUNNER.invoke(
        cli.app, ["lpars", "list"], env=_env(server.server_address[1], certificate_path)
    )

    assert result.exit_code == 0
    assert "aixprod" in result.stdout
    assert "linuxdev" in result.stdout
    assert "running" in result.stdout


def test_lpars_list_json_e2e(mock_hmc):
    server, certificate_path = mock_hmc
    result = RUNNER.invoke(
        cli.app,
        ["lpars", "list", "--json"],
        env=_env(server.server_address[1], certificate_path),
    )

    assert result.exit_code == 0
    assert "11111111-1111-1111-1111-111111111111" in result.stdout
    assert "22222222-2222-2222-2222-222222222222" in result.stdout


def test_lpar_power_on_e2e(mock_hmc):
    server, certificate_path = mock_hmc
    lpar_uuid = "11111111-1111-1111-1111-111111111111"
    result = RUNNER.invoke(
        cli.app,
        ["lpars", "power-on", lpar_uuid, "--force", "--yes"],
        env=_env(server.server_address[1], certificate_path),
    )

    assert result.exit_code == 0
    assert lpar_uuid in result.stdout
    power_on_request = next(
        request
        for request in server.request_log
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


# TLS trust (#1595): with HMC_VERIFY_SSL=true the client trusts its bundled roots
# plus SSL_CERT_FILE / SSL_CERT_DIR, and nothing else. docs/configuration.md
# ("TLS trust") documents exactly these four behaviours.


def _systems_list(server, certificate_path, **overrides):
    env = _env(server.server_address[1], certificate_path)
    env.update(overrides)
    return RUNNER.invoke(cli.app, ["systems", "list"], env=env)


def test_ssl_cert_file_trust_verifies_hmc_e2e(mock_hmc):
    server, certificate_path = mock_hmc

    result = _systems_list(server, certificate_path)

    assert result.exit_code == 0
    assert "server1" in result.stdout


def test_verify_ssl_without_trusted_ca_fails_e2e(mock_hmc, monkeypatch):
    server, certificate_path = mock_hmc
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    env = _env(server.server_address[1], certificate_path)
    del env["SSL_CERT_FILE"]
    # Disable the system store so the test cert (self-signed, not in OS store)
    # is not trusted.  ADR 0222: trust_system_store=false falls back to certifi.
    env["HMC_TRUST_SYSTEM_STORE"] = "false"

    result = RUNNER.invoke(cli.app, ["systems", "list"], env=env)

    assert result.exit_code != 0
    assert "CERTIFICATE_VERIFY_FAILED" in result.output


def test_requests_ca_bundle_alone_does_not_trust_e2e(mock_hmc, monkeypatch):
    server, certificate_path = mock_hmc
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    env = _env(server.server_address[1], certificate_path)
    del env["SSL_CERT_FILE"]
    env["REQUESTS_CA_BUNDLE"] = certificate_path
    # Disable the system store so REQUESTS_CA_BUNDLE alone is provably ignored.
    # ADR 0222: trust_system_store=false falls back to certifi only.
    env["HMC_TRUST_SYSTEM_STORE"] = "false"

    result = RUNNER.invoke(cli.app, ["systems", "list"], env=env)

    assert result.exit_code != 0
    assert "CERTIFICATE_VERIFY_FAILED" in result.output


def test_unterminated_pem_bundle_fails_to_load_e2e(mock_hmc, tmp_path):
    server, certificate_path = mock_hmc
    pem = Path(certificate_path).read_text().rstrip("\n")
    joined = tmp_path / "joined.pem"
    joined.write_text(pem + "\n" + pem + "\n")
    unterminated = tmp_path / "unterminated.pem"
    unterminated.write_text(pem + pem)

    good = _systems_list(server, certificate_path, SSL_CERT_FILE=str(joined))
    bad = _systems_list(server, certificate_path, SSL_CERT_FILE=str(unterminated))

    assert good.exit_code == 0
    assert bad.exit_code != 0
    assert "PEM lib" in bad.output


# SSL_CERT_DIR (#1607): httpx passes capath=SSL_CERT_DIR to ssl.create_default_context,
# which uses OpenSSL's hash-directory format.  Each cert must be accessible under the
# name "<subject-hash>.0" where the hash is the first four bytes of SHA-1(subject DER)
# interpreted as a little-endian uint32 and formatted as 8 lowercase hex digits.


def _ssl_cert_dir_hash(cert_path: str) -> str:
    """Return the OpenSSL subject-hash filename stem for a PEM certificate.

    Algorithm: SHA-1 of the *inner* content of the DER-encoded subject Name
    (i.e. the SEQUENCE content bytes, with the outer tag and length stripped);
    first four bytes reinterpreted as a little-endian uint32; formatted as 8
    lowercase hex digits.  This matches ``openssl x509 -subject_hash -noout``
    and what ``ssl.create_default_context(capath=…)`` expects.
    """
    from cryptography.x509 import load_pem_x509_certificate

    cert = load_pem_x509_certificate(Path(cert_path).read_bytes())
    full_der = cert.subject.public_bytes()
    # Strip the outer SEQUENCE tag (0x30) and its length to get the inner bytes.
    # The length field is one byte when < 0x80 (short form), otherwise the first
    # byte encodes how many subsequent bytes hold the actual length.
    length_byte = full_der[1]
    inner = full_der[2:] if length_byte < 0x80 else full_der[2 + (length_byte & 0x7F) :]
    digest = hashlib.sha1(inner).digest()
    return f"{struct.unpack_from('<I', digest[:4])[0]:08x}"


def test_ssl_cert_dir_hashed_trusts_hmc_e2e(mock_hmc, tmp_path):
    """A hash-named cert in SSL_CERT_DIR lets the client verify the mock HMC."""
    server, certificate_path = mock_hmc
    ssl_dir = tmp_path / "ssl_dir"
    ssl_dir.mkdir()
    hash_name = _ssl_cert_dir_hash(certificate_path)
    (ssl_dir / f"{hash_name}.0").write_bytes(Path(certificate_path).read_bytes())

    # Pass None to remove SSL_CERT_FILE so httpx falls through to SSL_CERT_DIR.
    result = _systems_list(
        server,
        certificate_path,
        SSL_CERT_FILE=None,
        SSL_CERT_DIR=str(ssl_dir),
        # truststore on macOS ignores SSL_CERT_DIR; opt out so httpx reads it.
        HMC_TRUST_SYSTEM_STORE="false",
    )

    assert result.exit_code == 0
    assert "server1" in result.output


def test_ssl_cert_dir_unhashed_trusts_nothing_e2e(mock_hmc, tmp_path):
    """A directory with a bare .pem file (not hash-named) trusts nothing."""
    server, certificate_path = mock_hmc
    ssl_dir = tmp_path / "ssl_dir"
    ssl_dir.mkdir()
    (ssl_dir / "mock_hmc_cert.pem").write_bytes(Path(certificate_path).read_bytes())

    # Pass None to remove SSL_CERT_FILE so httpx falls through to SSL_CERT_DIR.
    result = _systems_list(
        server,
        certificate_path,
        SSL_CERT_FILE=None,
        SSL_CERT_DIR=str(ssl_dir),
        # truststore on macOS ignores SSL_CERT_DIR; opt out so httpx reads it.
        HMC_TRUST_SYSTEM_STORE="false",
    )

    assert result.exit_code != 0
    assert "CERTIFICATE_VERIFY_FAILED" in result.output


def test_ssl_cert_file_wins_over_ssl_cert_dir_e2e(mock_hmc, tmp_path):
    """When both SSL_CERT_FILE and SSL_CERT_DIR are set, SSL_CERT_FILE is used.

    Proof: SSL_CERT_DIR points to an unhashed directory that would fail on its
    own — if SSL_CERT_DIR were consulted the connection would fail.  SSL_CERT_FILE
    points to the valid cert: success proves SSL_CERT_FILE was used, not
    SSL_CERT_DIR.
    """
    server, certificate_path = mock_hmc
    # Unhashed dir: present but not consulted when SSL_CERT_FILE is also set.
    unhashed_dir = tmp_path / "unhashed"
    unhashed_dir.mkdir()
    (unhashed_dir / "mock_hmc_cert.pem").write_bytes(
        Path(certificate_path).read_bytes()
    )

    result = _systems_list(
        server,
        certificate_path,
        SSL_CERT_FILE=certificate_path,
        SSL_CERT_DIR=str(unhashed_dir),
    )

    assert result.exit_code == 0
    assert "server1" in result.output
