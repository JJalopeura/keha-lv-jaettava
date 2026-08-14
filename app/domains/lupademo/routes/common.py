"""Shared route helpers used across all route modules."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
import os
from typing import Any, Callable, Optional

from fastapi import HTTPException
from fastapi.templating import Jinja2Templates

from .. import services as svc
from ..services.criteria import _normalize_municipality


def finnish_datetime(value: Any) -> str:
    if value is None or value == "":
        return ""

    if isinstance(value, (int, float)):
        parsed = datetime.fromtimestamp(value)
        return parsed.strftime("%d.%m.%Y klo %H.%M")

    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text

    if len(text) == 10:
        return parsed.strftime("%d.%m.%Y")
    return parsed.strftime("%d.%m.%Y klo %H.%M")


# --- ETA defaults (F015: avoid hardcoded values in route handlers) ---
DEFAULT_JOB_ETA_SECONDS: dict[str, int] = {
    "field_suggestion": 8,
    "field_suggestion_retrieval": 15,
    "precheck": 10,
    "intake_analysis": 10,
    "authority_summary": 8,
    "question_plan": 5,
    "package_analysis": 15,
    "application_check": 35,
    "material_ingest": 30,
    "intake_processing": 20,
    "api_playground": 10,
    "api_package_analysis": 15,
}


def job_eta_seconds(job_type: str) -> int:
    """Return the ETA in seconds for a given job type."""
    return DEFAULT_JOB_ETA_SECONDS.get(job_type, 10)


FINDING_CATEGORY_LABELS: dict[str, str] = {
    "missing_required_info": "Puuttuva tieto",
    "contradiction": "Ristiriita",
    "weak_description": "Puutteellinen kuvaus",
    "attachment_scope": "Liitteen käyttötarkoitus",
    "criteria_gap": "Kriteeripuute",
}


def _dedup_suggestions(items: list[dict]) -> list[dict]:
    """Keep only the last suggestion per field_id (list order = oldest first)."""
    seen: set[str] = set()
    result: list[dict] = []
    for item in reversed(items):
        fid = str(item.get("field_id") or "")
        if fid in seen:
            continue
        seen.add(fid)
        result.append(item)
    result.reverse()
    return result


def _ai_job_view(job: dict, case_id: str, application_id: str) -> dict:
    """Merge raw job dict with status payload for template display."""
    return {**job, **svc.ai_job_status_payload(job, case_id=case_id, application_id=application_id)}


def service_payload(payload: dict[str, Any], request_id: str, application_id: str) -> dict[str, Any]:
    """Fill in the request fields the route already resolved, so the service layer never sees them missing."""
    return {
        **payload,
        "request_id": request_id,
        "permit_type": application_id,
        "form_schema": payload.get("form_schema") or svc.load_form_schema(application_id),
        "form_data": payload.get("form_data") or {},
    }


def parse_documents(payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate and parse documents from an API payload. Returns (source_text, document_inventory, source_refs)."""
    documents = payload.get("documents") or []
    document_text_parts: list[str] = []
    document_inventory: list[dict[str, Any]] = []
    for index, doc in enumerate(documents):
        if not isinstance(doc, dict):
            raise HTTPException(status_code=422, detail=f"documents[{index}] must be an object")
        filename_value = doc.get("filename") or doc.get("name")
        text_value = doc.get("text") or doc.get("content")
        if filename_value is not None and not isinstance(filename_value, str):
            raise HTTPException(status_code=422, detail=f"documents[{index}].filename must be a string")
        if text_value is not None and not isinstance(text_value, str):
            raise HTTPException(status_code=422, detail=f"documents[{index}].text must be a string")
        filename = filename_value or f"document_{index + 1}.txt"
        text = text_value or ""
        if text.strip():
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
    if not source_text.strip():
        raise HTTPException(status_code=422, detail="At least one document with text content or source_text is required")
    source_refs = [
        {"source_id": item["document_id"], "title": item["filename"], "locator": f"document:{item['document_id']}"}
        for item in document_inventory
        if item["text_chars"] > 0
    ]
    if not source_refs and source_text.strip():
        source_refs = [{"source_id": "source_text", "title": "Request source_text", "locator": "request:source_text"}]
    return source_text, document_inventory, source_refs


def validate_municipality_criteria(client: dict[str, Any], payload: dict[str, Any], application_id: str) -> None:
    """Reject requests where the municipality's criteria set is not allowlisted for this tenant."""
    municipality = str(
        (payload.get("form_data") or {}).get("municipality")
        or (payload.get("common_data") or {}).get("municipality")
        or ""
    ).strip()
    if not municipality:
        return
    criteria = svc.load_criteria(application_id, municipality)
    criteria_set_id = str(criteria.get("criteria_set_id") or "")
    if _normalize_municipality(criteria.get("municipality")) != _normalize_municipality(municipality):
        raise HTTPException(status_code=403, detail=f"No allowlisted criteria exists for municipality {municipality}")
    if criteria_set_id and criteria_set_id not in client["criteria_sets"]:
        raise HTTPException(status_code=403, detail=f"Tenant {client['tenant_id']} cannot use criteria for municipality {municipality}")


def api_response(client: dict[str, Any], request_id: str, job: dict[str, Any], result: dict[str, Any], *, case_id: str, application_id: str) -> dict[str, Any]:
    evidence_refs: list[dict[str, Any]] = []
    for key in ("suggestions", "issues", "extracted_fields", "questions"):
        rows = result.get(key) or []
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("evidence_refs"), list):
                evidence_refs.extend([ref for ref in row["evidence_refs"] if isinstance(ref, dict)])
    status_payload = svc.ai_job_status_payload(job, case_id=case_id, application_id=application_id)
    job_id = str(job.get("id") or "")
    envelope = {
        "request_id": request_id,
        "job_id": job_id,
        "status": status_payload["status"],
        "events_url": f"/api/v1/jobs/{job_id}/events",
        "stream_url": f"/api/v1/jobs/{job_id}/stream",
        "result_url": f"/api/v1/jobs/{job_id}/result",
        "evidence_refs": evidence_refs,
        "agent_trace": status_payload["events"],
        "tenant_id": client["tenant_id"],
        "result": result,
    }
    return envelope


def context_app(
    app: dict,
    case_id: str,
    permit_type: str,
    db_obj: Any,
    settings_obj: Any,
) -> dict:
    """Build application context dict used by applicant/authority/admin/demo_lab routes."""

    def aj(type_: str, default: Any) -> Any:
        return svc.artifact_json(db_obj, case_id, type_, permit_type, default)

    municipality = (
        app.get("form_data", {}).get("municipality")
        or svc.artifact_json(db_obj, case_id, "lv_common_data", "main", {"data": {}}).get("data", {}).get("municipality", "")
    )
    active_criteria = svc.effective_criteria(db_obj, case_id, app["permit_type"], municipality)
    package_analysis = aj("package_analysis_result", {})
    package_analysis_stale = svc.is_package_analysis_stale(package_analysis, active_criteria)
    ai_suggestions = aj("ai_suggestions", {"suggestions": []}).get("suggestions", [])
    field_extraction_queue = aj("field_extraction_queue", {"items": []})
    extraction_items = field_extraction_queue.get("items", [])
    attachments = svc.list_attachments(db_obj, case_id, app["permit_type"])
    attachment_by_name = {str(item.get("filename")): item for item in attachments}
    for item in extraction_items:
        refs = list(item.get("evidence_refs") or [])
        source_document = str(item.get("source_document") or "")
        source_attachment = attachment_by_name.get(source_document)
        value_terms = [term.lower() for term in str(item.get("suggested_value") or "").split() if len(term) >= 5]
        matching_attachments = [
            attachment for attachment in attachments
            if any(term in str(attachment.get("text_excerpt") or attachment.get("summary") or "").lower() for term in value_terms)
        ]
        candidates = [source_attachment] + matching_attachments if source_attachment else matching_attachments
        for attachment in candidates:
            if not attachment or len(refs) >= 3 or any(ref.get("title") == attachment.get("filename") for ref in refs if isinstance(ref, dict)):
                continue
            excerpt = str(attachment.get("text_excerpt") or attachment.get("summary") or "")
            first_section = next((line.strip() for line in excerpt.splitlines() if line.strip()), "Tekstipoiminta")
            refs.append(
                svc.source_ref(
                    str(attachment.get("filename") or "Hakemusaineisto"),
                    first_section[:120],
                    excerpt[:260],
                    float(attachment.get("confidence") or 0.7),
                )
            )
        if refs:
            item["evidence_refs"] = refs[:3]
    inline_field_suggestions = [dict(item, source_kind="suggestion") for item in ai_suggestions]
    inline_suggestion_fields = {str(item.get("field_id") or "") for item in inline_field_suggestions}
    for item in extraction_items:
        field_id = str(item.get("field_id") or "")
        if (
            field_id
            and field_id not in inline_suggestion_fields
            and item.get("status") == "pending"
            and item.get("suggested_value")
        ):
            inline_field_suggestions.append(
                {
                    **item,
                    "source_kind": "extraction",
                    "accept_url": f"/cases/{case_id}/lupademo/application/{app['application_id']}/extractions/accept",
                    "reject_url": f"/cases/{case_id}/lupademo/application/{app['application_id']}/extractions/reject",
                    "accept_field_id": field_id,
                }
            )
    return {
        "application": app,
        "schema": svc.load_form_schema(app["permit_type"]),
        "criteria": active_criteria,
        "criteria_municipality": str(active_criteria.get("municipality") or municipality or "Kansallinen oletus"),
        "criteria_version": str(active_criteria.get("version") or "demo"),
        "attachments": svc.list_attachments(db_obj, case_id, app["permit_type"]),
        "suggestions": ai_suggestions,
        "inline_field_suggestions": inline_field_suggestions,
        "check_report": aj("check_report", {}),
        "precheck_gate": svc.precheck_gate_status(db_obj, case_id, app["permit_type"]),
        "package": aj("submission_package", {}),
        "receipt": aj("receipt", {}),
        "wizard": svc.wizard_step_status(db_obj, case_id, app["permit_type"]),
        "structure_extraction": aj("structure_extraction_result", {}),
        "intake_analysis": aj("intake_analysis", {}),
        "material_package": svc.current_material_package(db_obj, case_id, app["permit_type"]),
        "package_analysis": package_analysis,
        "package_analysis_stale": package_analysis_stale,
        "field_extraction_queue": field_extraction_queue,
        "ai_jobs": svc.list_ai_jobs(db_obj, case_id, app["permit_type"]),
        "contextual_chat": aj("contextual_chat", {"messages": []}),
        "voice_state": svc.voice_feature_state(settings_obj),
        "voice_chat": aj("voice_chat", {"turns": []}),
        "environmental_story": svc.artifact_json(
            db_obj, case_id, "environmental_material_story", "main", {}
        ),
    }


def ai_mode_status(project: dict[str, Any], settings_obj: Any) -> dict[str, str]:
    """Describe the provider visible to the current demo case."""
    if project.get("presenter_mode"):
        return {"label": "AI-simulaatio", "kind": "simulation"}
    if str(os.getenv("KEHA_LV_FORCE_MOCK_LLM", "")).strip().lower() in {"1", "true", "yes", "on"}:
        return {"label": "Testin AI-simulaatio", "kind": "simulation"}
    if str(getattr(settings_obj, "llm_provider", "mock") or "mock").lower() == "azure":
        return {"label": "Azure OpenAI", "kind": "azure"}
    return {"label": "Azure OpenAI ei käytettävissä", "kind": "error"}


def context_app_with_material(
    app: dict,
    case_id: str,
    permit_type: str,
    db_obj: Any,
    settings_obj: Any,
) -> dict:
    """Context helper for applicant route — includes material ingest job info."""
    ctx = context_app(app, case_id, permit_type, db_obj, settings_obj)
    ai_jobs = ctx["ai_jobs"]
    ai_job_views = [
        _ai_job_view(job, case_id=case_id, application_id=app["permit_type"])
        for job in ai_jobs
    ]
    material_jobs = [
        job for job in ai_jobs if job.get("kind") in {"material_ingest", "intake_analysis"}
    ]
    material_job = next(
        (job for job in material_jobs if job.get("status") in {"pending", "running"}),
        material_jobs[0] if material_jobs else None,
    )
    material_job_view = next(
        (job for job in ai_job_views if material_job and job.get("id") == material_job.get("id")),
        None,
    )
    ctx.update({
        "suggestions": _dedup_suggestions(ctx["suggestions"]),
        "ai_jobs": ai_job_views,
        "material_job": material_job,
        "material_job_view": material_job_view,
    })
    return ctx


# ── RouteContext: single factory for shared route infrastructure ──

@dataclass
class RouteContext:
    """Pre‑built context injected into every route sub‑router.

    Eliminates the ~8‑line preamble duplicated across 5 route files:
    Jinja2Templates instantiation, filter/global wiring, db()/settings()
    closures, and the context() builder.
    """

    templates: Jinja2Templates
    db_getter: Callable[[], Any]
    settings_getter: Callable[[], Any]

    def db(self) -> Any:
        return self.db_getter()

    def settings(self) -> Any:
        return self.settings_getter()

    def context(
        self,
        case_id: str,
        permit_type: Optional[str] = None,
        *,
        app_ctx_builder: Callable[..., dict] = context_app,
    ) -> dict[str, Any]:
        """Build the dict every template‑rendering handler needs.

        By default uses ``context_app`` (authority / admin / demo‑lab).
        Pass ``app_ctx_builder=context_app_with_material`` for the
        applicant router, which adds material‑job fields.
        """
        svc.ensure_seed_state(self.db(), case_id)
        sync = svc.detect_common_data_changes(self.db(), case_id)
        project = svc.artifact_json(self.db(), case_id, "lv_project", "main", {})
        common = svc.artifact_json(
            self.db(), case_id, "lv_common_data", "main",
            {"data": svc.DEFAULT_COMMON_DATA.copy()},
        )
        out: dict[str, Any] = {
            "case_id": case_id,
            "case": self.db().get_case(case_id),
            "project": project,
            "common": common,
            "sync": sync,
            "ai_mode_status": ai_mode_status(project, self.settings()),
        }
        if permit_type:
            app = svc.ensure_application(self.db(), case_id, permit_type)
            out.update(app_ctx_builder(app, case_id, permit_type, self.db(), self.settings()))
        return out

