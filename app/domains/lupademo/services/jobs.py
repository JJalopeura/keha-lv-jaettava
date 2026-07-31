"""Generic AI job lifecycle (start/advance/complete/cancel/retry).
Depends on core only.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

from .core import PERMIT_SCHEMAS, _list, application_id_for, artifact_json, event, save_artifact


AI_JOB_AGENT_LABELS = {
    "archive_validator": "Paketin tarkistus",
    "text_extractor": "Tekstin poiminta",
    "material_classifier": "Aineiston luokittelu",
    "vector_indexer": "Hakukontekstin valmistelu",
    "structure_extractor": "Rakenteistusagentti",
    "gap_analyzer": "Puutteiden tunnistus",
    "question_generator": "Kysymysten muodostus",
    "field_suggestion_agent": "Kenttäehdotusagentti",
    "form_suggestion_agent": "Lomake-ehdotusagentti",
    "completeness_validator": "Täydellisyystarkistus",
    "consistency_validator": "Ristiriitatarkistus",
    "criteria_assessor": "Kriteeriarviointi",
    "caseworker_summary_agent": "Käsittelijän yhteenvetoagentti",
    "additional_info_request_agent": "Täydennyspyyntöagentti",
    "authority_memo_draft_agent": "Valmistelumuistioagentti",
}


def _job_store(db: Any, case_id: str, permit_type: str) -> dict[str, Any]:
    return artifact_json(db, case_id, "ai_jobs", application_id_for(permit_type), {"jobs": []})


def list_ai_jobs(db: Any, case_id: str, permit_type: str, limit: int = 8) -> list[dict[str, Any]]:
    _refresh_ai_jobs_for_case_permit(db, case_id, permit_type)
    store = _job_store(db, case_id, permit_type)
    jobs = _list(store.get("jobs"))
    return sorted(jobs or [], key=lambda item: float(item.get("updated_at") or item.get("created_at") or 0), reverse=True)[:limit]


def find_ai_job(db: Any, job_id: str) -> dict[str, Any]:
    for case in db.list_cases():
        case_id = str(case.get("id") or "")
        for permit_type in PERMIT_SCHEMAS:
            for job in artifact_json(db, case_id, "ai_jobs", permit_type, {"jobs": []}).get("jobs", []):
                if isinstance(job, dict) and job.get("id") == job_id:
                    return {"case_id": case_id, "application_id": permit_type, "job": job}
    raise KeyError(job_id)


def ai_job_status_payload(job: dict[str, Any], *, case_id: str = "", application_id: str = "") -> dict[str, Any]:
    status_map = {"completed": "succeeded", "pending": "queued"}
    status = status_map.get(str(job.get("status") or ""), str(job.get("status") or "running"))
    updated_at = float(job.get("updated_at") or job.get("created_at") or time.time())
    events_payload = []
    for index, agent in enumerate(job.get("agents") or []):
        if not isinstance(agent, dict):
            continue
        events_payload.append(
            {
                "event_id": f"{job.get('id')}_agent_{index}",
                "timestamp": float(agent.get("finished_at") or agent.get("started_at") or updated_at),
                "level": "error" if status == "failed" else "info",
                "agent": agent.get("name"),
                "step": agent.get("label") or agent.get("name") or "agent",
                "message": agent.get("summary") or job.get("summary") or job.get("title") or "AI job step",
                "progress_percent": int(job.get("progress_pct") or 0),
                "estimated_seconds_remaining": int(job.get("eta_seconds") or 0),
                "artifact_ref": job.get("result_artifact") or None,
            }
        )
    if not events_payload:
        events_payload.append(
            {
                "event_id": f"{job.get('id')}_status",
                "timestamp": updated_at,
                "level": "info" if status != "failed" else "error",
                "agent": None,
                "step": str(job.get("kind") or "job"),
                "message": str(job.get("summary") or job.get("title") or "AI job"),
                "progress_percent": int(job.get("progress_pct") or 0),
                "estimated_seconds_remaining": int(job.get("eta_seconds") or 0),
                "artifact_ref": job.get("result_artifact") or None,
            }
        )
    return {
        "job_id": job.get("id"),
        "job_type": job.get("kind"),
        "title": job.get("title") or job.get("kind") or "AI job",
        "status": status if status in {"queued", "running", "needs_user_input", "succeeded", "failed", "cancelled"} else "running",
        "progress_percent": int(job.get("progress_pct") or 0),
        "estimated_seconds_total": int(job.get("estimated_seconds_total") or job.get("eta_seconds") or 0),
        "estimated_seconds_remaining": int(job.get("eta_seconds") or 0),
        "current_agent": next((agent.get("name") for agent in job.get("agents", []) if isinstance(agent, dict) and agent.get("status") not in {"completed", "failed"}), None),
        "current_step_label": next((agent.get("label") for agent in job.get("agents", []) if isinstance(agent, dict) and agent.get("status") not in {"completed", "failed"}), None),
        "events": events_payload,
        "project_id": case_id or None,
        "case_id": case_id or None,
        "application_id": application_id or job.get("permit_type") or None,
        "result_artifact_type": str(job.get("result_artifact") or "").split(":", 1)[0] or None,
        "result_artifact_name": str(job.get("result_artifact") or "").split(":", 1)[1] if ":" in str(job.get("result_artifact") or "") else None,
        "error_code": "job_failed" if status == "failed" else None,
        "error_message": str(job.get("summary") or "") if status == "failed" else None,
        "created_at": float(job.get("created_at") or updated_at),
        "started_at": float(job.get("created_at") or updated_at),
        "updated_at": updated_at,
        "completed_at": float(job.get("finished_at") or updated_at) if status in {"succeeded", "failed", "cancelled"} else None,
    }


def cancel_ai_job(db: Any, job_id: str) -> dict[str, Any]:
    found = find_ai_job(db, job_id)
    case_id = found["case_id"]
    permit_type = found["application_id"]
    store = _job_store(db, case_id, permit_type)
    now = time.time()
    selected: dict[str, Any] = {}
    for job in store.get("jobs", []):
        if job.get("id") == job_id:
            if job.get("status") not in {"pending", "running"}:
                raise ValueError("job_not_cancellable")
            job["status"] = "cancelled"
            job["eta_seconds"] = 0
            job["updated_at"] = now
            job["finished_at"] = now
            job["summary"] = job.get("summary") or "AI job cancelled."
            selected = job
            break
    save_artifact(db, case_id, "ai_jobs", permit_type, store)
    event(db, case_id, "ai.job.cancelled", "AI job cancelled", {"job_id": job_id, "permit_type": permit_type})
    return selected


def retry_ai_job(db: Any, job_id: str) -> dict[str, Any]:
    found = find_ai_job(db, job_id)
    original = found["job"]
    if original.get("status") not in {"failed", "cancelled"}:
        raise ValueError("job_not_retryable")
    raise ValueError("job_retry_not_supported")


def _job_agent_steps(agent_names: list[str], status: str = "pending") -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "label": AI_JOB_AGENT_LABELS.get(name, name),
            "status": status,
            "started_at": None,
            "finished_at": None,
            "summary": "",
        }
        for name in agent_names
    ]


def start_ai_job(
    db: Any,
    case_id: str,
    permit_type: str,
    kind: str,
    title: str,
    agent_names: list[str],
    eta_seconds: int = 20,
    *,
    auto_finalize: bool = False,
    managed_background: bool = False,
    tenant_id: str = "",
) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    now = time.time()
    job = {
        "id": f"job_{uuid.uuid4().hex[:10]}",
        "kind": kind,
        "title": title,
        "permit_type": permit_type,
        "status": "running",
        "progress_current": 0,
        "progress_total": max(1, len(agent_names)),
        "progress_pct": 0,
        "eta_seconds": eta_seconds,
        "estimated_seconds_total": eta_seconds,
        "auto_finalize": auto_finalize,
        "managed_background": managed_background,
        "tenant_id": tenant_id,
        "agents": _job_agent_steps(agent_names),
        "created_at": now,
        "updated_at": now,
        "finished_at": None,
        "result_artifact": "",
        "summary": "",
    }
    store = _job_store(db, case_id, permit_type)
    store.setdefault("jobs", []).append(job)
    store["jobs"] = store["jobs"][-20:]
    save_artifact(db, case_id, "ai_jobs", permit_type, store)
    event(db, case_id, "ai.job.started", "AI job started", {"job_id": job["id"], "kind": kind, "permit_type": permit_type})
    return job


def _job_simulation_seconds() -> float:
    raw = os.getenv("LUPADEMO_AI_JOB_TOTAL_SECONDS", "4")
    try:
        return max(0.2, float(raw))
    except ValueError:
        return 4.0


def _refresh_ai_jobs_for_case_permit(db: Any, case_id: str, permit_type: str) -> None:
    permit_type = application_id_for(permit_type)
    store = _job_store(db, case_id, permit_type)
    changed = False
    for job in store.get("jobs", []):
        if isinstance(job, dict) and job.get("status") == "running":
            _advance_ai_job_in_store(db, case_id, permit_type, job)
            changed = True
    if changed:
        save_artifact(db, case_id, "ai_jobs", permit_type, store)


def refresh_ai_job(db: Any, job_id: str) -> dict[str, Any]:
    found = find_ai_job(db, job_id)
    case_id = found["case_id"]
    permit_type = found["application_id"]
    store = _job_store(db, case_id, permit_type)
    selected: dict[str, Any] = {}
    changed = False
    for job in store.get("jobs", []):
        if isinstance(job, dict) and job.get("id") == job_id:
            selected = job
            if job.get("status") == "running":
                _advance_ai_job_in_store(db, case_id, permit_type, job)
                changed = True
            break
    if changed:
        save_artifact(db, case_id, "ai_jobs", permit_type, store)
    return selected or found["job"]


def _advance_ai_job_in_store(db: Any, case_id: str, permit_type: str, job: dict[str, Any]) -> None:
    now = time.time()
    created_at = float(job.get("created_at") or now)
    total_seconds = float(job.get("estimated_seconds_total") or 20) if job.get("managed_background") else _job_simulation_seconds()
    elapsed = max(0.0, now - created_at)
    progress = max(1, min(99, int((elapsed / total_seconds) * 100)))
    agent_count = max(1, len(job.get("agents") or []))
    active_index = min(agent_count - 1, int((progress / 100) * agent_count))
    job["progress_pct"] = progress
    job["progress_current"] = min(agent_count, active_index + 1)
    job["eta_seconds"] = max(1, int(round(total_seconds - elapsed))) if progress < 99 else 1
    job["updated_at"] = now
    for index, agent in enumerate(job.get("agents") or []):
        if not isinstance(agent, dict):
            continue
        if index < active_index:
            agent["status"] = "completed"
            agent["started_at"] = agent.get("started_at") or created_at
            agent["finished_at"] = agent.get("finished_at") or now
            agent["summary"] = agent.get("summary") or f"{agent.get('label') or agent.get('name')} valmis."
        elif index == active_index:
            agent["status"] = "running"
            agent["started_at"] = agent.get("started_at") or now
            agent["finished_at"] = None
            agent["summary"] = agent.get("summary") or f"{agent.get('label') or agent.get('name')} käynnissä."
        else:
            agent["status"] = "pending"
            agent["started_at"] = None
            agent["finished_at"] = None
    if elapsed >= total_seconds and job.get("managed_background"):
        job["progress_pct"] = 95
        job["progress_current"] = max(1, agent_count)
        job["eta_seconds"] = 0
    elif elapsed >= total_seconds:
        if job.get("auto_finalize") and job.get("kind") == "intake_analysis":
            # deferred import: application.py depends on jobs.py, not the reverse -
            # this is the one legitimate job-runner -> feature-finalizer callback.
            from .application import _finalize_intake_analysis_job_in_store

            _finalize_intake_analysis_job_in_store(db, case_id, permit_type, job)
        else:
            job["status"] = "completed"
            job["progress_current"] = agent_count
            job["progress_pct"] = 100
            job["eta_seconds"] = 0
            job["finished_at"] = now
            job["summary"] = job.get("summary") or "AI-ajo valmis."
            for agent in job.get("agents") or []:
                if isinstance(agent, dict):
                    agent["status"] = "completed"
                    agent["started_at"] = agent.get("started_at") or created_at
                    agent["finished_at"] = now
                    agent["summary"] = agent.get("summary") or "Valmis."


def complete_ai_job(
    db: Any,
    case_id: str,
    permit_type: str,
    job_id: str,
    *,
    summary: str,
    result_artifact: str = "",
    failed: bool = False,
) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    store = _job_store(db, case_id, permit_type)
    now = time.time()
    selected: dict[str, Any] = {}
    for job in store.get("jobs", []):
        if job.get("id") != job_id:
            continue
        selected = job
        if job.get("status") == "cancelled":
            break
        job["status"] = "failed" if failed else "completed"
        job["progress_current"] = job.get("progress_total") or len(job.get("agents") or []) or 1
        job["progress_pct"] = 100
        job["eta_seconds"] = 0
        job["updated_at"] = now
        job["finished_at"] = now
        job["summary"] = summary
        job["result_artifact"] = result_artifact
        for agent in job.get("agents", []):
            agent["status"] = "failed" if failed else "completed"
            agent["started_at"] = agent.get("started_at") or job.get("created_at")
            agent["finished_at"] = now
            agent["summary"] = summary if not agent.get("summary") else agent.get("summary")
        break
    save_artifact(db, case_id, "ai_jobs", permit_type, store)
    event(db, case_id, "ai.job.completed" if not failed else "ai.job.failed", "AI job finished", {"job_id": job_id, "permit_type": permit_type, "status": selected.get("status")})
    return selected
