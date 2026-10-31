import importlib
import importlib.util
import os
import sys
import traceback

print("Python:", sys.version)
print("Directory:", os.getcwd())
print("Executable:", sys.executable)

packages = ["fastapi", "a2wsgi", "uvicorn", "websockets", "backend.app"]
for package in packages:
    try:
        spec = importlib.util.find_spec(package)
        print(package, "FOUND" if spec else "NOT FOUND")
    except Exception as exc:
        print(package, "ERROR:", repr(exc))

print("--- importing backend.app ---")
try:
    module = importlib.import_module("backend.app")
    print("backend.app IMPORT: OK")
    print("hosted:", getattr(getattr(module, "cfg", None), "HOSTED", "unknown"))
    print("routes:", len(getattr(getattr(module, "app", None), "routes", [])))
except Exception:
    print("backend.app IMPORT: FAILED")
    traceback.print_exc()
