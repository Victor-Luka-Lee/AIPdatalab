"""Python-first stage commands; any failure aborts the sequence."""
import argparse
import json
import socket
import threading
from contextlib import contextmanager
from pathlib import Path
import uvicorn
from app.service import create_app
from pipeline.io import ROOT, read, initialize
from scenarios.runner import execute, wait_ready
from ground_truth.oracle import generate
from pipeline.split import populations
from pipeline.features import make_variant
from models.compare import train
from evaluation.metrics import evaluate
from evaluation.report import report

@contextmanager
def local_server(run):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(run), log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        if thread.is_alive():
            raise RuntimeError("Server shutdown timeout")

def run_all(run, config, policy, mode="smoke", conditions=("exact",)):
    initialize(run, config, policy)
    with local_server(run) as url:
        execute(run, url, mode)
    generate(run)
    populations(run)
    for name in conditions:
        make_variant(run, name)
        train(run, name)
        evaluate(run, name)
    return report(run)

def main(argv=None):
    parser = argparse.ArgumentParser(description="LOCAL SYNTHETIC RESEARCH ONLY; never use real data")
    parser.add_argument("command", choices=["init", "serve", "scenarios", "truth", "features", "train", "evaluate", "report", "run"])
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/default.json")
    parser.add_argument("--policy", type=Path, default=ROOT / "configs/policy.json")
    parser.add_argument("--mode", choices=["smoke", "pilot", "experiment"], default="smoke")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--incidents", type=int)
    parser.add_argument("--bootstrap", type=int)
    parser.add_argument("--conditions", nargs="+", default=["exact"])
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--compose-target", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--container-listen", action="store_true", help="Container only: listen on all interfaces, publish loopback only")
    args = parser.parse_args(argv)
    run = args.run_dir.resolve()
    config = read(args.config) if args.command in ("init", "run") else read(run / "config.json")
    if args.command in ("init", "run"):
        if args.seed is not None:
            config["seed"] = args.seed
        if args.incidents is not None:
            config["mode_incidents"][args.mode] = args.incidents
        if args.bootstrap is not None:
            config["bootstrap_iterations"] = args.bootstrap
        if config["customers"] < 12 or config["bootstrap_iterations"] < 0 or config["mode_incidents"][args.mode] < 1:
            raise ValueError("Invalid experiment dimensions")
        if not 0 < config["target_alert_rate"] < 1:
            raise ValueError("target_alert_rate must be between 0 and 1")
    for name in args.conditions:
        if name not in config["conditions"]:
            raise ValueError("Unknown predefined condition: " + name)
    if args.command == "init":
        initialize(run, config, read(args.policy))
    elif args.command == "serve":
        print("WARNING: deliberately imperfect synthetic service; trusted local research only", flush=True)
        uvicorn.run(create_app(run), host="0.0.0.0" if args.container_listen else "127.0.0.1", port=args.port, log_level="warning")
    elif args.command == "scenarios":
        execute(run, args.url, args.mode, args.compose_target)
    elif args.command == "truth":
        if (run / "private/labels.jsonl").exists():
            raise FileExistsError("Ground truth already exists")
        generate(run)
        populations(run)
    elif args.command == "features":
        for name in args.conditions:
            make_variant(run, name)
    elif args.command == "train":
        for name in args.conditions:
            train(run, name)
    elif args.command == "evaluate":
        for name in args.conditions:
            evaluate(run, name)
    elif args.command == "report":
        print(report(run))
    else:
        print(run_all(run, config, read(args.policy), args.mode, args.conditions))
    print(f"Completed {args.command}: {run}", flush=True)

if __name__ == "__main__":
    main()
