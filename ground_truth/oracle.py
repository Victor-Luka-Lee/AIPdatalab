"""Independent disclosure oracle: never imports application authorization or detector."""
import hashlib
from pipeline.io import read, rows, write, jsonl, masked

PII = ("name", "email", "phone", "birth_date", "identifier")

def disclosed(body, data):
    """Map scalar values independently of detector keys; resolve shared values by object context."""
    reverse = {}
    for oid, record in data["customers"].items():
        for field in PII:
            reverse.setdefault(record[field], set()).add((oid, field, "full"))
            if field == "identifier":
                reverse.setdefault(masked(record[field]), set()).add((oid, field, "masked"))
    actual = set()
    def visit(node, context=None):
        if isinstance(node, dict):
            context = node.get("id", context)
            for value in node.values():
                visit(value, context)
        elif isinstance(node, list):
            for value in node:
                visit(value, context)
        elif isinstance(node, str):
            matches = reverse.get(node, set())
            contextual = {m for m in matches if m[0] == context}
            actual.update(contextual if contextual else matches)
    visit(body)
    return actual

def judge(body, data, policy, evidence):
    if body is None:
        return {"label": "U", "reasons": ["RESPONSE_UNAVAILABLE"], "actual": [], "verified": False}
    actual = disclosed(body, data)
    if not policy or not evidence.get("relationship_known", True):
        return {"label": "U", "reasons": ["POLICY_UNKNOWN"], "actual": sorted(actual), "verified": False}
    actor = evidence["actor_id"]
    role = data["actors"].get(actor, {}).get("role")
    if role not in policy.get("roles", {}):
        return {"label": "U", "reasons": ["POLICY_UNKNOWN"], "actual": sorted(actual), "verified": False}
    cfg = policy["roles"][role]
    approval = data["tasks"].get(evidence.get("task_ref"), {})
    allowed = set()
    reasons = set()
    for oid, field, representation in actual:
        c = data["customers"][oid]
        if role == "user":
            scope = c["owner"] == actor
        elif role == "agent":
            scope = c["agent"] == actor and not evidence.get("assignment_revoked", False)
        else:
            scope = approval.get("active") is True and approval.get("actor") == actor and oid in approval.get("objects", [])
        if not scope:
            reasons.add("TASK_SCOPE" if role == "batch" else "OBJECT_SCOPE")
        if evidence.get("bulk", False) and not cfg["bulk"]:
            reasons.add("TASK_SCOPE")
        if field not in cfg["fields"] and field not in cfg["masked_fields"]:
            reasons.add("FIELD_SCOPE")
        elif field in cfg["masked_fields"] and representation == "full":
            reasons.add("MASKING_SCOPE")
        field_ok = field in cfg["fields"] or (field in cfg["masked_fields"] and representation == "masked")
        if scope and field_ok and (not evidence.get("bulk") or cfg["bulk"]):
            allowed.add((oid, field, representation))
    return {"label": int(bool(actual - allowed)), "reasons": sorted(reasons),
            "actual": sorted(actual), "allowed": sorted(allowed), "verified": True}

def generate(run):
    data = read(run / "private/data.json")
    policy = read(run / "private/policy.json")
    result = []
    for e in rows(run / "private/evidence.jsonl"):
        body = None if e["response_path"] is None else read(run / e["response_path"])["body"]
        snapshot = None if e.get("policy_missing") else policy
        decision = judge(body, data, snapshot, e)
        result.append({**e, **decision, "policy_version": policy["version"],
                       "policy_hash": hashlib.sha256((run / "private/policy.json").read_bytes()).hexdigest(),
                       "policy_reference": "private/policy.json", "verification_status": "verified" if decision["verified"] else "insufficient_evidence"})
    jsonl(run / "private/labels.jsonl", result)
    return result
