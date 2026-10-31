import io
import os
import sys
import traceback

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
os.environ.setdefault("SOFTRADEX_MODE", "public")

try:
    from passenger_wsgi import application

    result = {}
    environ = {
        "REQUEST_METHOD": "GET",
        "SCRIPT_NAME": "",
        "PATH_INFO": "/api/health",
        "QUERY_STRING": "",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "http",
        "wsgi.input": io.BytesIO(b""),
        "wsgi.errors": io.StringIO(),
        "wsgi.multithread": True,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
        "CONTENT_LENGTH": "0",
        "CONTENT_TYPE": "",
    }

    def start_response(status, headers, exc_info=None):
        result["status"] = status
        result["headers"] = headers

    body = b"".join(application(environ, start_response))
    print("WSGI STATUS:", result.get("status"))
    print("WSGI BODY:", body[:2000].decode("utf-8", errors="replace"))
    print("WSGI ERRORS:", environ["wsgi.errors"].getvalue())
except Exception:
    print("WSGI TEST FAILED")
    traceback.print_exc()
    raise
