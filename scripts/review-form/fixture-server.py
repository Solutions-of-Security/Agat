#!/usr/bin/env python3
"""Serve only an in-memory synthetic form, never workspace files or real reviews."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.lib.decision_review_form import render_form
from scripts.test.test_decision_review_form import ASSET_DIR, bundle_fixture

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers(); self.wfile.write(payload)
        elif self.path == "/favicon.ico":
            self.send_response(204); self.end_headers()
        else:
            self.send_response(404); self.end_headers()

    def log_message(self, *_args):
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--cases", type=int, choices=(3, 49), default=3)
    args = parser.parse_args()
    bundle = bundle_fixture(args.cases)
    html, _manifest = render_form(bundle["blank"], bundle["localization"], bundle["manifest"]["initialReviewFileSha256"],
                                  bundle["manifest"]["localizationFileSha256"], ASSET_DIR)
    payload = html.encode()
    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        print(json.dumps({"url": "http://127.0.0.1:" + str(server.server_port), "syntheticCases": args.cases, "pid": os.getpid()}), flush=True)
        server.serve_forever()
