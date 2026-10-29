"""
cPanel / Phusion Passenger entry point.

cPanel's "Setup Python App" serves the application through Passenger, which
speaks WSGI - not ASGI - and never upgrades a WebSocket. Two things happen here:

1. ``a2wsgi.ASGIMiddleware`` adapts the FastAPI (ASGI) application to WSGI.
2. Hosted mode is forced on, so the dashboard serves the public market feeds
   (no MetaTrader 5 terminal needed) and the browser polls the REST endpoints
   instead of the ``/ws`` streams.

Everything else is the same application the desktop build runs.
"""
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
os.chdir(str(BASE_DIR))

# Real prices from the public feeds, no MT5 terminal, HTTP instead of WS.
os.environ.setdefault("SOFTRADEX_MODE", "public")

from a2wsgi import ASGIMiddleware  # noqa: E402

from backend.app import app as asgi_app  # noqa: E402

# ASGIMiddleware (not WSGIMiddleware, which goes the other way) turns an ASGI
# app into the WSGI callable Passenger expects.
application = ASGIMiddleware(asgi_app)
