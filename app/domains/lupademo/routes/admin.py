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

def create_admin_router(db_getter: Callable[[], Any], settings_getter: Callable[[], Any]) -> APIRouter:
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

    @router.get("/cases/{case_id}/lupademo/suggestion-service", response_class=HTMLResponse)
    def suggestion_service(request: Request, case_id: str):
        return templates.TemplateResponse(
            "suggestion_service.html",
            {
                "request": request,
                **context(case_id),
                "api_clients": security.public_client_options(),
                "request_log": svc.artifact_json(db(), case_id, "suggestion_service_request_log", "main", {"items": []}),
            },
        )

    @router.get("/cases/{case_id}/lupademo/suggestion-service/api-demo", response_class=HTMLResponse)
    def api_demo(request: Request, case_id: str):
        default_payload = {
            "request_id": "demo_package_request",
            "tenant_id": "avi_demo",
            "permit_type": "environmental_industrial",
            "documents": [
                {
                    "filename": "ymparistolupahakemus.txt",
                    "document_role": "application_form",
                    "delivery_scope": "official_submission",
                    "text": "Ympäristölupahakemus biokaasulaitokselle. Kapasiteetti 18000 tonnia vuodessa. Jätevedet käsitellään suljetussa järjestelmässä.",
                },
                {
                    "filename": "bat_ja_tarkkailu.txt",
                    "document_role": "monitoring_plan",
                    "delivery_scope": "official_submission",
                    "text": "BAT-arviointi, tarkkailu, raportointi ja hajupäästöjen hallinta on kuvattu.",
                },
            ],
        }
        result = svc.artifact_json(db(), case_id, "suggestion_service_demo", "api", {})
        return templates.TemplateResponse(
            "api_demo.html",
            {
                "request": request,
                **context(case_id),
                "api_clients": security.public_client_options(),
                "request_json": json.dumps(default_payload, ensure_ascii=False, indent=2),
                "default_api_key": "demo-avi-key",
                "result": result,
            },
        )

    @router.get("/integrations/api-playground", response_class=HTMLResponse)
    def api_playground():
        case_id = scenarios.active_case_id(db()) or svc.ensure_demo_case(db())
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/suggestion-service/api-demo", status_code=303)

    @router.post("/cases/{case_id}/lupademo/suggestion-service/api-demo")
    def api_demo_post(case_id: str, api_key: str = Form("demo-vantaa-key"), request_json: str = Form("")):
        try:
            payload = json.loads(request_json)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Invalid request JSON: {exc}")
        fake_request = type("ApiDemoRequest", (), {"headers": {"X-Lupademo-Api-Key": api_key}})()
        client = security.require_api_client(fake_request)  # type: ignore[arg-type]
        payload = security.validate_client_payload(client, payload)
        payload["request_id"] = payload.get("request_id") or f"demo_{uuid.uuid4().hex[:8]}"
        trace = [
            {"event": "request.received", "detail": "Playground request accepted", "request_id": payload["request_id"]},
            {"event": "tenant.authenticated", "detail": client["tenant_id"], "request_id": payload["request_id"]},
        ]
        documents = payload.get("documents") if isinstance(payload.get("documents"), list) else []
        is_package_analysis = bool(documents)
        agent_names = ["document_classifier", "structure_extractor", "gap_analyzer"] if is_package_analysis else ["field_suggestion_agent"]
        job_kind = "api_package_analysis" if is_package_analysis else "api_playground"
        job = svc.start_ai_job(db(), case_id, payload.get("permit_type", "building_residential"), job_kind, "API playground request", agent_names, eta_seconds=15 if is_package_analysis else 10)
        trace.append({"event": "job.started", "detail": job["id"], "request_id": payload["request_id"]})
        if is_package_analysis:
            document_text_parts = []
            document_inventory = []
            for index, doc in enumerate(documents):
                if not isinstance(doc, dict):
                    continue
                filename = str(doc.get("filename") or doc.get("name") or f"document_{index + 1}.txt")
                text = str(doc.get("text") or doc.get("content") or "")
                document_text_parts.append(f"\n\n[{filename}]\n{text}")
                document_inventory.append(
                    {
                        "document_id": str(doc.get("document_id") or f"api_doc_{index + 1}"),
                        "filename": filename,
                        "document_role": str(doc.get("document_role") or "unknown"),
                        "delivery_scope": str(doc.get("delivery_scope") or "official_submission"),
                        "text_chars": len(text),
                    }
                )
            source_text = str(payload.get("source_text") or payload.get("text") or "") + "".join(document_text_parts)
            result = security.annotate_api_response(
                client,
                svc.structure_extract(
                    {
                        **payload,
                        "form_schema": payload.get("form_schema") or svc.load_form_schema(payload.get("permit_type", "environmental_industrial")),
                        "source_text": source_text,
                    },
                    db=db(),
                    settings=settings(),
                    case_id=case_id,
                ),
            )
            result["document_inventory"] = document_inventory
            trace.append({"event": "documents.accepted", "detail": f"{len(document_inventory)} documents", "request_id": payload["request_id"]})
        else:
            result = security.annotate_api_response(client, svc.suggestion_service_field(payload, db=db(), settings=settings(), case_id=case_id))
        svc.complete_ai_job(db(), case_id, payload.get("permit_type", "building_residential"), job["id"], summary="API playground request completed.", result_artifact="suggestion_service_demo:api")
        completed_job = next((item for item in svc.list_ai_jobs(db(), case_id, payload.get("permit_type", "building_residential")) if item.get("id") == job["id"]), job)
        trace.extend(
            [
                {"event": "agent.completed", "detail": ", ".join(agent_names), "request_id": payload["request_id"]},
                {"event": "response.ready", "detail": result.get("criteria_version", ""), "request_id": payload["request_id"]},
            ]
        )
        svc.save_artifact(db(), case_id, "suggestion_service_demo", "api", {"request": payload, "response": result, "api_key": api_key, "job": completed_job, "trace_stream": trace})
        svc.append_suggestion_service_log(db(), case_id, "api_demo", client["tenant_id"], payload.get("permit_type", ""), result.get("criteria_version", ""))
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/suggestion-service/api-demo", status_code=303)

    @router.get("/cases/{case_id}/lupademo/admin/criteria", response_class=HTMLResponse)
    def criteria_admin(request: Request, case_id: str):
        return templates.TemplateResponse(
            "criteria.html",
            {
                "request": request,
                **context(case_id),
                "criteria_sets": [svc.effective_criteria(db(), case_id, permit_type) for permit_type in svc.PERMIT_SCHEMAS],
                "criteria_histories": {permit_type: svc.criteria_history(db(), case_id, permit_type) for permit_type in svc.PERMIT_SCHEMAS},
                "criteria_history_rows": {permit_type: svc.criteria_history_rows(svc.criteria_history(db(), case_id, permit_type)) for permit_type in svc.PERMIT_SCHEMAS},
                "criteria_rule_rows": {permit_type: svc.criteria_rule_rows(svc.effective_criteria(db(), case_id, permit_type)) for permit_type in svc.PERMIT_SCHEMAS},
                "criteria_test_results": {permit_type: svc.artifact_json(db(), case_id, "criteria_test_result", permit_type, {}) for permit_type in svc.PERMIT_SCHEMAS},
                "source_ingest": svc.artifact_json(db(), case_id, "source_material_ingest", "main", {}),
            },
        )

    @router.post("/cases/{case_id}/lupademo/admin/criteria")
    def criteria_admin_post(case_id: str, permit_type: str = Form(...), criteria_json: str = Form(...)):
        try:
            payload = json.loads(criteria_json)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Invalid JSON: {exc}")
        svc.save_criteria_version(db(), case_id, permit_type, payload)
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/admin/criteria", status_code=303)

    @router.post("/cases/{case_id}/lupademo/admin/criteria/rule")
    def criteria_rule_post(
        case_id: str,
        permit_type: str = Form(...),
        rule_id: str = Form(...),
        field_id: str = Form(""),
        attachment_type: str = Form(""),
        severity: str = Form("major"),
        instruction: str = Form(...),
    ):
        try:
            svc.upsert_criteria_rule(
                db(),
                case_id,
                permit_type,
                {"id": rule_id, "field_id": field_id, "attachment_type": attachment_type, "severity": severity, "instruction": instruction},
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/admin/criteria", status_code=303)

    @router.post("/cases/{case_id}/lupademo/admin/criteria/rule/delete")
    def criteria_rule_delete_post(case_id: str, permit_type: str = Form(...), rule_id: str = Form(...)):
        try:
            svc.delete_criteria_rule(db(), case_id, permit_type, rule_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/admin/criteria", status_code=303)

    @router.post("/cases/{case_id}/lupademo/admin/criteria/restore")
    def criteria_restore_post(case_id: str, permit_type: str = Form(...), history_index: int = Form(...)):
        try:
            svc.restore_criteria_version(db(), case_id, permit_type, history_index)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/admin/criteria", status_code=303)

    @router.post("/cases/{case_id}/lupademo/admin/criteria/test")
    def criteria_test_post(case_id: str, permit_type: str = Form(...)):
        svc.run_criteria_test(db(), settings(), case_id, permit_type)
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/admin/criteria", status_code=303)

    @router.post("/cases/{case_id}/lupademo/admin/source-materials/ingest")
    def source_materials_ingest(case_id: str):
        app_settings = settings()
        svc.ingest_source_materials(db(), app_settings, case_id, source_dir=getattr(app_settings, "source_material_dir", "source_materials"))
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/admin/criteria", status_code=303)

    def _demo_case_for_api() -> str:
        return scenarios.active_case_id(db()) or svc.ensure_demo_case(db())

    def _service_payload(payload: dict[str, Any], request_id: str, application_id: str) -> dict[str, Any]:
        """Fill in the request fields the route already resolved, so the service layer never sees them missing."""
        return {
            **payload,
            "request_id": request_id,
            "permit_type": application_id,
            "form_schema": payload.get("form_schema") or svc.load_form_schema(application_id),
            "form_data": payload.get("form_data") or {},
        }

    def _api_response(client: dict[str, Any], request_id: str, job: dict[str, Any], result: dict[str, Any], *, case_id: str, application_id: str) -> dict[str, Any]:
        evidence_refs: list[dict[str, Any]] = []
        for key in ("suggestions", "issues", "extracted_fields", "questions"):
            rows = result.get(key) or []
            for row in rows:
                if isinstance(row, dict) and isinstance(row.get("evidence_refs"), list):
                    evidence_refs.extend([ref for ref in row["evidence_refs"] if isinstance(ref, dict)])
        status_payload = svc.ai_job_status_payload(job, case_id=case_id, application_id=application_id)
        return {
            "request_id": request_id,
            "job_id": job.get("id"),
            "status": status_payload["status"],
            "evidence_refs": evidence_refs,
            "agent_trace": status_payload["events"],
            "tenant_id": client["tenant_id"],
            "result": result,
        }

    def _complete_api_job(case_id: str, application_id: str, job: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        return svc.complete_ai_job(db(), case_id, application_id, job["id"], summary="API job completed.", result_artifact=f"api_result:{job['kind']}")

    def _find_or_404(job_id: str) -> dict[str, Any]:
        try:
            svc.refresh_ai_job(db(), job_id)
            found = svc.find_ai_job(db(), job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="job_not_found")
        return found

    @router.post("/api/ai/jobs")
    async def api_ai_jobs(request: Request):
        payload = await request.json()
        case_id = str(payload.get("project_id") or payload.get("case_id") or _demo_case_for_api())
        application_id = svc.application_id_for(str(payload.get("application_id") or payload.get("permit_type") or "building_residential"))
        job = svc.start_ai_job(db(), case_id, application_id, str(payload.get("job_type") or "intake_analysis"), str(payload.get("title") or "AI job"), ["request_router"], eta_seconds=10)
        return svc.ai_job_status_payload(job, case_id=case_id, application_id=application_id)

    @router.get("/api/ai/jobs/{job_id}")
    def api_ai_job_status(job_id: str):
        found = _find_or_404(job_id)
        return svc.ai_job_status_payload(found["job"], case_id=found["case_id"], application_id=found["application_id"])

    @router.get("/api/ai/jobs/{job_id}/stream")
    def api_ai_job_stream(job_id: str):
        found = _find_or_404(job_id)
        payload = svc.ai_job_status_payload(found["job"], case_id=found["case_id"], application_id=found["application_id"])
        return Response("event: status\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n", media_type="text/event-stream")

    @router.get("/api/ai/jobs/{job_id}/events")
    def api_ai_job_events(job_id: str):
        found = _find_or_404(job_id)
        payload = svc.ai_job_status_payload(found["job"], case_id=found["case_id"], application_id=found["application_id"])
        return {"job_id": job_id, "status": payload["status"], "events": payload["events"]}

    @router.get("/api/ai/jobs/{job_id}/result")
    def api_ai_job_result(job_id: str):
        found = _find_or_404(job_id)
        result = svc.artifact_json(db(), found["case_id"], "api_job_result", job_id, {})
        if not result:
            raise HTTPException(status_code=404, detail="result_not_ready")
        return result

    @router.post("/api/ai/jobs/{job_id}/cancel")
    def api_ai_job_cancel(job_id: str):
        try:
            job = svc.cancel_ai_job(db(), job_id)
            found = svc.find_ai_job(db(), job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="job_not_found")
        return svc.ai_job_status_payload(job, case_id=found["case_id"], application_id=found["application_id"])

    @router.post("/api/ai/jobs/{job_id}/retry")
    def api_ai_job_retry(job_id: str):
        try:
            job = svc.retry_ai_job(db(), job_id)
            found = svc.find_ai_job(db(), job["id"])
        except KeyError:
            raise HTTPException(status_code=404, detail="job_not_found")
        return svc.ai_job_status_payload(job, case_id=found["case_id"], application_id=found["application_id"])

    return router
