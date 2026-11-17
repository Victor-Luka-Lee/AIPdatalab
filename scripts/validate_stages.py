"""Check the public stage CLI using a real server subprocess."""
import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env.setdefault("MPLCONFIGDIR", str(root / ".runtime/matplotlib"))
    base = [sys.executable, "-m", "pipeline.cli"]
    run = str(args.run_dir.resolve())
    def stage(command, *extra):
        subprocess.run(base + [command, "--run-dir", run, *extra], cwd=root, env=env, check=True)
    stage("init", "--bootstrap", "20")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with (Path(run) / "server.log").open("w", encoding="utf-8") as log:
        server = subprocess.Popen(base + ["serve", "--run-dir", run, "--port", str(port)],
                                  cwd=root, env=env, stdout=log, stderr=log,
                                  creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            stage("scenarios", "--url", f"http://127.0.0.1:{port}")
        finally:
            server.terminate()
            server.wait(timeout=10)
    for command in ("truth", "features", "train", "evaluate", "report"):
        stage(command)

if __name__ == "__main__":
    main()
