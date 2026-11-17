"""Read-only environment checks; no network requests."""
import importlib.metadata
import platform
import shutil
import sys
print("Python:", sys.version)
print("Platform:", platform.platform())
print("Docker:", shutil.which("docker") or "not installed")
for package in ("fastapi", "uvicorn", "httpx", "numpy", "pandas", "scipy", "scikit-learn", "matplotlib", "pytest"):
    try:
        print(package, importlib.metadata.version(package))
    except importlib.metadata.PackageNotFoundError:
        print(package, "not installed")
