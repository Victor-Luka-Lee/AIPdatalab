"""Seeded scenarios. No scenario or defect flags are sent to HTTP endpoints."""
import copy
import random
import time
from urllib.parse import urlsplit
from pathlib import Path
import httpx
from detector.response import JsonFieldDetector
from pipeline.io import read, rows, write, append, uid

KINDS = ("own", "assigned", "other", "fields", "mask", "bulk", "repeat",
         "collect", "denied", "boundary", "unavailable", "unknown", "alias", "bulk_other", "new_pattern")

def validate_target(url, compose=False):
    p = urlsplit(url)
    allowed = {"localhost", "127.0.0.1", "::1"}
    if compose:
        allowed.add("lab-api")
    if (p.scheme != "http" or p.hostname not in allowed or p.username or p.password
            or p.query or p.fragment or p.path not in ("", "/")):
        raise ValueError("Only explicit local lab HTTP origins are allowed")
    return url.rstrip("/")

def plan(data, count, seed):
    rng = random.Random(seed)
    kinds = []
    while len(kinds) < count:
        block = list(KINDS)
        rng.shuffle(block)
        kinds.extend(block)
    result = []
    for i, kind in enumerate(kinds[:count]):
        agent = rng.choice(["a0", "a1", "a2"])
        assigned = [k for k, c in data["customers"].items() if c["agent"] == agent]
        others = [k for k, c in data["customers"].items() if c["agent"] != agent]
        own = rng.choice(list(data["customers"]))
        actor = agent
        ids = [rng.choice(assigned)]
        controls = {}
        task = None
        bulk = False
        repeat = 1
        if kind == "own":
            ids, actor = [own], data["customers"][own]["owner"]
        if kind in ("other", "denied", "alias"):
            actor = data["customers"][own]["owner"]
            ids = [rng.choice([k for k in data["customers"] if k != own])]
            controls = {"object_bypass": kind != "denied", "alias_only": kind == "alias"}
        if kind == "fields":
            controls = {"extra_field": True}
        if kind == "mask":
            controls = {"full_identifier": True}
        if kind in ("bulk", "bulk_other"):
            actor = rng.choice(["b0", "b1"])
            task = "t" + actor[1:]
            good = data["tasks"][task]["objects"]
            ids = rng.sample(good, min(4, len(good)))
            if kind == "bulk_other":
                ids = rng.sample([k for k in data["customers"] if k not in good], min(4, len(good)))
                controls = {"object_bypass": True}
            bulk = True
        if kind in ("repeat", "collect"):
            ids = [rng.choice(assigned if kind == "repeat" else others)]
            controls = {"object_bypass": kind == "collect"}
            repeat = 3
        if kind in ("boundary", "new_pattern"):
            repeat = 2
        result.append({"incident_id": uid(seed, "incident", i), "scenario_id": kind, "actor_id": actor,
                       "ids": ids, "controls": controls, "task_ref": task, "bulk": bulk, "repeat": repeat})
    return result

def wait_ready(client, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = client.get("/health")
            if r.status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(.1)
    raise RuntimeError("API not ready before deadline")

def execute(run, base_url, mode="smoke", compose=False):
    base_url = validate_target(base_url, compose)
    if (run / "private/evidence.jsonl").exists():
        raise FileExistsError("Scenario output already exists; use a new experiment directory")
    config, data = read(run / "config.json"), read(run / "private/data.json")
    count = config["mode_incidents"][mode]
    planned = plan(data, count, config["seed"])
    write(run / "private/plan.json", planned)
    detector = JsonFieldDetector()
    received = 0
    with httpx.Client(base_url=base_url, timeout=10, follow_redirects=False, trust_env=False) as client:
        wait_ready(client)
        if client.get("/health").json()["dataset_seed"] != data["seed"]:
            raise RuntimeError("Connected to a different experiment dataset")
        for incident in planned:
            login = client.post("/auth/login", json={"actor_id": incident["actor_id"]})
            login.raise_for_status()
            for step in range(incident["repeat"]):
                cfg = dict(incident["controls"])
                if incident["scenario_id"] == "boundary" and step == 1:
                    cfg["assignment_revoked"] = True
                write(run / "private/control.json", cfg)
                if incident["bulk"]:
                    route = "/customers/bulk-query"
                    response = client.post(route, json={"customer_ids": incident["ids"], "task_ref": incident["task_ref"]})
                else:
                    route = "/customers/{customer_id}"
                    suffix = "/summary" if incident["scenario_id"] == "new_pattern" and step == 0 else ""
                    route += suffix
                    response = client.get("/customers/" + incident["ids"][0] + suffix)
                if response.status_code >= 500:
                    response.raise_for_status()
                received += 1
                rid = response.headers["X-Request-ID"]
                event_time = float(response.headers["X-Event-Time"]) + .01
                response_path = f"private/responses/{rid}.json"
                write(run / response_path, {"body": response.json(), "body_hex": response.content.hex(),
                      "status_code": response.status_code, "headers": dict(response.headers), "received_wall_time": time.time()})
                metadata = {"request_id": rid, "event_timestamp": event_time, "ingested_at": event_time,
                            "normalized_route": route, "status_code": response.status_code,
                            "response_bytes": len(response.content)}
                event = detector.inspect(response.content, metadata)
                if event:
                    append(run / "observations/detector.jsonl", event)
                append(run / "private/evidence.jsonl", {
                    "run_id": uid(config["seed"], "run"), "incident_id": incident["incident_id"],
                    "request_id": rid, "scenario_id": incident["scenario_id"], "step": step,
                    "event_timestamp": event_time, "actor_id": incident["actor_id"],
                    "task_ref": incident["task_ref"], "bulk": incident["bulk"],
                    "relationship_known": True,
                    "relationship_snapshot": {k: {"owner": data["customers"][k]["owner"], "agent": data["customers"][k]["agent"],
                        "requester_role": data["actors"][incident["actor_id"]]["role"],
                        "assignment_active": not cfg.get("assignment_revoked", False)} for k in incident["ids"]},
                    "data_reference": "private/data.json", "assignment_revoked": cfg.get("assignment_revoked", False),
                    "policy_missing": incident["scenario_id"] == "unknown",
                    "response_path": None if incident["scenario_id"] == "unavailable" else response_path,
                    "captured_response_path": response_path, "server_control": cfg,
                    "account_compromised": None, "verification_status": "pending"
                })
    write(run / "private/control.json", {})
    write(run / "scenario_summary.json", {"mode": mode, "planned_incidents": count,
          "planned_responses": sum(p["repeat"] for p in planned), "actual_responses": received,
          "synthetic_event_clock": True})

