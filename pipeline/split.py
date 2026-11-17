"""Incident-grouped chronological split and explicit evaluation populations."""
from pipeline.io import rows, read, write, jsonl

def split_incidents(labels, fractions):
    spans = {}
    for e in labels:
        key = e["incident_id"]
        t = e["event_timestamp"]
        if key not in spans:
            spans[key] = [t, t]
        spans[key][0] = min(spans[key][0], t)
        spans[key][1] = max(spans[key][1], t)
    # Merge overlapping incident spans into indivisible temporal blocks.
    blocks = []
    for key, (start, end) in sorted(spans.items(), key=lambda p: (p[1][0], p[0])):
        if blocks and start <= blocks[-1]["end"]:
            blocks[-1]["ids"].append(key)
            blocks[-1]["end"] = max(blocks[-1]["end"], end)
        else:
            blocks.append({"ids": [key], "end": end, "start": start})
    n = len(blocks)
    train_end = max(1, int(n * fractions[0]))
    val_end = max(train_end + 1, int(n * sum(fractions[:2])))
    mapping = {}
    for i, block in enumerate(blocks):
        part = "train" if i < train_end else "validation" if i < val_end else "test"
        for incident in block["ids"]:
            mapping[incident] = part
    return mapping

def populations(run):
    labels = rows(run / "private/labels.jsonl")
    detected = {e["request_id"] for e in rows(run / "observations/detector.jsonl")}
    splits = split_incidents(labels, read(run / "config.json")["split_fractions"])
    all_rows = [{**e, "split": splits[e["incident_id"]], "detected": e["request_id"] in detected} for e in labels]
    jsonl(run / "private/population_all.jsonl", all_rows)
    jsonl(run / "private/population_main.jsonl", [e for e in all_rows if e["detected"]])
    write(run / "splits.json", {"seed": read(run / "config.json")["seed"], "incident_split": splits,
                              "boundary_rule": "overlapping incident spans merged; whole blocks assigned by start time"})
    known_positive = [e for e in all_rows if e["label"] == 1]
    pii = [e for e in all_rows if e["actual"]]
    unknown = [e for e in all_rows if e["label"] == "U"]
    write(run / "population_summary.json", {
        "all_responses": len(all_rows), "main_responses": sum(e["detected"] for e in all_rows),
        "unknown_all": len(unknown), "unknown_rate_all": len(unknown) / len(all_rows) if all_rows else None,
        "unknown_main": sum(e["detected"] for e in unknown),
        "unknown_reasons": {r: sum(r in e["reasons"] for e in unknown) for r in {r for e in unknown for r in e["reasons"]}},
        "pii_response_coverage": sum(e["detected"] for e in pii) / len(pii) if pii else None,
        "positive_response_coverage": sum(e["detected"] for e in known_positive) / len(known_positive) if known_positive else None,
        "missed_positive_responses": sum(not e["detected"] for e in known_positive),
        "detector_event_unknown_evidence_count": sum(e["detected"] and not e["verified"] for e in all_rows),
        "pii_response_count": len(pii), "detector_missed_pii_responses": sum(not e["detected"] for e in pii),
        "detector_false_event_count": sum(e["detected"] and not e["actual"] and e["verified"] for e in all_rows)
    })
    return all_rows

