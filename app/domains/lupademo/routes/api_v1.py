from __future__ import annotations
import json
import uuid
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from .template_filters import templates
from .. import scenarios, security, services as svc

def create_api_v1_router(db_getter: Callable[[], Any], settings_getter: Callable[[], Any]) -> APIRouter:
    router = APIRouter()

    def db() -> Any:
        return db_getter()

    def settings() -> Any:
        return settings_getter()

    def _demo_case_for_api() -> str:
        return scenarios.active_case_id(db()) or svc.ensure_demo_case(db())

    def _case_for_api_client(client: dict[str, Any]) -> str:
        tenant_id = str(client["tenant_id"])
        setting_key = f"lupademo_api_case_id:{tenant_id}"
        case_id = str(db().get_app_setting(setting_key) or "")
        if case_id and db().get_case(case_id):
            return case_id
        case_id = db().create_case(f"Suggestion service: {client['name']}", language="fi")
        db().set_case_status(case_id, "application_draft")
        svc.ensure_seed_state(db(), case_id)
        db().set_app_setting(setting_key, case_id)
        return case_id

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
        svc.save_artifact(db(), case_id, "api_job_result", job["id"], result)
        return svc.complete_ai_job(db(), case_id, application_id, job["id"], summary="API job completed.", result_artifact=f"api_job_result:{job['id']}")

    def _find_or_404(job_id: str) -> dict[str, Any]:
        try:
            svc.refresh_ai_job(db(), job_id)
            found = svc.find_ai_job(db(), job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="job_not_found")
        return found

    def _authorize_job_request(request: Request, found: dict[str, Any]) -> None:
        if not (request.headers.get("X-Lupademo-Api-Key") or request.headers.get("X-API-Key")):
            return
        client = security.require_api_client(request)
        if found["job"].get("tenant_id") != client["tenant_id"]:
            raise HTTPException(status_code=403, detail="job_tenant_forbidden")

    @router.post("/api/ai/jobs")
    async def api_ai_jobs(request: Request):
        payload = await request.json()
        case_id = str(payload.get("project_id") or payload.get("case_id") or _demo_case_for_api())
        application_id = svc.application_id_for(str(payload.get("application_id") or payload.get("permit_type") or "building_residential"))
        job = svc.start_ai_job(
            db(),
            case_id,
            application_id,
            str(payload.get("job_type") or "intake_analysis"),
            str(payload.get("title") or "AI job"),
            ["request_router"],
            eta_seconds=10,
        )
        return svc.ai_job_status_payload(job, case_id=case_id, application_id=application_id)

    @router.get("/api/ai/jobs/{job_id}")
    @router.get("/api/v1/jobs/{job_id}")
    def api_v1_job_status(job_id: str, request: Request):
        found = _find_or_404(job_id)
        _authorize_job_request(request, found)
        return svc.ai_job_status_payload(found["job"], case_id=found["case_id"], application_id=found["application_id"])

    @router.get("/api/ai/jobs/{job_id}/stream")
    @router.get("/api/v1/jobs/{job_id}/stream")
    def api_v1_job_stream(job_id: str, request: Request):
        found = _find_or_404(job_id)
        _authorize_job_request(request, found)
        payload = svc.ai_job_status_payload(found["job"], case_id=found["case_id"], application_id=found["application_id"])
        return Response("event: status\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n", media_type="text/event-stream")

    @router.get("/api/ai/jobs/{job_id}/events")
    @router.get("/api/v1/jobs/{job_id}/events")
    def api_v1_job_events(job_id: str, request: Request):
        found = _find_or_404(job_id)
        _authorize_job_request(request, found)
        payload = svc.ai_job_status_payload(found["job"], case_id=found["case_id"], application_id=found["application_id"])
        return {"job_id": job_id, "status": payload["status"], "events": payload["events"]}

    @router.get("/api/ai/jobs/{job_id}/result")
    @router.get("/api/v1/jobs/{job_id}/result")
    def api_v1_job_result(job_id: str, request: Request):
        found = _find_or_404(job_id)
        _authorize_job_request(request, found)
        result = svc.artifact_json(db(), found["case_id"], "api_job_result", job_id, {})
        if not result:
            raise HTTPException(status_code=404, detail="result_not_ready")
        return result

    @router.post("/api/ai/jobs/{job_id}/cancel")
    @router.post("/api/v1/jobs/{job_id}/cancel")
    def api_v1_job_cancel(job_id: str, request: Request):
        try:
            found = svc.find_ai_job(db(), job_id)
            _authorize_job_request(request, found)
            if found["job"].get("status") not in {"pending", "running"}:
                raise HTTPException(status_code=409, detail="job_not_cancellable")
            job = svc.cancel_ai_job(db(), job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="job_not_found")
        return svc.ai_job_status_payload(job, case_id=found["case_id"], application_id=found["application_id"])

    @router.post("/api/ai/jobs/{job_id}/retry")
    @router.post("/api/v1/jobs/{job_id}/retry")
    def api_v1_job_retry(job_id: str, request: Request):
        try:
            original = svc.find_ai_job(db(), job_id)
            _authorize_job_request(request, original)
            if original["job"].get("status") not in {"failed", "cancelled"}:
                raise HTTPException(status_code=409, detail="job_not_retryable")
            job = svc.retry_ai_job(db(), job_id)
            found = svc.find_ai_job(db(), job["id"])
        except KeyError:
            raise HTTPException(status_code=404, detail="job_not_found")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return svc.ai_job_status_payload(job, case_id=found["case_id"], application_id=found["application_id"])

    @router.post("/api/v1/field-suggestions")
    async def api_v1_field_suggestions(request: Request):
        client = security.require_api_client(request)
        payload = security.validate_client_payload(client, await request.json())
        case_id = _case_for_api_client(client)
        application_id = svc.application_id_for(str(payload.get("permit_type") or "building_residential"))
        request_id = str(payload.get("request_id") or f"field_{uuid.uuid4().hex[:8]}")
        job = svc.start_ai_job(db(), case_id, application_id, "field_suggestion", "Field suggestion API", ["field_suggestion_agent"], eta_seconds=8, tenant_id=client["tenant_id"])
        result = security.annotate_api_response(client, svc.suggestion_service_field(_service_payload(payload, request_id, application_id), db=db(), settings=settings(), case_id=case_id))
        completed = _complete_api_job(case_id, application_id, job, result)
        return _api_response(client, request_id, completed, result, case_id=case_id, application_id=application_id)

    @router.post("/api/v1/applications:precheck")
    async def api_v1_applications_precheck(request: Request):
        client = security.require_api_client(request)
        payload = security.validate_client_payload(client, await request.json())
        case_id = _case_for_api_client(client)
        application_id = svc.application_id_for(str(payload.get("permit_type") or "building_residential"))
        request_id = str(payload.get("request_id") or f"precheck_{uuid.uuid4().hex[:8]}")
        job = svc.start_ai_job(db(), case_id, application_id, "precheck", "Application precheck API", ["precheck_agent"], eta_seconds=10, tenant_id=client["tenant_id"])
        result = security.annotate_api_response(client, svc.suggestion_service_check(_service_payload(payload, request_id, application_id), db=db(), settings=settings(), case_id=case_id))
        completed = _complete_api_job(case_id, application_id, job, result)
        return _api_response(client, request_id, completed, result, case_id=case_id, application_id=application_id)

    @router.post("/api/v1/application-packages:analyze")
    async def api_v1_application_packages_analyze(request: Request):
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=422, detail="Invalid JSON in request body")
        client = security.require_api_client(request)
        payload = security.validate_client_payload(client, body)
        case_id = _case_for_api_client(client)
        application_id = svc.application_id_for(str(payload.get("permit_type") or "building_residential"))
        request_id = str(payload.get("request_id") or f"analyze_{uuid.uuid4().hex[:8]}")
        documents = payload.get("documents") or []
        document_text_parts: list[str] = []
        document_inventory: list[dict[str, Any]] = []
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
        if not source_text.strip():
            raise HTTPException(status_code=422, detail="At least one document with text content or source_text is required")
        job = svc.start_ai_job(
            db(),
            case_id,
            application_id,
            "api_package_analysis",
            "Application package analysis API",
            ["request_validator", "document_classifier", "structure_extractor", "gap_analyzer"],
            eta_seconds=15,
            tenant_id=client["tenant_id"],
        )
        result = security.annotate_api_response(
            client,
            svc.structure_extract(
                {
                    **payload,
                    "request_id": request_id,
                    "permit_type": application_id,
                    "form_schema": payload.get("form_schema") or svc.load_form_schema(application_id),
                    "source_text": source_text,
                },
                db=db(),
                settings=settings(),
                case_id=case_id,
            ),
        )
        result["document_inventory"] = document_inventory
        completed = _complete_api_job(case_id, application_id, job, result)
        final_response = _api_response(client, request_id, completed, result, case_id=case_id, application_id=application_id)
        svc.save_artifact(db(), case_id, "api_job_result", completed["id"], final_response)
        status_payload = svc.ai_job_status_payload(completed, case_id=case_id, application_id=application_id)
        return JSONResponse(
            status_code=200,
            content={
                "request_id": request_id,
                "job_id": completed["id"],
                "status": status_payload["status"],
                "events_url": f"/api/v1/jobs/{completed['id']}/events",
                "stream_url": f"/api/v1/jobs/{completed['id']}/stream",
                "result_url": f"/api/v1/jobs/{completed['id']}/result",
                "tenant_id": client["tenant_id"],
            },
            headers={"Location": f"/api/v1/jobs/{completed['id']}"},
        )

    @router.post("/api/v1/question-plans")
    async def api_v1_question_plans(request: Request):
        client = security.require_api_client(request)
        payload = security.validate_client_payload(client, await request.json())
        case_id = _case_for_api_client(client)
        application_id = svc.application_id_for(str(payload.get("permit_type") or "building_residential"))
        request_id = str(payload.get("request_id") or f"questions_{uuid.uuid4().hex[:8]}")
        app_payload = {"form_data": payload.get("form_data") or {}}
        questions = svc.intake_analysis_questions(svc.load_form_schema(application_id), app_payload, payload.get("attachments") or [])
        job = svc.start_ai_job(db(), case_id, application_id, "question_plan", "Question plan API", ["gap_analyzer"], eta_seconds=5, tenant_id=client["tenant_id"])
        completed = _complete_api_job(case_id, application_id, job, {"questions": questions})
        return _api_response(client, request_id, completed, {"questions": questions}, case_id=case_id, application_id=application_id)

    @router.post("/api/v1/authority:summarize")
    async def api_v1_authority_summarize(request: Request):
        client = security.require_api_client(request)
        payload = security.validate_client_payload(client, await request.json())
        case_id = _case_for_api_client(client)
        application_id = svc.application_id_for(str(payload.get("permit_type") or payload.get("application_id") or "building_residential"))
        request_id = str(payload.get("request_id") or f"authority_{uuid.uuid4().hex[:8]}")
        job = svc.start_ai_job(db(), case_id, application_id, "authority_summary", "Authority summary API", ["authority_summary_agent"], eta_seconds=8, tenant_id=client["tenant_id"])
        result = svc.authority_summary(db(), case_id, application_id, settings=settings())
        completed = _complete_api_job(case_id, application_id, job, result)
        return _api_response(client, request_id, completed, result, case_id=case_id, application_id=application_id)

    @router.post("/api/projects/{project_id}/chat/messages")
    async def api_project_chat(project_id: str, request: Request):
        payload = await request.json()
        application_id = svc.application_id_for(str(payload.get("application_id") or payload.get("permit_type") or "building_residential"))
        message = str(payload.get("message") or "")
        return svc.contextual_chat_answer(db(), project_id, application_id, message, settings=settings())

    @router.post("/api/projects/{project_id}/chat/actions/{action_id}/accept")
    async def api_project_chat_accept(project_id: str, action_id: str, request: Request):
        payload = await request.json()
        application_id = svc.application_id_for(str(payload.get("application_id") or payload.get("permit_type") or "building_residential"))
        edited_value = str(payload.get("edited_value") or "")
        try:
            application = svc.accept_suggestion(db(), project_id, application_id, action_id, edited_value)
        except KeyError:
            raise HTTPException(status_code=404, detail="action_not_found")
        return {"ok": True, "application": application}

    @router.post("/api/realtime/session")
    async def api_realtime_session(request: Request):
        state = svc.voice_feature_state(settings())
        if state.get("status") == "mock":
            return {"enabled": True, "status": "mock", "mode": "mock", "client_secret": "mock-disabled-for-tests", "reason": state["reason"]}
        return JSONResponse(status_code=503, content={"enabled": False, "status": "disabled", "mode": state.get("mode", "disabled"), "reason": state["reason"]})

    @router.post("/api/v1/suggestions/field")
    async def api_suggest_field(request: Request):
        try:
            client = security.require_api_client(request)
            payload = security.validate_client_payload(client, await request.json())
            result = security.annotate_api_response(client, svc.suggestion_service_field(payload, db=db(), settings=settings()))
            svc.append_suggestion_service_log(db(), _case_for_api_client(client), "api_v1_field", client["tenant_id"], payload.get("permit_type", ""), result.get("criteria_version", ""), endpoint="/api/v1/suggestions/field", request_id=payload.get("request_id", ""), status="ok", fallback_used=result.get("model_audit", {}).get("fallback_used", False))
            return result
        except HTTPException:
            raise
        except ValidationError as exc:
            return JSONResponse(status_code=422, content={"detail": exc.errors()})

    @router.post("/api/v1/suggestions/form")
    async def api_suggest_form(request: Request):
        try:
            client = security.require_api_client(request)
            payload = security.validate_client_payload(client, await request.json())
            result = security.annotate_api_response(client, svc.suggestion_service_form(payload, db=db(), settings=settings()))
            svc.append_suggestion_service_log(db(), _case_for_api_client(client), "api_v1_form", client["tenant_id"], payload.get("permit_type", ""), result.get("criteria_version", ""), endpoint="/api/v1/suggestions/form", request_id=payload.get("request_id", ""), status="ok", fallback_used=result.get("model_audit", {}).get("fallback_used", False))
            return result
        except HTTPException:
            raise
        except ValidationError as exc:
            return JSONResponse(status_code=422, content={"detail": exc.errors()})

    @router.post("/api/v1/checks/application")
    async def api_check(request: Request):
        try:
            client = security.require_api_client(request)
            payload = security.validate_client_payload(client, await request.json())
            result = security.annotate_api_response(client, svc.suggestion_service_check(payload, db=db(), settings=settings()))
            svc.append_suggestion_service_log(db(), _case_for_api_client(client), "api_v1_check", client["tenant_id"], payload.get("permit_type", ""), result.get("criteria_version", ""), endpoint="/api/v1/checks/application", request_id=payload.get("request_id", ""), status="ok", fallback_used=result.get("model_audit", {}).get("fallback_used", False))
            return result
        except HTTPException:
            raise
        except ValidationError as exc:
            return JSONResponse(status_code=422, content={"detail": exc.errors()})

    @router.post("/api/v1/structure/extract")
    async def api_extract(request: Request):
        try:
            client = security.require_api_client(request)
            payload = security.validate_client_payload(client, await request.json())
            result = security.annotate_api_response(client, svc.structure_extract(payload, db=db(), settings=settings()))
            svc.append_suggestion_service_log(db(), _case_for_api_client(client), "api_v1_extract", client["tenant_id"], payload.get("permit_type", ""), result.get("criteria_version", ""), endpoint="/api/v1/structure/extract", request_id=payload.get("request_id", ""), status="ok", fallback_used=result.get("model_audit", {}).get("fallback_used", False))
            return result
        except HTTPException:
            raise
        except ValidationError as exc:
            return JSONResponse(status_code=422, content={"detail": exc.errors()})

    @router.get("/api/v1/criteria/{criteria_set_id}")
    def api_criteria(criteria_set_id: str, request: Request):
        client = security.require_api_client(request)
        if not security.client_can_access_criteria(client, criteria_set_id):
            raise HTTPException(status_code=403, detail=f"Tenant {client['tenant_id']} cannot use criteria_set_id {criteria_set_id}")
        for permit in svc.CRITERIA_FILES:
            criteria = svc.load_criteria(permit)
            if criteria.get("criteria_set_id") == criteria_set_id:
                result = security.annotate_api_response(client, {**criteria, "criteria_version": criteria.get("version", "demo")})
                svc.append_suggestion_service_log(db(), _case_for_api_client(client), "api_v1_criteria", client["tenant_id"], permit, result.get("criteria_version", ""), endpoint="/api/v1/criteria/{criteria_set_id}", request_id=criteria_set_id, status="ok", fallback_used=False)
                return result
        raise HTTPException(status_code=404, detail="Criteria set not found")

    return router
