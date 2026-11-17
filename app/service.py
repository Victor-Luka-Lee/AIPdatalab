"""Deliberately imperfect service. Never expose to an untrusted network."""
import json
import sqlite3
import time
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pipeline.io import read, append, uid, masked

def create_app(run):
    run = Path(run)
    data, policy = read(run / "private/data.json"), read(run / "private/policy.json")
    sessions = {}
    clock = [0]
    app = FastAPI(title="LOCAL SYNTHETIC RESEARCH ONLY")

    def control():
        return read(run / "private/control.json")

    @app.middleware("http")
    async def logging(request, call_next):
        clock[0] += 1
        rid = uid(data["seed"], "request", clock[0])
        start = 1700000000.0 + clock[0] * 0.1
        request.state.rid = rid
        request.state.at = start
        sid = request.cookies.get("session")
        request.state.actor = sessions.get(sid)
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Event-Time"] = str(start)
        route = request.scope.get("route")
        actor = request.state.actor
        append(run / "observations/web.jsonl", {
            "request_id": rid, "event_timestamp": start, "ingested_at": start + .002,
            "actor_id": actor, "session_id": sid, "method": request.method,
            "role": data["actors"].get(actor, {}).get("role"),
            "normalized_route": route.path if route else request.url.path,
            "request_object_id": request.path_params.get("customer_id"),
            "status_code": response.status_code, "response_bytes": int(response.headers.get("content-length", 0))
        })
        return response

    @app.get("/health")
    def health():
        return {"status": "ok", "dataset_seed": data["seed"]}

    @app.post("/auth/login")
    async def login(request: Request):
        actor = (await request.json()).get("actor_id")
        if actor not in data["actors"]:
            return JSONResponse({"detail": "unknown identity"}, status_code=401)
        sid = uid(data["seed"], "session", request.state.rid)
        sessions[sid] = actor
        request.state.actor = actor
        response = JSONResponse({"role": data["actors"][actor]["role"]})
        response.set_cookie("session", sid, httponly=True, samesite="strict")
        return response

    def serve(request, ids, task=None, bulk=False, summary=False):
        actor = request.state.actor
        if actor is None:
            return JSONResponse({"detail": "login required"}, status_code=401)
        cfg = control()
        role = data["actors"][actor]["role"]
        allowed_role = policy["roles"][role]
        db = sqlite3.connect(run / "private/data.sqlite")
        found = []
        for key in ids:
            row = db.execute("SELECT payload FROM customers WHERE id=?", (key,)).fetchone()
            if row:
                found.append(json.loads(row[0]))
        db.close()
        approved = data["tasks"].get(task, {})
        objects_ok = all(
            (role == "user" and c["owner"] == actor) or
            (role == "agent" and c["agent"] == actor and not cfg.get("assignment_revoked")) or
            (role == "batch" and approved.get("active") and approved.get("actor") == actor and c["id"] in approved.get("objects", []))
            for c in found
        )
        permit = len(found) == len(ids) and objects_ok and (not bulk or allowed_role["bulk"])
        # Authorization and serializer defects are controlled by a local file only.
        append(run / "observations/audit.jsonl", {
            "request_id": request.state.rid, "event_timestamp": request.state.at,
            "ingested_at": request.state.at + .001, "actor_id": actor, "operation": "bulk" if bulk else "read",
            "object_ids": [c["id"] for c in found], "accessed_record_count": len(found),
            "task_ref": task, "assignment_ref": actor if role == "agent" else None
        })
        if not permit and not cfg.get("object_bypass"):
            return JSONResponse({"detail": "not permitted"}, status_code=403)
        result = []
        for c in found:
            item = {"id": c["id"]}
            for field in allowed_role["fields"]:
                if not summary or field in ("name", "email"):
                    item[field] = c[field]
            for field in allowed_role["masked_fields"]:
                if not summary:
                    item[field] = masked(c[field])
            if cfg.get("extra_field"):
                item["birth_date"] = c["birth_date"]
            if cfg.get("full_identifier"):
                item["identifier"] = c["identifier"]
            if cfg.get("alias_only"):
                item = {"id": c["id"], "contact_value": c["email"]}
            result.append(item)
        return JSONResponse({"customers": result})

    @app.get("/me")
    def me(request: Request):
        actor = request.state.actor
        ids = [c["id"] for c in data["customers"].values() if c["owner"] == actor]
        return serve(request, ids[:1]) if ids else JSONResponse({"detail": "no profile"}, status_code=404)

    @app.get("/customers/{customer_id}")
    def customer(customer_id: str, request: Request):
        return serve(request, [customer_id])

    @app.get("/customers/{customer_id}/summary")
    def summary(customer_id: str, request: Request):
        return serve(request, [customer_id], summary=True)

    @app.post("/customers/bulk-query")
    async def bulk(request: Request):
        body = await request.json()
        return serve(request, body.get("customer_ids", []), body.get("task_ref"), bulk=True)

    return app

