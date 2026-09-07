from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json, os, threading
lock = threading.Lock()
mode = {"telegram": "success", "bitrix": "success"}
stats = {k: {"calls": 0, "codes": []} for k in mode}


class Handler(BaseHTTPRequestHandler):
    def _json(self, value, status=200):
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path == "/health":
            self._json({"status": "ok"})
        elif self.path == "/stats":
            with lock: self._json({k: {"calls": v["calls"], "mode": mode[k], "codes": list(v["codes"])} for k, v in stats.items()})
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/control/reset":
            with lock:
                for value in stats.values(): value.update(calls=0, codes=[])
            self._json({"ok": True}); return
        if self.path == "/control/mode":
            length = int(self.headers.get("Content-Length", "0")); raw = self.rfile.read(length)
            try: requested = json.loads(raw)
            except (ValueError, UnicodeDecodeError): self.send_error(400); return
            if not isinstance(requested, dict) or any(k not in mode or v not in {"success", "temporary", "permanent"} for k, v in requested.items()): self.send_error(400); return
            with lock: mode.update(requested)
            self._json({"ok": True}); return
        channel = "telegram" if self.path.startswith("/bot") else "bitrix" if self.path == "/bitrix" else None
        if channel is None: self.send_error(404); return
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        with lock:
            n = stats[channel]["calls"]; stats[channel]["calls"] += 1
            code = 500 if mode[channel] == "temporary" and n == 0 else 400 if mode[channel] == "permanent" else 200
            stats[channel]["codes"].append(code)
        self._json({}, code)

    def log_message(self, *_):
        pass


ThreadingHTTPServer(("0.0.0.0", int(os.getenv("STUB_PORT", "8080"))), Handler).serve_forever()
