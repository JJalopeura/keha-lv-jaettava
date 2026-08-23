from __future__ import annotations
import json
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import scenarios, security, services as svc
from .common import RouteContext, job_eta_seconds, parse_documents, service_payload, validate_municipality_criteria

def create_admin_router(ctx: RouteContext) -> APIRouter:
    router = APIRouter()
    templates = ctx.templates
    db = ctx.db
    settings = ctx.settings

    def context(case_id: str, permit_type: Optional[str] = None) -> dict[str, Any]:
        return ctx.context(case_id, permit_type)

    def require_case_owner(request: Request, case_id: str) -> None:
        # F28: case-scoped admin mutations require an authenticated owning session.
        customer_id = str(request.scope.get("session", {}).get("customer_id") or "")
        owner = str((db().get_case(case_id) or {}).get("customer_id") or "")
        if not owner or customer_id != owner:
            raise HTTPException(status_code=403, detail="admin_case_forbidden")

    def playground_case_for_client(client: dict[str, Any]) -> str:
        setting_key = f"lupademo_api_case_id:{client['tenant_id']}"
        case_id = str(db().get_app_setting(setting_key) or "")
        if case_id and db().get_case(case_id):
            return case_id
        case_id = db().create_case(f"Suggestion service: {client['name']}", language="fi")
        db().set_case_status(case_id, "application_draft")
        svc.ensure_seed_state(db(), case_id)
        db().set_app_setting(setting_key, case_id)
        return case_id

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
        if result.get("response"):
            response = result.get("response") or {}
            visual_result = result.get("result") or response.get("result") or response
            form_data = (result.get("request") or {}).get("form_data") or {}
            common_data = (result.get("request") or {}).get("common_data") or {}
            municipality = str(form_data.get("municipality") or common_data.get("municipality") or "")
            result["integration_summary"] = {
                "tenant_id": str((result.get("request") or {}).get("tenant_id") or response.get("tenant_id") or ""),
                "municipality": municipality or "Kriteeristö ratkaistaan pyynnön kohteen mukaan",
                "criteria_version": str(visual_result.get("criteria_version") or ""),
                "criteria_set_id": str(visual_result.get("criteria_set_id") or ""),
                "endpoint": "POST /api/v1/application-packages:analyze" if (result.get("request") or {}).get("documents") or (result.get("request") or {}).get("source_text") else "POST /api/v1/suggestions/field",
                "result_count": len(visual_result.get("suggestions") or visual_result.get("extracted_fields") or visual_result.get("issues") or []),
            }
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
        case_id = playground_case_for_client(client)
        payload = security.validate_client_payload(client, payload)
        application_id = svc.application_id_for(str(payload.get("permit_type") or "building_residential"))
        validate_municipality_criteria(client, payload, application_id)
        payload["request_id"] = payload.get("request_id") or f"demo_{uuid.uuid4().hex[:8]}"
        trace = [
            {"event": "request.received", "detail": "Playground request accepted", "request_id": payload["request_id"]},
            {"event": "tenant.authenticated", "detail": client["tenant_id"], "request_id": payload["request_id"]},
        ]
        documents = payload.get("documents") if isinstance(payload.get("documents"), list) else []
        is_package_analysis = bool(documents or payload.get("source_text") or payload.get("text"))
        document_inventory: list[dict[str, Any]] = []
        source_text = ""
        source_refs: list[dict[str, Any]] = []
        if is_package_analysis:
            source_text, document_inventory, source_refs = parse_documents(payload)
        agent_names = ["document_classifier", "structure_extractor", "gap_analyzer"] if is_package_analysis else ["field_suggestion_agent"]
        job_kind = "api_package_analysis" if is_package_analysis else "api_playground"
        job = svc.start_ai_job(
            db(),
            case_id,
            payload.get("permit_type", "building_residential"),
            job_kind,
            "API playground request",
            agent_names,
            eta_seconds=job_eta_seconds(job_kind) if is_package_analysis else job_eta_seconds("field_suggestion"),
            tenant_id=client["tenant_id"],
        )
        trace.append({"event": "job.started", "detail": job["id"], "request_id": payload["request_id"]})
        if is_package_analysis:
            result = security.annotate_api_response(
                client,
                svc.structure_extract(
                    {
                        **payload,
                        "form_schema": payload.get("form_schema") or svc.load_form_schema(payload.get("permit_type", "environmental_industrial")),
                        "source_text": source_text,
                        "source_refs": source_refs,
                    },
                    db=db(),
                    settings=settings(),
                    case_id=case_id,
                ),
            )
            result["document_inventory"] = document_inventory
            trace.append({"event": "documents.accepted", "detail": f"{len(document_inventory)} documents", "request_id": payload["request_id"]})
        else:
            result = security.annotate_api_response(
                client,
                svc.suggestion_service_field(
                    service_payload(payload, payload["request_id"], application_id),
                    db=db(),
                    settings=settings(),
                    case_id=case_id,
                ),
            )
        trace.extend(
            [
                {"event": "agent.completed", "detail": ", ".join(agent_names), "request_id": payload["request_id"]},
                {"event": "response.ready", "detail": result.get("criteria_version", ""), "request_id": payload["request_id"]},
            ]
        )
        completed_job = dict(job)
        contract_response = {
            "request_id": payload["request_id"],
            "job_id": completed_job["id"],
            "status": "succeeded",
            "events_url": f"/api/v1/jobs/{completed_job['id']}/events",
            "stream_url": f"/api/v1/jobs/{completed_job['id']}/stream",
            "result_url": f"/api/v1/jobs/{completed_job['id']}/result",
            "tenant_id": client["tenant_id"],
            "result": result,
        }
        svc.save_artifact(db(), case_id, "api_job_result", job["id"], contract_response)
        completed_job = svc.complete_ai_job(db(), case_id, payload.get("permit_type", "building_residential"), job["id"], summary="API playground request completed.", result_artifact=f"api_job_result:{job['id']}")
        svc.save_artifact(db(), case_id, "suggestion_service_demo", "api", {"request": payload, "response": contract_response, "result": result, "api_key": api_key, "job": completed_job, "trace_stream": trace})
        svc.append_suggestion_service_log(db(), case_id, "api_demo", client["tenant_id"], payload.get("permit_type", ""), result.get("criteria_version", ""))
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/suggestion-service/api-demo", status_code=303)

    @router.get("/cases/{case_id}/lupademo/admin/criteria", response_class=HTMLResponse)
    def criteria_admin(request: Request, case_id: str):
        municipality = str(request.query_params.get("municipality") or "").strip()
        return templates.TemplateResponse(
            "admin/criteria.html",
            {
                "request": request,
                **context(case_id),
                "selected_municipality": municipality,
                "municipalities": svc.list_municipalities(db(), case_id),
                "criteria_sets": [svc.effective_criteria(db(), case_id, permit_type, municipality) for permit_type in svc.PERMIT_SCHEMAS],
                "criteria_histories": {permit_type: svc.criteria_history(db(), case_id, permit_type, municipality) for permit_type in svc.PERMIT_SCHEMAS},
                "criteria_history_rows": {permit_type: svc.criteria_history_rows(svc.criteria_history(db(), case_id, permit_type, municipality)) for permit_type in svc.PERMIT_SCHEMAS},
                "criteria_rule_rows": {permit_type: svc.criteria_rule_rows(svc.effective_criteria(db(), case_id, permit_type, municipality)) for permit_type in svc.PERMIT_SCHEMAS},
                "criteria_test_results": {
                    permit_type: svc.artifact_json(
                        db(), case_id, "criteria_test_result", f"{permit_type}:{municipality.lower()}", {}
                    )
                    for permit_type in svc.PERMIT_SCHEMAS
                },
                "source_ingest": svc.artifact_json(db(), case_id, "source_material_ingest", "main", {}),
            },
        )

    @router.post("/cases/{case_id}/lupademo/admin/criteria/city", dependencies=[Depends(require_case_owner)])
    def criteria_city_post(case_id: str, municipality: str = Form(...)):
        try:
            svc.create_city_criteria(db(), case_id, municipality)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/admin/criteria?municipality={municipality}", status_code=303)

    @router.post("/cases/{case_id}/lupademo/admin/criteria", dependencies=[Depends(require_case_owner)])
    def criteria_admin_post(case_id: str, permit_type: str = Form(...), criteria_json: str = Form(...), municipality: str = Form("")):
        try:
            payload = json.loads(criteria_json)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Invalid JSON: {exc}")
        svc.save_criteria_version(db(), case_id, permit_type, payload, municipality=municipality)
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/admin/criteria?municipality={municipality}", status_code=303)

    @router.post("/cases/{case_id}/lupademo/admin/criteria/rule", dependencies=[Depends(require_case_owner)])
    def criteria_rule_post(
        case_id: str,
        permit_type: str = Form(...),
        rule_id: str = Form(...),
        field_id: str = Form(""),
        attachment_type: str = Form(""),
        severity: str = Form("major"),
        instruction: str = Form(...),
        municipality: str = Form(""),
    ):
        try:
            svc.upsert_criteria_rule(
                db(),
                case_id,
                permit_type,
                {"id": rule_id, "field_id": field_id, "attachment_type": attachment_type, "severity": severity, "instruction": instruction},
                municipality=municipality,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/admin/criteria?municipality={municipality}", status_code=303)

    @router.post("/cases/{case_id}/lupademo/admin/criteria/rule/delete", dependencies=[Depends(require_case_owner)])
    def criteria_rule_delete_post(case_id: str, permit_type: str = Form(...), rule_id: str = Form(...), municipality: str = Form("")):
        try:
            svc.delete_criteria_rule(db(), case_id, permit_type, rule_id, municipality=municipality)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/admin/criteria?municipality={municipality}", status_code=303)

    @router.post("/cases/{case_id}/lupademo/admin/criteria/restore", dependencies=[Depends(require_case_owner)])
    def criteria_restore_post(case_id: str, permit_type: str = Form(...), history_index: int = Form(...), municipality: str = Form("")):
        try:
            svc.restore_criteria_version(db(), case_id, permit_type, history_index, municipality=municipality)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/admin/criteria?municipality={municipality}", status_code=303)

    @router.post("/cases/{case_id}/lupademo/admin/criteria/test", dependencies=[Depends(require_case_owner)])
    def criteria_test_post(case_id: str, permit_type: str = Form(...), municipality: str = Form("")):
        svc.run_criteria_test(db(), settings(), case_id, permit_type, municipality=municipality)
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/admin/criteria?municipality={municipality}", status_code=303)

    @router.post("/cases/{case_id}/lupademo/admin/source-materials/ingest", dependencies=[Depends(require_case_owner)])
    def source_materials_ingest(case_id: str):
        app_settings = settings()
        svc.ingest_source_materials(db(), app_settings, case_id, source_dir=getattr(app_settings, "source_material_dir", "source_materials"))
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/admin/criteria", status_code=303)

    return router
