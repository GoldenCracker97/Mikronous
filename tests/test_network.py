import http.server
import json
import socket
import threading

import pytest

from mikronous import network


def test_parse_ip_neigh_and_arp():
    neigh = ("192.168.1.1 dev wlan0 lladdr 11:22:33:44:55:66 REACHABLE\n"
             "192.168.1.9 dev wlan0  FAILED\n"
             "192.168.1.20 dev wlan0 lladdr aa:bb:cc:dd:ee:ff STALE\n")
    rows = network.parse_ip_neigh(neigh)
    assert [r["ip"] for r in rows] == ["192.168.1.1", "192.168.1.20"] and rows[1]["mac"] == "aa:bb:cc:dd:ee:ff"
    arp = ("Interface: 192.168.1.5 --- 0xb\n"
           "  Internet Address      Physical Address      Type\n"
           "  192.168.1.1           11-22-33-44-55-66     dynamic\n"
           "  192.168.1.255         ff-ff-ff-ff-ff-ff     static\n"
           "  224.0.0.22            01-00-5e-00-00-16     static\n")
    rows = network.parse_arp_a(arp)
    assert rows == [{"ip": "192.168.1.1", "mac": "11:22:33:44:55:66", "state": "dynamic"}]


def test_magic_packet():
    pkt = network.magic_packet("AA:bb:cc:dd:ee:ff")
    assert pkt[:6] == b"\xff" * 6 and len(pkt) == 102 and pkt[6:12] == bytes.fromhex("aabbccddeeff")
    with pytest.raises(ValueError):
        network.magic_packet("not-a-mac")
    assert "error" in network.wake_on_lan({"mac": "zz"})


def test_host_check_local_port():
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
    port = srv.getsockname()[1]
    try:
        res = network.host_check({"host": "localhost", "ports": [port, 1]})
        assert res["resolved"] and res["open_ports"] == [port] and 1 in res["closed_ports"]
    finally:
        srv.close()
    assert network.host_check({"host": ""})["error"]
    assert network.host_check({"host": "no-such-host.invalid"})["resolved"] is False


class _Handler(http.server.BaseHTTPRequestHandler):
    def _reply(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        self._reply(200, {"path": self.path, "auth": self.headers.get("Authorization", "")})

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        self._reply(201, {"got": json.loads(self.rfile.read(n) or b"null")})

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def server():
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True); t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_http_request_get_auth_and_redaction(server, monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "s3cr3t-value")
    res = network.http_request({"url": f"{server}/x", "auth_env": "MY_TOKEN"})
    assert res["ok"] and res["status"] == 200 and res["json"]["path"] == "/x"
    assert "s3cr3t-value" not in json.dumps(res) and res["json"]["auth"] == "Bearer [redacted]"
    assert "error" in network.http_request({"url": f"{server}/x", "auth_env": "MISSING_TOKEN"})
    assert "error" in network.http_request({"url": "file:///etc/passwd"})


def test_http_request_write_needs_confirm(server):
    denied = network.http_request({"method": "POST", "url": f"{server}/items", "json": {"a": 1}})
    assert "confirm" in denied["error"]
    ok = network.http_request({"method": "POST", "url": f"{server}/items", "json": {"a": 1}, "confirm": True})
    assert ok["status"] == 201 and ok["json"]["got"] == {"a": 1}
