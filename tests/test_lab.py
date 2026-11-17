import copy
import json
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
import pytest
from pipeline.io import ROOT, fixture, read, write, rows, masked, initialize
from ground_truth.oracle import judge, disclosed
from pipeline.features import build_features, VIEWS, match_audit
from pipeline.split import split_incidents, populations
from models.compare import make_model, choose_threshold
from evaluation.metrics import metrics
from scenarios.runner import validate_target, plan
from pipeline.cli import run_all

@pytest.fixture
def data():
    return fixture()

@pytest.fixture
def policy():
    return read(ROOT / "configs/policy.json")

def evidence(actor="u0", **kwargs):
    return {"actor_id": actor, "relationship_known": True, **kwargs}

def body(data, oid="c0", fields=("email",)):
    return {"customers": [{"id": oid, **{f: data["customers"][oid][f] for f in fields}}]}

def test_01_own_response_zero(data, policy):
    assert judge(body(data), data, policy, evidence())["label"] == 0

def test_02_other_object_positive(data, policy):
    out = judge(body(data, "c1"), data, policy, evidence())
    assert out["label"] == 1 and "OBJECT_SCOPE" in out["reasons"]

def test_03_field_scope(data, policy):
    out = judge(body(data, fields=("birth_date",)), data, policy, evidence("a0"))
    assert out["label"] == 1 and out["reasons"] == ["FIELD_SCOPE"]

def test_04_mask_scope(data, policy):
    out = judge(body(data, fields=("identifier",)), data, policy, evidence("a0"))
    assert out["label"] == 1 and out["reasons"] == ["MASKING_SCOPE"]
    masked_body = {"customers": [{"id": "c0", "identifier": masked(data["customers"]["c0"]["identifier"])}]}
    assert judge(masked_body, data, policy, evidence("a0"))["label"] == 0

def test_05_approved_bulk(data, policy):
    response = {"customers": [body(data, k, ("email", "identifier"))["customers"][0] for k in ("c0", "c2")]}
    assert judge(response, data, policy, evidence("b0", bulk=True, task_ref="t0"))["label"] == 0
    assert judge(response, data, policy, evidence("b0", bulk=True, task_ref="t1"))["label"] == 1

def test_06_denied_is_not_disclosure(data, policy):
    assert judge({"detail": "not permitted"}, data, policy, evidence("u1"))["label"] == 0

@pytest.mark.parametrize("missing", ["response", "policy", "relationship"])
def test_07_missing_evidence_unknown(data, policy, missing):
    e = evidence(relationship_known=missing != "relationship")
    out = judge(None if missing == "response" else body(data), data, None if missing == "policy" else policy, e)
    assert out["label"] == "U"

def test_08_failed_attempt_not_positive(data, policy):
    e = evidence(server_control={"object_bypass": True}, scenario_id="other")
    assert judge({"detail": "login required"}, data, policy, e)["label"] == 0
    assert judge(None, data, policy, e)["label"] == "U"

@pytest.fixture
def observations():
    e = {"request_id": "r", "event_timestamp": 10., "ingested_at": 10., "normalized_route": "/customers/{customer_id}",
         "status_code": 200, "response_bytes": 100,
         **{f + "_count": 1 for f in ("name", "email", "phone", "birth_date", "identifier")}}
    w = {"request_id": "r", "event_timestamp": 9.99, "ingested_at": 10., "actor_id": "u", "session_id": "s",
         "normalized_route": e["normalized_route"], "request_object_id": "c", "role": "user"}
    a = {"request_id": "r", "event_timestamp": 9.99, "ingested_at": 10., "actor_id": "u", "operation": "read",
         "object_ids": ["c"], "accessed_record_count": 1, "task_ref": None, "record_key": "k"}
    return e, w, a, read(ROOT / "configs/default.json")

def test_09_exact_join_uses_key(observations):
    e, w, a, c = observations
    bad = {**a, "request_id": "different", "accessed_record_count": 999}
    f, links, _ = build_features([e], [w], [bad, a], c)
    assert f.iloc[0].accessed_record_count == 1
    assert links[0]["audit_record_key"] == "k"

def test_10_unmatched_rows_retained(observations):
    e, w, a, c = observations
    f, links, _ = build_features([e], [w], [], c)
    assert len(f) == 1 and f.iloc[0].audit_missing == 1
    assert pd.isna(f.iloc[0].accessed_record_count) and links[0]["link_state"] == "unmatched"

def test_11_future_events_not_used(observations):
    e, w, a, c = observations
    future = {**w, "request_id": "f", "event_timestamp": 10.1, "ingested_at": 10.1}
    future_a = {**a, "event_timestamp": 10.1, "ingested_at": 10.1}
    f, _, _ = build_features([e], [w, future], [future_a], c)
    assert f.iloc[0].history_calls == 0 and f.iloc[0].audit_missing == 1

def test_12_unarrived_audit_not_used(observations):
    e, w, a, c = observations
    f, _, _ = build_features([e], [w], [{**a, "ingested_at": 11}], c)
    assert f.iloc[0].audit_missing == 1
    past = {**w, "request_id": "past", "event_timestamp": 9, "ingested_at": 11}
    f, _, _ = build_features([e], [w, past], [a], c)
    assert f.iloc[0].history_calls == 0

def test_13_label_defect_and_scenario_excluded(observations):
    e, w, a, c = observations
    e.update(label=1, scenario_id="x", object_bypass=True)
    f, _, _ = build_features([e], [w], [a], c)
    assert not {"label", "scenario_id", "object_bypass", "policy_version"} & set(f.columns)

def test_14_no_raw_identifiers(observations):
    e, w, a, c = observations
    f, _, _ = build_features([e], [w], [a], c)
    assert list(f.columns) == VIEWS["D2"]
    assert not {"request_id", "incident_id", "run_id", "actor_id", "object_id", "session_id"} & set(f.columns)
    assert set(VIEWS["D0"]) < set(VIEWS["D1"]) < set(VIEWS["D2"])

def test_16_incident_splits_no_overlap():
    labels = [{"incident_id": str(i), "event_timestamp": float(i * 10 + j)} for i in range(10) for j in range(3)]
    mapping = split_incidents(labels, [.6, .2, .2])
    assert [mapping[str(i)] for i in range(10)] == ["train"] * 6 + ["validation"] * 2 + ["test"] * 2
    overlapping = [{"incident_id": "a", "event_timestamp": 1}, {"incident_id": "a", "event_timestamp": 5},
                   {"incident_id": "b", "event_timestamp": 4}, {"incident_id": "b", "event_timestamp": 6}]
    mapping = split_incidents(overlapping, [.6, .2, .2])
    assert mapping["a"] == mapping["b"]

def test_17_preprocessing_fit_train_only():
    model = make_model(["email_count", "normalized_route"], 42)
    train = pd.DataFrame({"email_count": [1., 2., 3., 4.], "normalized_route": ["a", "b", "a", "b"]})
    test = pd.DataFrame({"email_count": [100000.], "normalized_route": ["never_in_train"]})
    model.fit(train, [0, 1, 0, 1])
    pre = model.named_steps["preprocess"]
    before = pre.named_transformers_["numeric"].named_steps["scale"].mean_.copy()
    model.predict_proba(test)
    assert np.array_equal(pre.named_transformers_["numeric"].named_steps["scale"].mean_, before)
    assert "never_in_train" not in pre.named_transformers_["categorical"].named_steps["encode"].categories_[0]

def test_19_one_class_unavailable():
    out = metrics([0, 0], [.2, .4], [0, 0])
    assert out["status"].startswith("unavailable") and "F1" not in out

def test_20_detector_miss_independent_oracle(data, policy):
    from detector.response import JsonFieldDetector
    response = {"id": "c1", "contact_value": data["customers"]["c1"]["email"]}
    assert JsonFieldDetector().inspect(json.dumps(response).encode(), {}) is None
    assert judge(response, data, policy, evidence())["label"] == 1

def test_22_same_seed_data_and_plan():
    first, second = fixture(87), fixture(87)
    assert first == second
    assert plan(first, 36, 87) == plan(second, 36, 87)
    assert fixture(88) != first
    assert plan(first, 36, 88) != plan(first, 36, 87)

@pytest.mark.parametrize("url", ["https://127.0.0.1", "http://example.com", "http://127.0.0.1.evil.test",
                                "http://127.0.0.2", "http://user@localhost", "http://localhost/path",
                                "http://lab-api:8000", "http://localhost?next=x"])
def test_23_external_target_blocked(url):
    with pytest.raises(ValueError):
        validate_target(url)

def test_local_target_allowed():
    assert validate_target("http://127.0.0.1:8000") == "http://127.0.0.1:8000"
    assert validate_target("http://lab-api:8000", compose=True) == "http://lab-api:8000"

def test_restricted_ambiguity_never_picks_truth(observations):
    e, w, a, c = observations
    found, state = match_audit(w, [a, {**a, "record_key": "other"}], "restricted", .05)
    assert found is None and state == "ambiguous"

def test_threshold_budget_and_ties():
    assert choose_threshold([1, 1, 1, 1], .25) > 1
    t = choose_threshold([1, 2, 2, 3], .5)
    assert np.mean(np.array([1, 2, 2, 3]) >= t) <= .5

@pytest.fixture(scope="module")
def smoke(tmp_path_factory):
    run = tmp_path_factory.mktemp("integration") / "run"
    cfg = read(ROOT / "configs/default.json")
    cfg["bootstrap_iterations"] = 10
    run_all(run, cfg, read(ROOT / "configs/policy.json"), conditions=("exact", "missing", "delayed", "restricted"))
    return run

def test_21_real_http_smoke_report(smoke):
    assert (smoke / "report.md").exists()
    summary = read(smoke / "scenario_summary.json")
    assert summary["actual_responses"] == summary["planned_responses"]
    labels = rows(smoke / "private/labels.jsonl")
    assert {e["label"] for e in labels} == {0, 1, "U"}
    assert {e["scenario_id"] for e in labels} == set(__import__("scenarios.runner", fromlist=["KINDS"]).KINDS)
    for e in labels:
        raw = read(smoke / e["captured_response_path"])
        assert bytes.fromhex(raw["body_hex"])
    result = read(smoke / "versions/exact/evaluation.json")
    assert set(result) == {"R0", "R1", "R2", "M0", "M1", "M2"}
    assert all(v["status"] == "ok" for v in result.values())

def test_15_all_comparison_populations_equal(smoke):
    for condition in ("exact", "missing", "delayed", "restricted"):
        dest = smoke / "versions" / condition
        scores = rows(dest / "scores.jsonl")
        sets = [{e["request_id"] for e in scores if e["model"] == model and e["split"] == "test"} for model in ("R0", "R1", "R2", "M0", "M1", "M2")]
        assert all(s == sets[0] for s in sets)
        assert all(len(pd.read_csv(dest / "inputs" / f"D{i}.csv")) == len(rows(dest / "row_manifest.jsonl")) for i in range(3))

def test_18_unknown_excluded_not_negative(smoke):
    labels = rows(smoke / "private/population_main.jsonl")
    unknown = {e["request_id"] for e in labels if e["label"] == "U"}
    assert unknown
    training = read(smoke / "versions/exact/training.json")
    for info in training.values():
        assert not unknown.intersection(info["fit_request_ids"] + info["validation_request_ids"])
    scores = rows(smoke / "versions/exact/scores.jsonl")
    assert all(e["label"] == "U" for e in scores if e["request_id"] in unknown)

def test_model_fit_manifest_and_missing_audit(smoke):
    labels = {e["request_id"]: e for e in rows(smoke / "private/population_main.jsonl")}
    training = read(smoke / "versions/exact/training.json")
    for k in ("M0", "M1", "M2"):
        assert all(labels[rid]["split"] == "train" for rid in training[k]["fit_request_ids"])
    f = pd.read_csv(smoke / "versions/delayed/inputs/D2.csv")
    assert f.audit_missing.eq(1).all()
    assert f.accessed_record_count.isna().all()
    original = rows(smoke / "observations/audit.jsonl")
    restricted = rows(smoke / "versions/restricted/audit.jsonl")
    assert all("request_id" in e for e in original)
    assert all("request_id" not in e for e in restricted)

def test_same_seed_http_replay_consistent(smoke, tmp_path):
    from pipeline.cli import local_server
    from scenarios.runner import execute
    from ground_truth.oracle import generate
    cfg = read(smoke / "config.json")
    other = tmp_path / "replay"
    initialize(other, cfg, read(smoke / "private/policy.json"))
    with local_server(other) as url:
        execute(other, url)
    generate(other)
    labels = rows(other / "private/labels.jsonl")
    populations(other)
    original = rows(smoke / "private/labels.jsonl")
    keys = ("incident_id", "request_id", "label", "actual", "reasons", "event_timestamp")
    assert [{k: e[k] for k in keys} for e in labels] == [{k: e[k] for k in keys} for e in original]
    assert read(other / "splits.json") == read(smoke / "splits.json")
    for e, prior in zip(labels, original):
        assert read(other / e["captured_response_path"])["body_hex"] == read(smoke / prior["captured_response_path"])["body_hex"]
    f, _, _ = build_features(rows(other / "observations/detector.jsonl"), rows(other / "observations/web.jsonl"),
                             rows(other / "observations/audit.jsonl"), cfg)
    expected = pd.read_csv(smoke / "versions/exact/inputs/D2.csv")
    pd.testing.assert_frame_equal(f, expected, check_dtype=False)

def test_unknown_evidence_not_called_detector_false_event(smoke):
    summary = read(smoke / "population_summary.json")
    assert summary["detector_false_event_count"] == 0
    assert summary["detector_event_unknown_evidence_count"] > 0

def test_masked_values_use_object_context():
    data = fixture(count=600)
    value = data["customers"]["c0"]["birth_date"]
    assert value == data["customers"]["c540"]["birth_date"]
    assert disclosed({"id": "c0", "birth_date": value}, data) == {("c0", "birth_date", "full")}

def test_all_missing_categorical_pipeline():
    model = make_model(["email_count", "role"], 42)
    frame = pd.DataFrame({"email_count": [1., 2., 3., 4.], "role": np.array([np.nan] * 4, dtype=object)})
    model.fit(frame, [0, 1, 0, 1])
    assert model.predict_proba(frame).shape == (4, 2)

def test_no_detector_population_report(tmp_path):
    cfg = read(ROOT / "configs/default.json")
    cfg["mode_incidents"]["smoke"] = 1
    cfg["bootstrap_iterations"] = 2
    # Select a seed by planned behavior, never by resulting test performance.
    cfg["seed"] = next(s for s in range(100) if plan(fixture(s), 1, s)[0]["scenario_id"] == "denied")
    run = tmp_path / "empty"
    run_all(run, cfg, read(ROOT / "configs/policy.json"))
    assert read(run / "population_summary.json")["main_responses"] == 0
    result = read(run / "versions/exact/evaluation.json")
    assert len(result) == 6
    assert all(v["status"].startswith("unavailable") for v in result.values())
    assert all("F1" not in v for v in result.values())
    assert (run / "report.md").exists()
