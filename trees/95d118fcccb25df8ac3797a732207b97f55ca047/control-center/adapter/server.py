"""Loopback read-only API over one reviewed projection. No core/artifact traversal."""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from safe_io import Refused, json_bytes, read_bytes

APP = Path(__file__).resolve().parents[1]


def load_bundle():
    manifest = json_bytes(read_bytes(APP, "published/manifest.json"))
    if manifest.get("schema") != 1 or manifest.get("file") != "observatory-v1.json":
        raise Refused("unsupported_manifest")
    data = read_bytes(APP, "published/observatory-v1.json", manifest["sha256"])
    payload = json_bytes(data)
    if (
        payload.get("apiVersion") != "1.0"
        or payload.get("sourcePolicy") != "REVIEWED_PROJECTION_ONLY"
    ):
        raise Refused("unsupported_contract")
    return dict(
        apiVersion="1.0",
        snapshotId=hashlib.sha256(data).hexdigest(),
        servedAt=datetime.now(UTC).isoformat(),
        data=payload,
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "ControlCenterReadOnly/1"
    sys_version = ""

    def log_message(self, *_args):
        pass  # Do not echo URLs/headers/payloads into logs.

    def boundary(self):
        host = f"127.0.0.1:{self.server.server_port}"
        allowed = (f"http://{host}", "http://127.0.0.1:5173")
        origin = self.headers.get("Origin")
        if (
            self.headers.get("Host") != host
            or origin not in (None, *allowed)
            or self.headers.get("Sec-Fetch-Site") == "cross-site"
        ):
            return False
        return True

    def send_json(self, code, payload):
        data = json.dumps(payload, allow_nan=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        if self.boundary() and self.headers.get("Origin"):
            self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET")
            self.send_header("Access-Control-Allow-Headers", "X-Control-Center")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.boundary() or self.headers.get("X-Control-Center") != "1":
            self.send_json(403, dict(error="LOCAL_READ_BOUNDARY_REQUIRED"))
            return
        if self.path not in ("/api/v1/snapshot", "/api/v1/capabilities"):
            self.send_json(404, dict(error="UNKNOWN_ROUTE"))
            return
        try:
            result = load_bundle()
            if self.path.endswith("/capabilities"):
                result["data"] = dict(capabilities=result["data"]["capabilities"])
            self.send_json(200, result)
        except (ValueError, OSError, KeyError, TypeError):
            self.send_json(503, dict(error="ARTIFACT_UNAVAILABLE_OR_INVALID"))

    def do_OPTIONS(self):
        if (
            self.boundary()
            and self.path in ("/api/v1/snapshot", "/api/v1/capabilities")
            and self.headers.get("Access-Control-Request-Method") == "GET"
            and self.headers.get("Access-Control-Request-Headers", "").lower() == "x-control-center"
        ):
            self.send_json(200, {})
        else:
            self.send_json(403, dict(error="CROSS_ORIGIN_REFUSED"))

    def refuse(self):
        self.send_json(405, dict(error="READ_ONLY_API"))

    do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = refuse


class LocalServer(HTTPServer):
    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(3)
        return sock, address


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dump", action="store_true")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    if args.dump:
        print(json.dumps(load_bundle(), indent=2))
        return
    if not 1024 <= args.port <= 65535:
        raise SystemExit("Use an unprivileged loopback port.")
    with LocalServer(("127.0.0.1", args.port), Handler) as server:
        print(
            f"Read-only recorded evidence: http://127.0.0.1:{args.port}/api/v1/snapshot", flush=True
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
