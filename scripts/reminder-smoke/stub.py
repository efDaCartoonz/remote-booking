from http.server import BaseHTTPRequestHandler, HTTPServer

calls = []


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
        elif self.path == "/stats":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(str(len(calls)).encode())
        else:
            self.send_error(404)

    def do_POST(self):
        calls.append("telegram" if self.path.startswith("/bot") else "bitrix")
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *_):
        pass


HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
