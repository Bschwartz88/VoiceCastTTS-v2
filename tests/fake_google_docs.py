"""A small stand-in for Google Docs' download address, so import tests never go online.
Set .routes[path] = (status, headers, body) before a request; unknown paths answer 404.
.delay waits before answering; .drip sends the body in 10 pieces with that many seconds between them."""

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DOC_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
LINK = f"https://docs.google.com/document/d/{DOC_ID}/edit?usp=sharing"
EXPORT_PATH = f"/document/d/{DOC_ID}/export"


class FakeGoogle:
    def __init__(self):
        self.routes, self.requests, self.delay, self.drip = {}, [], 0, 0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                fake.requests.append({"path": self.path, "headers": dict(self.headers)})
                if fake.delay:
                    time.sleep(fake.delay)
                status, headers, body = fake.routes.get(self.path, (404, {}, b"not found"))
                try:
                    self.send_response(status)
                    for key, value in headers.items():
                        self.send_header(key, value)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    if fake.drip:
                        step = max(1, len(body) // 10)
                        for i in range(0, len(body), step):
                            self.wfile.write(body[i:i + step])
                            self.wfile.flush()
                            time.sleep(fake.drip)
                    else:
                        self.wfile.write(body)
                except ConnectionError:  # the import gave up waiting (slow-answer test)
                    pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def point(self, import_text):
        """Points import_text at this fake (plain http on 127.0.0.1); returns a function that undoes it."""
        names = ("EXPORT_URL", "ALLOWED_SCHEMES", "ALLOWED_HOSTS", "ALLOWED_HOST_SUFFIXES", "ALLOWED_PORTS",
                 "FETCH_TIMEOUT")
        saved = {n: getattr(import_text, n) for n in names}
        import_text.EXPORT_URL = self.base + "/document/d/{id}/export"
        import_text.ALLOWED_SCHEMES = ("http",)
        import_text.ALLOWED_HOSTS = ("127.0.0.1",)
        import_text.ALLOWED_HOST_SUFFIXES = ()  # never ("",): every host ends with ""
        import_text.ALLOWED_PORTS = (self.server.server_address[1],)
        return lambda: [setattr(import_text, n, v) for n, v in saved.items()]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
