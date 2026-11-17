"""Portable files and deterministic synthetic fixtures."""
import hashlib
import json
import platform
import random
import sqlite3
from pathlib import Path
from importlib.metadata import version

ROOT = Path(__file__).resolve().parents[1]

def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")

def rows(path):
    p = Path(path)
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x] if p.exists() else []

def append(path, value):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")

def jsonl(path, values):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for value in values:
            f.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")

def uid(seed, *parts):
    return hashlib.sha256(json.dumps([seed, *parts]).encode()).hexdigest()[:24]

def masked(value):
    return "SYN-****-" + value[-4:]

def fixture(seed=42, count=24):
    rng = random.Random(seed)
    actors = {f"u{i}": {"role": "user"} for i in range(count)}
    actors.update({f"a{i}": {"role": "agent"} for i in range(3)})
    actors.update({f"b{i}": {"role": "batch"} for i in range(2)})
    customers = {}
    for i in range(count):
        key = f"c{i}"
        customers[key] = {
            "id": key, "owner": f"u{i}", "agent": f"a{i % 3}",
            "name": f"Synthetic Person {i:03d}", "email": f"person{i:03d}@example.test",
            "phone": f"TEST-PHONE-{i:06d}", "birth_date": f"{1980 + i % 20}-01-{1 + i % 27:02d}",
            "identifier": f"SYN-{rng.randrange(10000000, 99999999)}-{i:04d}"
        }
    tasks = {f"t{i}": {"actor": f"b{i}", "objects": list(customers)[i::2], "active": True} for i in range(2)}
    return {"seed": seed, "actors": actors, "customers": customers, "tasks": tasks}

def initialize(run, config, policy):
    run = Path(run)
    run.mkdir(parents=True, exist_ok=False)
    data = fixture(config["seed"], config["customers"])
    write(run / "private/data.json", data)
    write(run / "private/policy.json", policy)
    write(run / "config.json", config)
    write(run / "private/control.json", {})
    db = sqlite3.connect(run / "private/data.sqlite")
    db.execute("CREATE TABLE customers (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
    db.executemany("INSERT INTO customers VALUES (?, ?)", [(k, json.dumps(v)) for k, v in data["customers"].items()])
    db.commit()
    db.close()
    write(run / "environment.json", {"python": platform.python_version(), "platform": platform.platform(),
          "source_sha256": source_hashes(), "project_version": "0.1.0",
          "versions": {p: version(p) for p in ["scipy", "fastapi", "uvicorn", "httpx", "numpy", "pandas", "scikit-learn", "matplotlib", "joblib"]}})
    return data

def source_hashes():
    paths = [p for directory in ("app", "detector", "scenarios", "ground_truth", "pipeline", "models", "evaluation", "configs")
             for p in (ROOT / directory).rglob("*") if p.is_file() and p.suffix in (".py", ".json")]
    paths += [p for p in [ROOT / "pyproject.toml"] if p.exists()]
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
