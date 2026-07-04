from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import deployment as dep
from .channels import channel_meta


def build_payload(deployment, snapshot, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    channels = {}
    for name, reading in dep.resolve_channels(deployment, snapshot, now).items():
        meta = channel_meta(name)
        channels[name] = {
            "value": reading.value,
            "ts": reading.ts,
            "source_id": reading.source_id,
            "unit": meta.unit if meta else "",
            "label": meta.label if meta else name,
            "group": meta.group if meta else "",
        }
    return {
        "schema_version": 1,
        "deployment": deployment.descriptor(),
        "capabilities": dep.capabilities(deployment, snapshot, now),
        "channels": channels,
        "timestamp": now,
    }


def make_server(host: str, port: int, deployment, snapshot) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def _json(self, code: int, obj) -> None:
            body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/health":
                self._json(200, {"ok": True})
            elif path in ("/telemetry", "/telemetry/power", "/sensor/power"):
                self._json(200, build_payload(deployment, snapshot))
            elif path == "/telemetry/history":
                self._json(200, {"schema_version": 1, "deployment_id": deployment.id, "points": []})
            else:
                self._json(404, {"error": "not_found"})

    return ThreadingHTTPServer((host, port), Handler)
