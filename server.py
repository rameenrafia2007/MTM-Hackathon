#!/usr/bin/env python3
"""Web UI + JSON API. Calls the SAME orchestrator.process() as main.py.

Built on Starlette (the toolkit FastAPI itself is built on): `python server.py`
then open http://127.0.0.1:8000
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from engine import orchestrator, store
from engine.llm import load_env
from engine.trace import AREAS

ROOT = Path(__file__).resolve().parent
UI = ROOT / "ui"
LOCK = threading.Lock()


def _slips() -> list:
    return store.load_json("slips.json", []) or []


def _resolve_slip(run: dict) -> None:
    slips = _slips()
    changed = False
    for s in slips:
        if s["status"] == "locked":
            actual = [a for a in AREAS if a in run["model_areas"]]
            s.update({"status": "resolved", "run_id": run["id"], "actual": actual,
                      "match": sorted(s["areas"]) == sorted(actual),
                      "resolved_at": store.now_iso()})
            changed = True
    if changed:
        store.save_json("slips.json", slips)


async def index(request):
    return FileResponse(UI / "index.html")


async def branding(request):
    p = UI / "branding.json"
    if p.exists():
        return Response(p.read_text(encoding="utf-8"), media_type="application/json")
    return JSONResponse({"event_name": "Mind to Machine Hackathon", "organizers": [], "sponsors": []})


async def api_state(request):
    st = store.load_state()
    return JSONResponse({"has_state": st is not None, "state": st, "slips": _slips(), "busy": LOCK.locked()})


async def api_run(request):
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        return JSONResponse({"ok": False, "error": "paste or choose some messages first"}, status_code=400)
    if not LOCK.acquire(blocking=False):
        return JSONResponse({"ok": False, "error": "another run is in progress"}, status_code=409)
    try:
        res = await run_in_threadpool(lambda: orchestrator.process(text=text, echo=False))
    finally:
        LOCK.release()
    run = res.get("run")
    if run:
        _resolve_slip(run)
    return JSONResponse({"ok": res.get("ok", False), "error": res.get("error"), "mode": res.get("mode"),
                         "noop": bool(res.get("noop")), "run": run})


async def api_reset(request):
    if LOCK.locked():
        return JSONResponse({"ok": False, "error": "a run is in progress"}, status_code=409)
    store.reset_state()
    store.save_json("slips.json", [])
    return JSONResponse({"ok": True})


async def api_slip(request):
    body = await request.json()
    areas = sorted({a for a in body.get("areas", []) if a in AREAS})
    slips = _slips()
    if any(s["status"] == "locked" for s in slips):
        return JSONResponse({"ok": False, "error": "a prediction is already locked and cannot be changed"},
                            status_code=409)
    slip = {"id": len(slips) + 1, "areas": areas, "locked_at": store.now_iso(), "status": "locked"}
    slips.append(slip)
    store.save_json("slips.json", slips)
    return JSONResponse({"ok": True, "slip": slip})


async def api_export(request):
    st = store.load_state()
    if not st:
        return JSONResponse({"ok": False, "error": "nothing to export yet"}, status_code=404)
    fmt = request.query_params.get("fmt", "txt")
    if fmt == "json":
        body = json.dumps({"plan": st["plan"], "validation": st["validation"], "ledger": st["ledger"],
                           "runs": [{k: v for k, v in r.items() if k != "trace"} | {"trace": r["trace"]}
                                    for r in st["runs"]]}, indent=1, ensure_ascii=False)
        return Response(body, media_type="application/json",
                        headers={"Content-Disposition": "attachment; filename=final_plan_and_trace.json"})
    return Response(store.render_final(st), media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=final_plan_and_trace.txt"})


routes = [
    Route("/", index),
    Route("/branding.json", branding),
    Route("/api/state", api_state),
    Route("/api/run", api_run, methods=["POST"]),
    Route("/api/reset", api_reset, methods=["POST"]),
    Route("/api/slip", api_slip, methods=["POST"]),
    Route("/api/export", api_export),
]
if (UI / "assets").exists():
    routes.append(Mount("/assets", StaticFiles(directory=str(UI / "assets")), name="assets"))

load_env()
app = Starlette(routes=routes)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))
