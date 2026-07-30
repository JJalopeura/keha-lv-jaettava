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
from .template_filters import finnish_datetime

DOMAIN_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(DOMAIN_DIR / "templates"), undefined=StrictUndefined)
templates.env.filters["finnish_datetime"] = finnish_datetime
templates.env.globals["FINDING_CATEGORY_LABELS"] = {
    "missing_required_info": "Puuttuva tieto",
    "contradiction": "Ristiriita",
    "weak_description": "Puutteellinen kuvaus",
    "attachment_scope": "Liitteen käyttötarkoitus",
    "criteria_gap": "Kriteeripuute",
}

def create_demo_lab_router(db_getter: Callable[[], Any], settings_getter: Callable[[], Any]) -> APIRouter:
    
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

    @router.get("/cases/{case_id}/lupademo/suggestion-service/ui-only", response_class=HTMLResponse)
    def ui_only(request: Request, case_id: str):
        return templates.TemplateResponse(
            "ui_only.html",
            {
                "request": request,
                **context(case_id),
                "api_clients": security.public_client_options(),
                "result": svc.artifact_json(db(), case_id, "suggestion_service_demo", "ui_only", {}),
                "acceptance_log": svc.artifact_json(db(), case_id, "ui_only_acceptance_log", "main", {"items": []}),
            },
        )

    @router.get("/suggestion-service/ui-only", response_class=HTMLResponse)
    def ui_only_global():
        case_id = scenarios.active_case_id(db()) or svc.ensure_demo_case(db())
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/suggestion-service/ui-only", status_code=303)

    @router.post("/cases/{case_id}/lupademo/suggestion-service/ui-only")
    async def ui_only_post(
        case_id: str,
        tenant_id: str = Form("avi_demo"),
        permit_type: str = Form("environmental_industrial"),
        source_text: str = Form(""),
        uploaded_file: Optional[UploadFile] = File(None),
    ):
        schema = svc.load_form_schema(permit_type)
        source_documents: list[dict[str, Any]] = []
        uploaded_text = ""
        if uploaded_file is not None and uploaded_file.filename:
            content = await uploaded_file.read()
            if content:
                try:
                    svc.validate_upload_bytes(uploaded_file.filename, content)
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc))
                uploaded_text = svc.extract_text_from_bytes(uploaded_file.filename, content)
                source_documents.append(
                    {
                        "filename": uploaded_file.filename,
                        "size_bytes": len(content),
                        "text_chars": len(uploaded_text),
                        "delivery_scope": "ui_only_ephemeral",
                    }
                )
        combined_text = "\n\n".join(part.strip() for part in [source_text, uploaded_text] if part and part.strip())
        result = svc.structure_extract({"request_id": f"ui_{uuid.uuid4().hex[:8]}", "permit_type": permit_type, "form_schema": schema, "source_text": combined_text}, db=db(), settings=settings(), case_id=case_id)
        if not result["extracted_fields"]:
            result["extracted_fields"] = [{"field_id": "activity_description", "value": combined_text[:500] or "Liitä hakemusteksti tähän.", "confidence": 0.5, "evidence_refs": []}]
        result["tenant_id"] = tenant_id
        result["criteria_version"] = f"{svc.criteria_version(permit_type, db(), case_id)}:{tenant_id}"
        result["source_documents"] = source_documents
        result["source_text_chars"] = len(combined_text)
        svc.save_artifact(db(), case_id, "suggestion_service_demo", "ui_only", result)
        svc.append_suggestion_service_log(db(), case_id, "ui_only", tenant_id, permit_type, result.get("criteria_version", ""))
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/suggestion-service/ui-only", status_code=303)

    @router.post("/cases/{case_id}/lupademo/suggestion-service/ui-only/accept")
    def ui_only_accept(case_id: str, accepted_fields: list[str] = Form([])):
        result = svc.artifact_json(db(), case_id, "suggestion_service_demo", "ui_only", {})
        fields = [item for item in result.get("extracted_fields", []) if item.get("field_id") in set(accepted_fields)]
        log = svc.artifact_json(db(), case_id, "ui_only_acceptance_log", "main", {"items": []})
        log.setdefault("items", []).append(
            {
                "id": f"accept_{uuid.uuid4().hex[:8]}",
                "tenant_id": result.get("tenant_id", ""),
                "permit_type": result.get("permit_type", ""),
                "criteria_version": result.get("criteria_version", ""),
                "accepted_fields": fields,
                "created_at": time.time(),
            }
        )
        svc.save_artifact(db(), case_id, "ui_only_acceptance_log", "main", log)
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/suggestion-service/ui-only#acceptance-log", status_code=303)

    @router.get("/cases/{case_id}/lupademo/admin/benchmark-lab", response_class=HTMLResponse)
    def benchmark_lab(request: Request, case_id: str):
        return templates.TemplateResponse(
            "benchmark_lab.html",
            {"request": request, **context(case_id), "benchmark_lab": svc.benchmark_lab_snapshot(db(), case_id)},
        )

    @router.get("/cases/{case_id}/lupademo/admin/benchmarks", response_class=HTMLResponse)
    def benchmark_lab_alias(request: Request, case_id: str):
        return benchmark_lab(request, case_id)

    @router.get("/admin/benchmarks", response_class=HTMLResponse)
    def benchmark_lab_global(request: Request):
        case_id = scenarios.active_case_id(db()) or svc.ensure_demo_case(db())
        return benchmark_lab(request, case_id)

    @router.get("/cases/{case_id}/lupademo/admin/audit-trail", response_class=HTMLResponse)
    def audit_trail(request: Request, case_id: str):
        events = svc.artifact_json(db(), case_id, "lupademo_events", "main", {"events": []}).get("events", [])
        events = list(reversed([item for item in events if isinstance(item, dict)][-200:]))
        ai_calls = [
            {
                "id": item.get("id"),
                "created_at": item.get("created_at"),
                "agent": str(item.get("agent") or "").removeprefix("lupademo."),
                "model": item.get("model") or "-",
                "duration_ms": item.get("duration_ms"),
                "status": "failed" if item.get("error") else "completed",
                "error": item.get("error") or "",
            }
            for item in db().list_ai_logs(case_id=case_id, limit=200)
        ]
        return templates.TemplateResponse(
            "audit_trail.html",
            {"request": request, **context(case_id), "audit_events": events, "ai_calls": ai_calls},
        )

    @router.get("/admin/audit-trail", response_class=HTMLResponse)
    def audit_trail_global(request: Request):
        # Aggregate events and AI logs from all cases
        all_events: list[dict[str, Any]] = []
        all_ai_calls: list[dict[str, Any]] = []
        for c in db().list_cases():
            events = svc.artifact_json(db(), c["id"], "lupademo_events", "main", {"events": []}).get("events", [])
            for item in events:
                if isinstance(item, dict):
                    item["_case_id"] = c["id"]
                    all_events.append(item)
            for item in db().list_ai_logs(case_id=c["id"], limit=200):
                all_ai_calls.append({
                    "id": item.get("id"),
                    "created_at": item.get("created_at"),
                    "agent": str(item.get("agent") or "").removeprefix("lupademo."),
                    "model": item.get("model") or "-",
                    "duration_ms": item.get("duration_ms"),
                    "status": "failed" if item.get("error") else "completed",
                    "error": item.get("error") or "",
                    "_case_id": c["id"],
                })
        all_events = list(sorted(all_events, key=lambda e: e.get("ts", 0), reverse=True))[:500]
        all_ai_calls = list(sorted(all_ai_calls, key=lambda c: c.get("created_at", ""), reverse=True))[:500]
        case_id = scenarios.active_case_id(db()) or svc.ensure_demo_case(db())
        return templates.TemplateResponse(
            "audit_trail.html",
            {"request": request, **context(case_id), "audit_events": all_events, "ai_calls": all_ai_calls},
        )

    @router.post("/cases/{case_id}/lupademo/admin/benchmark-lab/run")
    def benchmark_lab_run(case_id: str):
        svc.benchmark_lab_snapshot(db(), case_id)
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/admin/benchmark-lab", status_code=303)

    @router.get("/cases/{case_id}/lupademo/admin/production-hardening", response_class=HTMLResponse)
    def production_hardening(request: Request, case_id: str):
        return templates.TemplateResponse(
            "production_hardening.html",
            {"request": request, **context(case_id), "hardening": svc.production_hardening_snapshot(db(), case_id)},
        )

    @router.get("/cases/{case_id}/lupademo/metrics", response_class=HTMLResponse)
    def metrics(request: Request, case_id: str):
        return templates.TemplateResponse(request, "metrics.html", {**context(case_id), "metrics": svc.metrics_snapshot(db(), case_id)})

    @router.get("/cases/{case_id}/lupademo/metrics/report.json")
    def metrics_report_json(case_id: str):
        return JSONResponse(svc.metrics_snapshot(db(), case_id))

    @router.get("/cases/{case_id}/lupademo/metrics/report.html", response_class=HTMLResponse)
    def metrics_report_html(request: Request, case_id: str):
        metrics_payload = svc.metrics_snapshot(db(), case_id)
        return templates.TemplateResponse(request, "metrics_report.html", {"metrics_payload": metrics_payload})

    @router.post("/cases/{case_id}/lupademo/admin/agent-chain/audit-run")
    def agent_chain_audit_run(case_id: str):
        svc.run_agent_chain_audit(db(), settings(), case_id)
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/metrics", status_code=303)

    # --- Global redirects (no case_id in URL) ---

    @router.get("/metrics", response_class=HTMLResponse)
    def metrics_global(request: Request):
        case_id = scenarios.active_case_id(db()) or svc.ensure_demo_case(db())
        # Aggregate metrics across all cases
        aggregated: dict[str, Any] = {}
        counters = ["application_count", "submitted_count", "accepted_suggestions", "rejected_suggestions",
                     "checks_run", "detected_issues", "blocking_issues_before", "blocking_issues_after",
                     "major_issues_before", "major_issues_after", "info_request_drafts", "completed_ai_jobs",
                     "contextual_chat_suggestions", "fallback_count"]
        scores: list[float] = []
        for c in db().list_cases():
            m = svc.metrics_snapshot(db(), c["id"])
            for key in counters:
                aggregated[key] = aggregated.get(key, 0) + int(m.get(key, 0) or 0)
            if m.get("average_quality_score"):
                scores.append(float(m["average_quality_score"]))
        if scores:
            m = svc.metrics_snapshot(db(), case_id)
            m.update(aggregated)
            m["average_quality_score"] = round(sum(scores) / len(scores), 1)
            m["application_count"] = aggregated.get("application_count", 0)
            m["submitted_count"] = aggregated.get("submitted_count", 0)
        else:
            m = svc.metrics_snapshot(db(), case_id)
            m.update(aggregated)
        return templates.TemplateResponse(request, "metrics.html", {**context(case_id), "metrics": m})

    @router.get("/metrics/report.html", response_class=HTMLResponse)
    def metrics_report_global(request: Request):
        case_id = scenarios.active_case_id(db()) or svc.ensure_demo_case(db())
        return metrics_report_html(request, case_id)

    @router.get("/metrics/report.json")
    def metrics_report_json_global(case_id: str = ""):
        cid = case_id or scenarios.active_case_id(db()) or svc.ensure_demo_case(db())
        return metrics_report_json(cid)

    @router.get("/admin/criteria", response_class=HTMLResponse)
    def criteria_global(request: Request):
        case_id = scenarios.active_case_id(db()) or svc.ensure_demo_case(db())
        ctx = context(case_id)
        permit_types = list(svc.PERMIT_SCHEMAS.keys())
        ctx.update({
            "criteria_sets": [svc.effective_criteria(db(), case_id, pt) for pt in permit_types],
            "criteria_histories": {pt: svc.criteria_history(db(), case_id, pt) for pt in permit_types},
            "criteria_history_rows": {pt: svc.criteria_history_rows(svc.criteria_history(db(), case_id, pt)) for pt in permit_types},
            "criteria_rule_rows": {pt: svc.criteria_rule_rows(svc.effective_criteria(db(), case_id, pt)) for pt in permit_types},
            "criteria_test_results": {pt: svc.artifact_json(db(), case_id, "criteria_test_result", pt, {}) for pt in permit_types},
            "source_ingest": svc.artifact_json(db(), case_id, "source_material_ingest", "main", {}),
        })
        return templates.TemplateResponse("criteria.html", {**ctx, "request": request})

    @router.get("/admin/suggestion-service", response_class=HTMLResponse)
    def suggestion_service_global(request: Request):
        case_id = scenarios.active_case_id(db()) or svc.ensure_demo_case(db())
        ctx = context(case_id)
        ctx.update({
            "api_clients": security.public_client_options(),
            "request_log": svc.artifact_json(db(), case_id, "suggestion_service_request_log", "main", {"items": []}),
        })
        return templates.TemplateResponse("suggestion_service.html", {**ctx, "request": request})

    @router.get("/admin/production-hardening", response_class=HTMLResponse)
    def production_hardening_global(request: Request):
        case_id = scenarios.active_case_id(db()) or svc.ensure_demo_case(db())
        return production_hardening(request, case_id)

    @router.get("/demo-lab", response_class=HTMLResponse)
    def demo_lab_index(request: Request):
        case_id = scenarios.active_case_id(db()) or svc.ensure_demo_case(db())
        # Aggregate key metrics across all cases
        all_metrics = {"application_count": 0, "submitted_count": 0, "average_quality_score": 0, "fallback_count": 0}
        all_eval = {"authority_example_count": 0, "target_count": 0}
        all_readiness = {"ready_count": 0, "total_count": 0}
        quality_scores: list[float] = []
        for c in db().list_cases():
            m = svc.metrics_snapshot(db(), c["id"])
            all_metrics["application_count"] += m.get("application_count", 0)
            all_metrics["submitted_count"] += m.get("submitted_count", 0)
            all_metrics["fallback_count"] += m.get("fallback_count", 0)
            if m.get("average_quality_score"):
                quality_scores.append(float(m["average_quality_score"]))
            e = svc.benchmark_lab_snapshot(db(), c["id"])
            all_eval["authority_example_count"] += e.get("authority_example_count", 0)
            all_eval["target_count"] += e.get("target_count", 0)
            r = svc.production_hardening_snapshot(db(), c["id"])
            all_readiness["ready_count"] += r.get("ready_count", 0)
            all_readiness["total_count"] += r.get("total_count", 0)
        if quality_scores:
            all_metrics["average_quality_score"] = round(sum(quality_scores) / len(quality_scores), 1)
        return templates.TemplateResponse(
            request,
            "demo_lab.html",
            {
                **context(case_id),
                "metrics": all_metrics,
                "evaluation": all_eval,
                "readiness": all_readiness,
            },
        )

    return router
