"""Run the dashboard exactly the way cPanel/Passenger will run it.

    python serve_cpanel_local.py        # then open http://127.0.0.1:8799/

Passenger speaks WSGI and never upgrades a WebSocket, so this is the honest
local check before uploading to cPanel: the browser falls back to HTTP polling,
the gzip middleware and the news-thinning state feed all behave there the same
way they will on the shared host (uvicorn, by contrast, would serve WebSockets
and hide those problems).
"""
import os
import sys
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server, WSGIRequestHandler

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

os.environ.setdefault("SOFTRADEX_MODE", "public")

from a2wsgi import ASGIMiddleware  # noqa: E402

from backend.app import app as asgi_app  # noqa: E402


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


httpd = make_server(
    "127.0.0.1", 8799, ASGIMiddleware(asgi_app),
    server_class=ThreadingWSGIServer, handler_class=QuietHandler,
)
print("wsgi server ready on http://127.0.0.1:8799", flush=True)
httpd.serve_forever()
