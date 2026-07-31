"""Shared route helpers used across all route modules."""
from __future__ import annotations
from typing import Any

from .. import services as svc


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
        "environmental_story": svc.artifact_json(
            db_obj, case_id, "environmental_material_story", "main", {}
        ),
    }


def context_app_with_material(
    app: dict,
    case_id: str,
    permit_type: str,
    db_obj: Any,
    settings_obj: Any,
) -> dict:
    """Context helper for applicant route — includes material ingest job info."""

    def aj(type_: str, default: Any) -> Any:
        return svc.artifact_json(db_obj, case_id, type_, permit_type, default)

    ai_jobs = svc.list_ai_jobs(db_obj, case_id, app["permit_type"])
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
    return {
        "application": app,
        "schema": svc.load_form_schema(app["permit_type"]),
        "criteria": svc.load_criteria(app["permit_type"]),
        "attachments": svc.list_attachments(db_obj, case_id, app["permit_type"]),
        "suggestions": _dedup_suggestions(aj("ai_suggestions", {"suggestions": []}).get("suggestions", [])),
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
        "ai_jobs": ai_job_views,
        "material_job": material_job,
        "material_job_view": material_job_view,
        "contextual_chat": aj("contextual_chat", {"messages": []}),
        "voice_state": svc.voice_feature_state(settings_obj),
        "voice_chat": aj("voice_chat", {"turns": []}),
        "environmental_story": svc.artifact_json(
            db_obj, case_id, "environmental_material_story", "main", {}
        ),
    }
