import os
import sys
import asyncio
import traceback
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
os.chdir(str(BASE_DIR))
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from backend.app import app

    print("Testing FastAPI routes...")

    async def request_path(path):
        response_status = None
        response_headers = []
        response_body = bytearray()
        request_sent = False

        async def receive():
            nonlocal request_sent
            if request_sent:
                return {"type": "http.disconnect"}
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            nonlocal response_status, response_headers
            if message["type"] == "http.response.start":
                response_status = message["status"]
                response_headers = message.get("headers", [])
            elif message["type"] == "http.response.body":
                response_body.extend(message.get("body", b""))

        await app({
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": [],
            "server": ("localhost", 80),
            "client": ("127.0.0.1", 12345),
        }, receive, send)
        return response_status, response_headers, bytes(response_body)

    for path in ("/", "/api/health"):
        status, headers, body = asyncio.run(request_path(path))
        header_map = {key.decode().lower(): value.decode() for key, value in headers}
        print("PATH:", path)
        print("HTTP Status:", status)
        print("Content-Type:", header_map.get("content-type", ""))
        print("Response:", body[:2000].decode("utf-8", errors="replace"))
except Exception:
    print("TEST FAILED")
    traceback.print_exc()
    raise
