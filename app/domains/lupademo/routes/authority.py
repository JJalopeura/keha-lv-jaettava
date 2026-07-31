from __future__ import annotations
import json
import io
import time
import uuid
import zipfile
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import ValidationError

from .. import scenarios, security, services as svc
from .common import RouteContext

def create_authority_router(ctx: RouteContext) -> APIRouter:
    router = APIRouter()
    templates = ctx.templates
    db = ctx.db
    settings = ctx.settings

    def context(case_id: str, permit_type: Optional[str] = None) -> dict[str, Any]:
        return ctx.context(case_id, permit_type)

    @router.get("/cases/{case_id}/lupademo/authority/queue")
    def authority_queue(case_id: str, permit_type: str = "", municipality: str = ""):
        suffix = ""
        if permit_type or municipality:
            suffix = f"?permit_type={permit_type}&municipality={municipality}"
        return RedirectResponse(url=f"/authority/queue{suffix}", status_code=301)

    @router.get("/authority/queue", response_class=HTMLResponse)
    def authority_queue_global(request: Request, permit_type: str = "", municipality: str = ""):
        # Aggregate items from all cases' authority queues
        all_items: list[dict[str, Any]] = []
        for c in db().list_cases():
            q = svc.artifact_json(db(), c["id"], "authority_queue", "main", {"items": []})
            for item in q.get("items", []):
                item["case_id"] = c["id"]
                all_items.append(item)
        if permit_type:
            all_items = [item for item in all_items if item.get("permit_type") == permit_type or item.get("application_id") == permit_type]
        if municipality:
            all_items = [item for item in all_items if str(item.get("municipality") or "").lower() == municipality.lower()]
        return templates.TemplateResponse(request, "authority/authority_queue.html", {"queue": {"items": all_items}, "filters": {"permit_type": permit_type, "municipality": municipality}})

    @router.get("/cases/{case_id}/lupademo/authority/applications/{application_id}", response_class=HTMLResponse)
    def authority_detail(request: Request, case_id: str, application_id: str):
        return templates.TemplateResponse(
            "authority/authority_detail.html",
            {
                "request": request,
                **context(case_id, application_id),
                "authority_summary": svc.artifact_json(db(), case_id, "authority_summary", svc.application_id_for(application_id), {}),
                "additional_info_draft": svc.artifact_json(db(), case_id, "additional_info_draft", svc.application_id_for(application_id), {}),
                "authority_memo": svc.artifact_json(db(), case_id, "authority_memo", svc.application_id_for(application_id), {}),
                "caseworker_observations": svc.caseworker_observation_panel(db(), case_id, application_id),
            },
        )

    @router.get("/authority/applications/{application_id}", response_class=HTMLResponse)
    def authority_detail_global(application_id: str):
        case_id = scenarios.active_case_id(db()) or svc.ensure_demo_case(db())
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/authority/applications/{application_id}", status_code=303)

    @router.post("/cases/{case_id}/lupademo/authority/applications/{application_id}/observations/{observation_id}/status")
    def update_observation_status(case_id: str, application_id: str, observation_id: str, status: str = Form(...), note: str = Form("")):
        try:
            svc.update_caseworker_observation_status(db(), case_id, application_id, observation_id, status, note)  # type: ignore[arg-type]
        except KeyError:
            raise HTTPException(status_code=404, detail="Observation not found")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid observation status")
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/authority/applications/{application_id}#observation-{observation_id}", status_code=303)

    @router.post("/cases/{case_id}/lupademo/authority/applications/{application_id}/ai/summary")
    def generate_authority_summary(case_id: str, application_id: str):
        svc.authority_summary(db(), case_id, application_id, settings=settings())
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/authority/applications/{application_id}", status_code=303)

    @router.post("/cases/{case_id}/lupademo/authority/applications/{application_id}/ai/request-info-draft")
    def generate_additional_info(case_id: str, application_id: str):
        svc.additional_info_draft(db(), case_id, application_id, settings=settings())
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/authority/applications/{application_id}", status_code=303)

    @router.post("/cases/{case_id}/lupademo/authority/applications/{application_id}/ai/decision-support")
    def generate_authority_memo(case_id: str, application_id: str):
        svc.authority_memo(db(), case_id, application_id, settings=settings())
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/authority/applications/{application_id}", status_code=303)

    @router.post("/cases/{case_id}/lupademo/authority/applications/{application_id}/status")
    def update_authority_status(case_id: str, application_id: str, status: str = Form(...)):
        queue = svc.artifact_json(db(), case_id, "authority_queue", "main", {"items": []})
        for item in queue.get("items", []):
            if item.get("application_id") == svc.application_id_for(application_id):
                item["status"] = status
        svc.save_artifact(db(), case_id, "authority_queue", "main", queue)
        svc.event(db(), case_id, "authority.status.updated", "Authority status updated", {"application_id": application_id, "status": status})
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/authority/applications/{application_id}", status_code=303)

    return router
