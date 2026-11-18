"""A fake OpenAI-compatible model server on localhost, for tests and offline demos ONLY.

It exercises the real HTTP code path of engine/llm.py (discovery via GET /v1/models,
POST /v1/chat/completions, usage fields, timeouts, errors). Token usage it reports is
SYNTHETIC (characters / 4); this is not a real model.
"""
from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeLLMServer:
    def __init__(self, responder, models=("fake-small-a", "fake-small-b", "fake-embed-x"), delays=None, fails=None,
                 port=0):
        self.responder, self.models = responder, list(models)
        self.delays, self.fails = delays or {}, fails or {}
        self.requests: list = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass

            def do_GET(self):
                if self.path.rstrip("/").endswith("/models"):
                    self._send(200, {"object": "list", "data": [{"id": m} for m in outer.models]})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
                model = body.get("model", "")
                outer.requests.append({"model": model, "body": body})
                time.sleep(outer.delays.get(model, 0))
                if model in outer.fails:
                    return self._send(outer.fails[model], {"error": "stub failure"})
                msgs = body.get("messages", [])
                system = next((m["content"] for m in msgs if m["role"] == "system"), "")
                user = next((m["content"] for m in msgs if m["role"] == "user"), "")
                text = outer.responder(system, user, model)
                pt, ct = (len(system) + len(user)) // 4, len(text) // 4
                self._send(200, {"model": model, "choices": [{"message": {"role": "assistant", "content": text}}],
                                 "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.port = self.httpd.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}/v1"

    def start(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


if __name__ == "__main__":  # demo server: python -m tests.fake_llm_server 8765
    import sys
    from .canned_all import RESPONDER
    srv = FakeLLMServer(RESPONDER, port=int(sys.argv[1]) if len(sys.argv) > 1 else 8765)
    print(f"fake model server on {srv.base_url} (synthetic token counts; NOT a real model)")
    srv.httpd.serve_forever()
