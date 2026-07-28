from __future__ import annotations
import json
import io
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from jinja2 import StrictUndefined
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from .. import scenarios, security, services as svc

DOMAIN_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(DOMAIN_DIR / "templates"), undefined=StrictUndefined)
templates.env.globals["FINDING_CATEGORY_LABELS"] = {
    "missing_required_info": "Puuttuva tieto",
    "contradiction": "Ristiriita",
    "weak_description": "Puutteellinen kuvaus",
    "attachment_scope": "Liitteen käyttötarkoitus",
    "criteria_gap": "Kriteeripuute",
}

def create_authority_router(db_getter: Callable[[], Any], settings_getter: Callable[[], Any]) -> APIRouter:
    router = APIRouter()

    def db() -> Any:
        return db_getter()

    def settings() -> Any:
        return settings_getter()

    def context(case_id: str, permit_type: Optional[str] = None) -> dict[str, Any]:
        svc.ensure_seed_state(db(), case_id)
        sync = svc.detect_common_data_changes(db(), case_id)
        project = svc.artifact_json(db(), case_id, "lv_project", "main", {})
        common = svc.artifact_json(db(), case_id, "lv_common_data", "main", {"data": svc.DEFAULT_COMMON_DATA.copy()})
        out = {"case_id": case_id, "case": db().get_case(case_id), "project": project, "common": common, "sync": sync}
        if permit_type:
            app = svc.ensure_application(db(), case_id, permit_type)
            out.update(_context_app(app, case_id, permit_type, db(), settings()))
        return out


    def _context_app(app: dict, case_id: str, permit_type: str, db_obj: Any, settings_obj: Any) -> dict:
        def aj(type_: str, default: Any) -> Any:
            return svc.artifact_json(db_obj, case_id, type_, permit_type, default)
        return {
            "application": app,
            "schema": svc.load_form_schema(app["permit_type"]),
            "criteria": svc.load_criteria(app["permit_type"]),
            "attachments": svc.list_attachments(db_obj, case_id, app["permit_type"]),
            "suggestions": aj("ai_suggestions", {"suggestions": []}).get("suggestions", []),
            "check_report": aj("check_report", {}),
            "precheck_gate": svc.precheck_gate_status(db_obj, case_id, app["permit_type"]),
            "package": aj("submission_package", {}),
            "receipt": aj("receipt", {}),
            "wizard": svc.wizard_step_status(db_obj, case_id, app["permit_type"]),
            "structure_extraction": aj("structure_extraction_result", {}),
            "intake_analysis": aj("intake_analysis", {}),
            "material_package": svc.current_material_package(db_obj, case_id, app["permit_type"]),
            "package_analysis": aj("package_analysis_result", {}),
            "field_extraction_queue": aj("field_extraction_queue", {"items": []}),
            "ai_jobs": svc.list_ai_jobs(db_obj, case_id, app["permit_type"]),
            "contextual_chat": aj("contextual_chat", {"messages": []}),
            "voice_state": svc.voice_feature_state(settings_obj),
            "voice_chat": aj("voice_chat", {"turns": []}),
            "environmental_story": svc.artifact_json(db_obj, case_id, "environmental_material_story", "main", {}),
        }

    @router.get("/cases/{case_id}/lupademo/authority/queue", response_class=HTMLResponse)
    def authority_queue(request: Request, case_id: str, permit_type: str = "", municipality: str = ""):
        svc.ensure_seed_state(db(), case_id)
        queue = svc.artifact_json(db(), case_id, "authority_queue", "main", {"items": []})
        items = queue.get("items", [])
        if permit_type:
            items = [item for item in items if item.get("permit_type") == permit_type or item.get("application_id") == permit_type]
        if municipality:
            items = [item for item in items if str(item.get("municipality") or "").lower() == municipality.lower()]
        return templates.TemplateResponse(request, "authority_queue.html", {**context(case_id), "queue": {**queue, "items": items}, "filters": {"permit_type": permit_type, "municipality": municipality}})

    @router.get("/authority/queue", response_class=HTMLResponse)
    def authority_queue_global(permit_type: str = "", municipality: str = ""):
        case_id = scenarios.active_case_id(db()) or svc.ensure_demo_case(db())
        suffix = ""
        if permit_type or municipality:
            suffix = f"?permit_type={permit_type}&municipality={municipality}"
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/authority/queue{suffix}", status_code=303)

    @router.get("/cases/{case_id}/lupademo/authority/applications/{application_id}", response_class=HTMLResponse)
    def authority_detail(request: Request, case_id: str, application_id: str):
        return templates.TemplateResponse(
            "authority_detail.html",
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
