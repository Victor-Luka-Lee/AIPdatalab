"""Point-in-time feature views. This module reads observations only."""
import copy
import random
import time
import numpy as np
import pandas as pd
from pipeline.io import rows, read, write, jsonl, uid

D0 = ["name_count", "email_count", "phone_count", "birth_date_count", "identifier_count",
      "response_bytes", "normalized_route", "status_code", "hour"]
D1 = D0 + ["web_missing", "request_interval", "history_calls", "object_diversity", "api_transitions", "role"]
D2 = D1 + ["audit_missing", "link_state", "operation", "object_count", "accessed_record_count",
           "repeat_access", "task_present", "assignment_present", "actor_linked"]
VIEWS = {"D0": D0, "D1": D1, "D2": D2}
CATEGORICAL = {"normalized_route", "role", "link_state", "operation"}

def match_audit(web, audits, mode, window):
    """Candidate selection receives no truth mapping. Ambiguity stays missing."""
    if not web:
        return None, "unmatched"
    if mode == "exact":
        candidates = [a for a in audits if a.get("request_id") == web["request_id"]]
    else:
        operation = "bulk" if web["normalized_route"].endswith("bulk-query") else "read"
        candidates = [a for a in audits if a.get("actor_id") == web.get("actor_id")
                      and a["operation"] == operation
                      and abs(a["event_timestamp"] - web["event_timestamp"]) <= window]
    return (candidates[0], "matched") if len(candidates) == 1 else (None, "ambiguous" if candidates else "unmatched")

def build_features(events, webs, audits, config, mode="exact"):
    started = time.perf_counter()
    output, links = [], []
    for e in events:
        t = e["event_timestamp"]
        decision = t + config["decision_delay_seconds"]
        available_web = [w for w in webs if w["event_timestamp"] <= t and w["ingested_at"] <= decision]
        available_audit = [a for a in audits if a["event_timestamp"] <= t and a["ingested_at"] <= decision]
        candidates = [w for w in available_web if w["request_id"] == e["request_id"]]
        w = candidates[0] if len(candidates) == 1 else None
        a, state = match_audit(w, available_audit, mode, config["link_window_seconds"])
        x = {k: e[k] for k in D0 if k != "hour"}
        x["hour"] = int(t // 3600 % 24)
        x.update({k: np.nan for k in D1 if k not in D0})
        x["web_missing"] = int(w is None)
        if w:
            history = [h for h in available_web if h["event_timestamp"] < w["event_timestamp"]
                       and t - h["event_timestamp"] <= config["history_window_seconds"]
                       and h.get("session_id") == w.get("session_id")]
            history.sort(key=lambda h: h["event_timestamp"])
            x.update(request_interval=w["event_timestamp"] - history[-1]["event_timestamp"] if history else np.nan,
                     history_calls=len(history),
                     object_diversity=len({h["request_object_id"] for h in history if h.get("request_object_id")}),
                     api_transitions=sum(p["normalized_route"] != q["normalized_route"] for p, q in zip(history, history[1:])),
                     role=w.get("role") or "unknown")
        x.update({k: np.nan for k in D2 if k not in D1})
        x.update(audit_missing=int(a is None), link_state=state)
        if a:
            prior = [h for h in available_audit if h["event_timestamp"] < a["event_timestamp"]
                     and t - h["event_timestamp"] <= config["history_window_seconds"]
                     and h.get("actor_id") == a.get("actor_id")]
            x.update(operation=a["operation"], object_count=len(a["object_ids"]),
                     accessed_record_count=a["accessed_record_count"],
                     repeat_access=sum(len(set(h["object_ids"]) & set(a["object_ids"])) for h in prior),
                     task_present=int(bool(a.get("task_ref"))), assignment_present=int(bool(a.get("assignment_ref"))),
                     actor_linked=int(w["actor_id"] == a["actor_id"]))
        output.append(x)
        links.append({"request_id": e["request_id"], "link_state": state,
                      "audit_record_key": a.get("record_key") if a else None})
    return pd.DataFrame(output, columns=D2), links, time.perf_counter() - started

def make_variant(run, name):
    config = read(run / "config.json")
    condition = config["conditions"][name]
    dest = run / "versions" / name
    if dest.exists():
        raise FileExistsError(dest)
    dest.mkdir(parents=True)
    rng = random.Random(config["seed"])
    original = rows(run / "observations/audit.jsonl")
    changed, truth = [], {}
    for i, item in enumerate(original):
        a = copy.deepcopy(item)
        key = uid(config["seed"], "audit", i)
        truth[key] = a["request_id"]
        if rng.random() < condition["drop"]:
            continue
        a["record_key"] = key
        a["ingested_at"] += condition["delay"]
        if condition["remove_key"]:
            a.pop("request_id", None)
        changed.append(a)
    jsonl(dest / "audit.jsonl", changed)
    write(run / "private/link_truth" / f"{name}.json", truth)
    events = rows(run / "observations/detector.jsonl")
    frame, links, elapsed = build_features(events, rows(run / "observations/web.jsonl"), changed, config, condition["link"])
    inputs = dest / "inputs"
    inputs.mkdir()
    for view, columns in VIEWS.items():
        frame[columns].to_csv(inputs / f"{view}.csv", index=False)
    jsonl(dest / "row_manifest.jsonl", [{"request_id": e["request_id"], "event_timestamp": e["event_timestamp"]} for e in events])
    jsonl(dest / "links.jsonl", links)
    matched = [l for l in links if l["link_state"] == "matched"]
    correct = sum(truth.get(l["audit_record_key"]) == l["request_id"] for l in matched)
    # Denominator: main-population responses having an original audit record, even if dropped.
    original_ids = {a["request_id"] for a in original}
    eligible = sum(e["request_id"] in original_ids for e in events)
    write(dest / "feature_metadata.json", {
        "condition": condition, "feature_seconds": elapsed,
        "views": {k: {"columns": v, "count": len(v)} for k, v in VIEWS.items()},
        "rows": len(frame), "web_missing_rate": float(frame["web_missing"].mean()) if len(frame) else None,
        "audit_missing_rate": float(frame["audit_missing"].mean()) if len(frame) else None,
        "link_success_rate": len(matched) / len(events) if events else None,
        "link_precision": correct / len(matched) if matched else None,
        "link_recall": correct / eligible if eligible else None,
        "link_states": {k: sum(l["link_state"] == k for l in links) for k in ("matched", "ambiguous", "unmatched")}
    })
    return dest
