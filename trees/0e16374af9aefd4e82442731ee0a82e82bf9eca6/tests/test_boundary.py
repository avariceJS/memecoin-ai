import hashlib
import http.client
import json
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "adapter"))
import export_snapshot
import server
from safe_io import Refused, json_bytes, read_bytes


class BoundaryTests(unittest.TestCase):
    def test_source_allowlist_denied_before_source_open(self):
        pins = json_bytes(read_bytes(export_snapshot.APP, "contracts/source-pins.json"))
        pins["sources"]["d43"]["path"] = "work/calendar-execution-01/windows/004/seal.json"
        calls = []

        def reader(root, relative, expected_sha=None):
            calls.append(relative)
            if relative != "contracts/source-pins.json":
                self.fail("Unapproved artifact opened")
            return json.dumps(pins).encode()

        with patch.object(export_snapshot, "read_bytes", side_effect=reader):
            with self.assertRaises(Refused):
                export_snapshot.build()
        self.assertEqual(calls, ["contracts/source-pins.json"])

    def test_exact_reproduction_without_network(self):
        with patch.object(socket, "create_connection", side_effect=AssertionError("network")):
            data = export_snapshot.build()
        self.assertEqual(data, read_bytes(export_snapshot.APP, "published/observatory-v1.json"))
        self.assertEqual(hashlib.sha256(data).hexdigest(), server.load_bundle()["snapshotId"])

    def test_no_core_import_or_raw_reference_in_dto(self):
        body = json.dumps(server.load_bundle())
        for marker in (
            "work/",
            "/Users/",
            "stop_command",
            "unload_command",
            "api_key",
            "v2/",
            "report_command",
            "weights",
            "worker.log",
            "nonoverlap_ids",
        ):
            self.assertNotIn(marker, body)
        self.assertFalse(any(k.startswith("memecoin_") for k in sys.modules))

    def test_protected_windows_have_no_observed_fields(self):
        data = server.load_bundle()["data"]
        protected = [r for r in data["calendar"]["data"]["windows"] if r["access"] == "PROTECTED"]
        self.assertEqual(len(protected), 2)
        for row in protected:
            self.assertEqual(set(row), {"id", "index", "plannedAt", "access"})
        research = data["research"]["data"]
        self.assertEqual(research["admission"], "LOCKED")
        self.assertEqual(research["globallyUnseenHoldout"], "NOT_CERTIFIED")
        self.assertEqual(research["recordedResearchFits"], 0)

    def test_missing_is_not_zero_or_fake_live(self):
        data = server.load_bundle()["data"]
        pending = data["calendar"]["data"]["windows"][1]
        self.assertIsNone(pending["snapshots"])
        self.assertIsNone(pending["actualStartNs"])
        self.assertEqual(pending["recordedStatus"], "pending")
        self.assertIsNone(data["models"]["data"]["activeModel"])
        self.assertEqual(data["system"]["availability"], "UNAVAILABLE")
        self.assertTrue(
            all(v["temporalScope"] == "RECORDED_SNAPSHOT" for v in data["sources"].values())
        )

    def test_timestamp_precision(self):
        data = server.load_bundle()["data"]
        ns = data["research"]["data"]["sampling"]["anchorNs"]
        self.assertEqual(ns, "1791331200000000000")
        self.assertIsInstance(ns, str)
        self.assertIsInstance(data["calendar"]["data"]["windows"][0]["finishedNs"], str)

    def test_refuse_traversal_symlink_fifo_and_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "ok").write_bytes(b"{}")
            for name in ("../ok", "/etc/passwd", "a/../ok"):
                with self.assertRaises(Refused):
                    read_bytes(root, name)
            (root / "link").symlink_to(root / "ok")
            with self.assertRaises(Refused):
                read_bytes(root, "link")
            (root / "dir").symlink_to(root, target_is_directory=True)
            with self.assertRaises(Refused):
                read_bytes(root, "dir/ok")
            with self.assertRaises(Refused):
                read_bytes(root, "ok", "0" * 64)
            (root / "large").write_bytes(b"x" * (512 * 1024 + 1))
            with self.assertRaises(Refused):
                read_bytes(root, "large")
            import os

            os.mkfifo(root / "fifo")
            with self.assertRaises(Refused):
                read_bytes(root, "fifo")

    def test_malformed_json(self):
        for body in (b'{"a":1,"a":2}', b'{"a":NaN}', b"{"):
            with self.assertRaises(ValueError):
                json_bytes(body)

    def test_http_boundary(self):
        with server.LocalServer(("127.0.0.1", 0), server.Handler) as httpd:
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()

            def request(path="/api/v1/snapshot", method="GET", extra=None, custom=True):
                c = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=3)
                headers = {"X-Control-Center": "1"} if custom else {}
                headers.update(extra or {})
                c.request(method, path, headers=headers)
                response = c.getresponse()
                result = (response.status, response.read(), dict(response.getheaders()))
                c.close()
                return result

            try:
                self.assertEqual(request()[0], 200)
                self.assertEqual(request("/api/v1/capabilities")[0], 200)
                self.assertEqual(request(custom=False)[0], 403)
                self.assertEqual(request(extra={"Origin": "https://evil.invalid"})[0], 403)
                self.assertEqual(request(extra={"Host": "evil.invalid"})[0], 403)
                self.assertEqual(request(extra={"Sec-Fetch-Site": "cross-site"})[0], 403)
                self.assertEqual(request(extra={"Origin": "http://127.0.0.1:5173"})[0], 200)
                for method in ("POST", "PUT", "DELETE", "PATCH"):
                    self.assertEqual(request(method=method)[0], 405)
                for path in (
                    "/../../.env.local",
                    "/api/v1/snapshot?path=/etc/passwd",
                    "/raw",
                    "/run",
                ):
                    with patch.object(server, "load_bundle", side_effect=AssertionError("read")):
                        self.assertEqual(request(path)[0], 404)
                with patch.object(
                    server, "load_bundle", side_effect=Refused("private secret path")
                ):
                    code, body, _ = request()
                    self.assertEqual(code, 503)
                    self.assertNotIn(b"private", body)
            finally:
                httpd.shutdown()
                thread.join(3)


if __name__ == "__main__":
    unittest.main()
