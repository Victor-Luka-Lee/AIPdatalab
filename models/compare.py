"""Identical sequential logistic-regression pipelines and explicit rule scores."""
import time
import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.linear_model import LogisticRegression
from pipeline.features import VIEWS, CATEGORICAL
from pipeline.io import read, rows, write, jsonl

def make_model(columns, seed):
    numeric = [c for c in columns if c not in CATEGORICAL]
    cats = [c for c in columns if c in CATEGORICAL]
    numeric_pipe = Pipeline([("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
                             ("scale", StandardScaler())])
    cat_pipe = Pipeline([("impute", SimpleImputer(strategy="constant", fill_value="missing", keep_empty_features=True)),
                         ("encode", OneHotEncoder(handle_unknown="ignore"))])
    return Pipeline([("preprocess", ColumnTransformer([("numeric", numeric_pipe, numeric), ("categorical", cat_pipe, cats)])),
                     ("classifier", LogisticRegression(C=1., max_iter=1000, class_weight="balanced", random_state=seed))])

def rule_scores(frame, level, config):
    weights = dict(config["rules"]["base"])
    if level >= 1:
        weights.update(config["rules"]["web"])
    if level >= 2:
        weights.update(config["rules"]["audit"])
    return sum(frame[col].fillna(0).to_numpy(float) * weight for col, weight in weights.items())

def choose_threshold(scores, target):
    """Largest attainable alert count <= validation budget, all ties included or excluded."""
    scores = np.asarray(scores, dtype=float)
    if not len(scores):
        return None
    candidates = sorted(set(scores))
    candidates.append(float(np.nextafter(max(scores), np.inf)))
    acceptable = [t for t in candidates if float(np.mean(scores >= t)) <= target]
    return float(min(acceptable))

def train(run, name):
    dest = run / "versions" / name
    if (dest / "training.json").exists():
        raise FileExistsError("Training exists; choose a new condition or run")
    cfg = read(run / "config.json")
    labels = {e["request_id"]: e for e in rows(run / "private/population_main.jsonl")}
    manifest = rows(dest / "row_manifest.jsonl")
    meta = [labels[e["request_id"]] for e in manifest]
    train_idx = [i for i, e in enumerate(meta) if e["split"] == "train" and e["label"] != "U"]
    val_idx = [i for i, e in enumerate(meta) if e["split"] == "validation" and e["label"] != "U"]
    y = np.array([e["label"] if e["label"] != "U" else -1 for e in meta], dtype=int)
    output, training = [], {}
    (dest / "models").mkdir(exist_ok=True)
    for level in range(3):
        view = f"D{level}"
        frame = pd.read_csv(dest / "inputs" / f"{view}.csv")
        for column in CATEGORICAL & set(frame.columns):
            frame[column] = frame[column].astype(object)
        if list(frame.columns) != VIEWS[view] or len(frame) != len(manifest):
            raise ValueError("Feature whitelist or row alignment violation")
        for family in ("R", "M"):
            model_name = f"{family}{level}"
            status = "ok"
            started = time.perf_counter()
            if family == "R":
                score = rule_scores(frame, level, cfg)
                fit_seconds = 0.
                inference_seconds = time.perf_counter() - started
            elif len(set(y[train_idx])) != 2:
                status = "training requires two known classes"
                score = None
                fit_seconds = 0.
                inference_seconds = 0.
            else:
                model = make_model(VIEWS[view], cfg["seed"])
                model.fit(frame.iloc[train_idx], y[train_idx])
                fit_seconds = time.perf_counter() - started
                started = time.perf_counter()
                score = model.predict_proba(frame)[:, 1]
                inference_seconds = time.perf_counter() - started
                joblib.dump(model, dest / "models" / f"{model_name}.joblib")
            threshold = choose_threshold(score[val_idx], cfg["target_alert_rate"]) if score is not None and val_idx else None
            if threshold is None and status == "ok":
                status = "validation has no known responses"
            training[model_name] = {
                "status": status, "view": view, "threshold": threshold,
                "validation_alert_rate": float(np.mean(score[val_idx] >= threshold)) if threshold is not None else None,
                "fit_seconds": fit_seconds, "inference_seconds": inference_seconds,
                "fit_request_ids": [meta[i]["request_id"] for i in train_idx] if family == "M" and score is not None else [],
                "validation_request_ids": [meta[i]["request_id"] for i in val_idx],
                "hyperparameters": {"C": 1.0, "max_iter": 1000, "class_weight": "balanced", "candidates": 1} if family == "M" else cfg["rules"]
            }
            for i, e in enumerate(meta):
                output.append({"model": model_name, "request_id": e["request_id"], "incident_id": e["incident_id"],
                               "event_timestamp": e["event_timestamp"], "split": e["split"], "label": e["label"],
                               "score": float(score[i]) if score is not None else None,
                               "prediction": int(score[i] >= threshold) if score is not None and threshold is not None else None})
    jsonl(dest / "scores.jsonl", output)
    write(dest / "training.json", training)

