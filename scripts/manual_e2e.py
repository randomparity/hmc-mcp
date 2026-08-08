"""Manual harness: run the real CLI against a mocked HMC over HTTP."""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

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
        <MachineTypeModelSerialNumber>
          <MachineType>9179</MachineType><Model>MHD</Model><SerialNumber>06064FV</SerialNumber>
        </MachineTypeModelSerialNumber>
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


class H(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_PUT(self):
        if self.path == "/rest/api/web/Logon":
            self._send(LOGON)
        else:
            self._send(b"", 404)

    def do_DELETE(self):
        self._send(b"", 204)

    def do_GET(self):
        if self.path == "/rest/api/uom/ManagedSystem":
            self._send(SYSTEMS)
        elif self.path == "/rest/api/uom/LogicalPartition":
            self._send(LPARS)
        else:
            self._send(b"", 404)

    def _send(self, body, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/xml")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    srv = HTTPServer(("127.0.0.1", 12443), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    import subprocess, os
    env = dict(os.environ, HMC_HOST="127.0.0.1", HMC_USER="hscroot", HMC_PASSWORD="abc123")
    for cmd in (["uv", "run", "hmc-mcp", "systems", "list"],
                ["uv", "run", "hmc-mcp", "lpars", "list"]):
        print(f"$ {' '.join(cmd)}")
        out = subprocess.run(cmd, env=env, capture_output=True, text=True, cwd="/home/hermes/src/hmc-mcp")
        print(out.stdout)
        if out.returncode != 0:
            print("STDERR:", out.stderr[-2000:])
    srv.shutdown()
