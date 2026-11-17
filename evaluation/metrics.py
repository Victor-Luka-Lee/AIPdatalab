"""Final test metrics; paired incident-cluster bootstrap shares every resample."""
import time
import os
import tempfile
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "privacy-lab-matplotlib"))
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.metrics import average_precision_score, precision_recall_curve, confusion_matrix, f1_score
from pipeline.io import rows, read, write

def metrics(y, score, prediction):
    y, score, p = np.asarray(y, int), np.asarray(score, float), np.asarray(prediction, int)
    if len(set(y)) < 2:
        return {"status": "unavailable: test requires two known classes", "n": len(y)}
    tn, fp, fn, tp = confusion_matrix(y, p, labels=[0, 1]).ravel()
    return {"status": "ok", "n": len(y), "precision": float(tp / (tp + fp)) if tp + fp else None,
            "recall": float(tp / (tp + fn)), "F1": float(f1_score(y, p, zero_division=0)),
            "false_positive_rate": float(fp / (fp + tn)), "average_precision": float(average_precision_score(y, score)),
            "alert_rate": float(np.mean(p)), "tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)}

def incident_metrics(frame):
    positive = [g.sort_values("event_timestamp") for _, g in frame.groupby("incident_id") if (g.label == 1).any()]
    delays = []
    for g in positive:
        hit = np.flatnonzero(((g.label == 1) & (g.prediction == 1)).to_numpy())
        delays.append(int(hit[0] + 1) if len(hit) else None)
    valid = [d for d in delays if d is not None]
    return {"positive_incidents": len(positive), "detected_incidents": len(valid),
            "incident_detection_rate": len(valid) / len(positive) if positive else None,
            "first_detection_response_count_mean": float(np.mean(valid)) if valid else None,
            "undetected_incidents": len(delays) - len(valid)}

def paired_bootstrap(frames, iterations, seed):
    pairs = [("M1", "M0"), ("M2", "M1"), ("R2", "R1"), ("M2", "R2")]
    available = {k: v.set_index("request_id") for k, v in frames.items() if not v.empty and v.score.notna().all() and v.prediction.notna().all()}
    if not available:
        return {"status": "unavailable", "pairs": []}
    # All models must evaluate the identical response set.
    reference = next(iter(available.values()))
    for f in available.values():
        if set(f.index) != set(reference.index):
            raise ValueError("Inconsistent comparison populations")
    incidents = list(reference.incident_id.unique())
    clusters = {k: reference.index[reference.incident_id == k].tolist() for k in incidents}
    rng = np.random.default_rng(seed)
    deltas = {a + "-" + b: [] for a, b in pairs}
    skipped = 0
    for _ in range(iterations):
        sampled = rng.choice(incidents, size=len(incidents), replace=True)
        ids = [rid for incident in sampled for rid in clusters[incident]]
        if len(set(reference.loc[ids, "label"])) < 2:
            skipped += 1
            continue
        for a, b in pairs:
            if a in available and b in available:
                fa, fb = available[a].loc[ids], available[b].loc[ids]
                deltas[a + "-" + b].append(float(f1_score(fa.label.astype(int), fa.prediction.astype(int), zero_division=0)
                                                 - f1_score(fb.label.astype(int), fb.prediction.astype(int), zero_division=0)))
    result = {}
    for a, b in pairs:
        key = a + "-" + b
        values = deltas[key]
        observed = None
        if a in available and b in available and len(set(reference.label)) == 2:
            observed = float(f1_score(available[a].label.astype(int), available[a].prediction.astype(int), zero_division=0)
                             - f1_score(available[b].label.astype(int), available[b].prediction.astype(int), zero_division=0))
        result[key] = {"metric": "F1", "observed_difference": observed,
                       "ci95": np.quantile(values, [.025, .975]).tolist() if values else None, "valid_resamples": len(values)}
    return {"iterations": iterations, "skipped_one_class": skipped, "method": "paired incident cluster percentile bootstrap", "pairs": result}

def evaluate(run, name):
    dest = run / "versions" / name
    if (dest / "evaluation.json").exists():
        raise FileExistsError("Evaluation already exists")
    cfg = read(run / "config.json")
    all_scores = pd.DataFrame(rows(dest / "scores.jsonl"), columns=[
        "model", "request_id", "incident_id", "event_timestamp", "split", "label", "score", "prediction"])
    model_names = sorted(read(dest / "training.json"))
    results, frames, incidents = {}, {}, []
    figures = dest / "figures"
    figures.mkdir(exist_ok=True)
    for model in model_names:
        started = time.perf_counter()
        all_test = all_scores[(all_scores.model == model) & (all_scores.split == "test")]
        frame = all_test[all_test.label != "U"].copy()
        frames[model] = frame
        if frame.empty or frame.score.isna().any() or frame.prediction.isna().any():
            result = {"status": "unavailable: model or test responses missing", "n": len(frame)}
        else:
            result = metrics(frame.label.astype(int), frame.score, frame.prediction.astype(int))
        result["excluded_U_test"] = int((all_test.label == "U").sum())
        result["excluded_U_test_rate"] = float((all_test.label == "U").mean()) if len(all_test) else None
        if result["status"] == "ok":
            result.update(incident_metrics(frame))
            p, r, t = precision_recall_curve(frame.label.astype(int), frame.score)
            pd.DataFrame({"precision": p, "recall": r, "threshold": np.r_[t, np.nan]}).to_csv(dest / f"{model}_pr.csv", index=False)
            fig, ax = plt.subplots(figsize=(4, 3))
            ax.step(r, p, where="post")
            ax.set(xlabel="Recall", ylabel="Precision", title=model)
            fig.tight_layout()
            fig.savefig(figures / f"{model}_pr.png", dpi=120)
            plt.close(fig)
            cm = np.array([[result["tn"], result["fp"]], [result["fn"], result["tp"]]])
            pd.DataFrame(cm, index=["actual_0", "actual_1"], columns=["pred_0", "pred_1"]).to_csv(dest / f"{model}_confusion.csv")
            fig, ax = plt.subplots(figsize=(3, 3))
            ax.imshow(cm, cmap="Blues")
            for (i, j), v in np.ndenumerate(cm):
                ax.text(j, i, str(v), ha="center", va="center")
            ax.set(xticks=[0, 1], yticks=[0, 1], xlabel="Prediction", ylabel="Actual", title=model)
            fig.tight_layout()
            fig.savefig(figures / f"{model}_confusion.png", dpi=120)
            plt.close(fig)
            for incident, g in frame.groupby("incident_id"):
                g = g.sort_values("event_timestamp")
                hits = np.flatnonzero(((g.label == 1) & (g.prediction == 1)).to_numpy())
                incidents.append({"model": model, "incident_id": incident, "positive": bool((g.label == 1).any()),
                                  "detected": bool(len(hits)), "first_detection_response_count": int(hits[0] + 1) if len(hits) else None})
        result["evaluation_seconds"] = time.perf_counter() - started
        results[model] = result
    pd.DataFrame.from_dict(results, orient="index").rename_axis("model").to_csv(dest / "metrics.csv")
    pd.DataFrame(incidents).to_csv(dest / "incident_metrics.csv", index=False)
    write(dest / "evaluation.json", results)
    write(dest / "bootstrap.json", paired_bootstrap(frames, cfg["bootstrap_iterations"], cfg["seed"]))
    return results
