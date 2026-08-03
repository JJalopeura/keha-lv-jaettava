from __future__ import annotations
import json
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import scenarios, security, services as svc
from .common import RouteContext, job_eta_seconds

def create_admin_router(ctx: RouteContext) -> APIRouter:
    router = APIRouter()
    templates = ctx.templates
    db = ctx.db
    settings = ctx.settings

    def context(case_id: str, permit_type: Optional[str] = None) -> dict[str, Any]:
        return ctx.context(case_id, permit_type)

    @router.get("/cases/{case_id}/lupademo/suggestion-service", response_class=HTMLResponse)
    def suggestion_service(request: Request, case_id: str):
        return templates.TemplateResponse(
            "admin/suggestion_service.html",
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
            "admin/api_demo.html",
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
        job = svc.start_ai_job(db(), case_id, payload.get("permit_type", "building_residential"), job_kind, "API playground request", agent_names, eta_seconds=job_eta_seconds(job_kind) if is_package_analysis else job_eta_seconds("field_suggestion"))
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
            "admin/criteria.html",
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

    return router
