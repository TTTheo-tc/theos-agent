"""Cron management API routes."""

from __future__ import annotations

import json

from starlette.requests import Request
from starlette.responses import JSONResponse


def _get_cron_service(request: Request):
    return (request.app.state.app_context or {}).get("cron_service")


def _require_gateway(request: Request):
    if not _get_cron_service(request):
        return JSONResponse({"error": "Requires running gateway"}, status_code=503)
    return None


async def cron_jobs_list(request: Request) -> JSONResponse:
    svc = _get_cron_service(request)
    if svc:
        jobs = svc.list_jobs(include_disabled=True)
        return JSONResponse([j.model_dump(by_alias=True) for j in jobs])
    store_path = (request.app.state.app_context or {}).get("cron_store_path")
    if store_path and store_path.exists():
        from src.cron.types import CronStore

        data = json.loads(store_path.read_text())
        store = CronStore.model_validate(data)
        return JSONResponse([j.model_dump(by_alias=True) for j in store.jobs])
    return JSONResponse([])


async def cron_job_create(request: Request) -> JSONResponse:
    err = _require_gateway(request)
    if err:
        return err
    svc = _get_cron_service(request)
    body = await request.json()
    from src.cron.types import CronSchedule

    try:
        job = svc.add_job(
            name=body["name"],
            schedule=CronSchedule.model_validate(body["schedule"]),
            message=body.get("message", ""),
            kind=body.get("kind", "agent_turn"),
            deliver=body.get("deliver", False),
            channel=body.get("channel"),
            to=body.get("to"),
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(job.model_dump(by_alias=True), status_code=201)


async def cron_job_update(request: Request) -> JSONResponse:
    err = _require_gateway(request)
    if err:
        return err
    svc = _get_cron_service(request)
    job_id = request.path_params["job_id"]
    body = await request.json()
    if "enabled" in body:
        job = svc.enable_job(job_id, body["enabled"])
        if not job:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(job.model_dump(by_alias=True))
    return JSONResponse({"error": "no updatable fields"}, status_code=400)


async def cron_job_delete(request: Request) -> JSONResponse:
    err = _require_gateway(request)
    if err:
        return err
    svc = _get_cron_service(request)
    job_id = request.path_params["job_id"]
    ok = svc.remove_job(job_id)
    if not ok:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"ok": True})


async def cron_job_run(request: Request) -> JSONResponse:
    err = _require_gateway(request)
    if err:
        return err
    svc = _get_cron_service(request)
    job_id = request.path_params["job_id"]
    # Check job exists before firing
    store = svc._load_store()
    if not any(j.id == job_id for j in store.jobs):
        return JSONResponse({"error": "not found"}, status_code=404)
    # Fire-and-forget — run_job invokes the full agent loop, don't block HTTP
    import asyncio

    asyncio.create_task(svc.run_job(job_id, force=True))
    return JSONResponse({"ok": True, "status": "accepted"}, status_code=202)
