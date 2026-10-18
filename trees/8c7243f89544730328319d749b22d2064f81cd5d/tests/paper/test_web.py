"""The live UI is a read-only projection, not a filesystem or execution API."""

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

from memecoin_paper.web import handler


def test_local_readonly_projection(tmp_path):
    (tmp_path / "dashboard.json").write_text(json.dumps(dict(schema="fixture")))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(tmp_path, 0))
    port = server.server_address[1]
    server.RequestHandlerClass = handler(tmp_path, port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.request("GET", "/api/snapshot")
        res = conn.getresponse()
        assert res.status == 200 and json.loads(res.read()) == dict(schema="fixture")
        for path in ("/../config.json", "/api/activate", "/config.json"):
            conn.request("GET", path)
            res = conn.getresponse()
            assert res.status == 404
            res.read()
        conn.request("GET", "/", headers={"Host": "evil.example"})
        res = conn.getresponse()
        assert res.status == 403
        res.read()
        conn.request("POST", "/api/snapshot")
        res = conn.getresponse()
        assert res.status == 501
        res.read()
        conn.request("GET", "/", headers={"Origin": "https://evil.example"})
        res = conn.getresponse()
        assert res.status == 403
        res.read()
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
