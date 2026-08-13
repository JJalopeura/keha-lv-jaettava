from __future__ import annotations
import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from .. import scenarios, security, services as svc
from .common import RouteContext, api_response, job_eta_seconds, parse_documents, service_payload, validate_municipality_criteria

def create_api_v1_router(ctx: RouteContext) -> APIRouter:
    router = APIRouter()
    db = ctx.db
    settings = ctx.settings

    async def json_payload(request: Request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=422, detail="Invalid JSON in request body") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="request body must be a JSON object")
        return payload

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

    def _complete_api_job(case_id: str, application_id: str, job: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        return svc.complete_ai_job(db(), case_id, application_id, job["id"], summary="API job completed.", result_artifact=f"api_job_result:{job['id']}")

    def _legacy_api_response(
        client: dict[str, Any],
        payload: dict[str, Any],
        application_id: str,
        kind: str,
        title: str,
        agents: list[str],
        result: dict[str, Any],
        case_id: str,
    ) -> dict[str, Any]:
        job = svc.start_ai_job(
            db(),
            case_id,
            application_id,
            kind,
            title,
            agents,
            eta_seconds=job_eta_seconds(kind),
            tenant_id=client["tenant_id"],
        )
        annotated = security.annotate_api_response(client, result)
        completed = _complete_api_job(case_id, application_id, job, annotated)
        envelope = api_response(
            client,
            str(payload.get("request_id") or ""),
            completed,
            annotated,
            case_id=case_id,
            application_id=application_id,
        )
        for key in ("criteria_version", "suggestions", "issues", "extracted_fields", "warnings", "score", "summary", "model_audit"):
            if key in annotated:
                envelope[key] = annotated[key]
        svc.save_artifact(db(), case_id, "api_job_result", completed["id"], envelope)
        return envelope

    def _find_or_404(job_id: str) -> dict[str, Any]:
        try:
            svc.refresh_ai_job(db(), job_id)
            found = svc.find_ai_job(db(), job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="job_not_found")
        return found

    def _authorize_job_request(request: Request, found: dict[str, Any]) -> None:
        client = security.require_api_client(request)
        if found["job"].get("tenant_id") != client["tenant_id"]:
            raise HTTPException(status_code=403, detail="job_tenant_forbidden")

    @router.post("/api/v1/ai/jobs")
    async def api_v1_ai_jobs(request: Request):
        client = security.require_api_client(request)
        payload = security.validate_client_payload(client, await json_payload(request))
        case_id = _case_for_api_client(client)
        application_id = svc.application_id_for(str(payload.get("application_id") or payload.get("permit_type") or "building_residential"))
        if application_id not in client["permit_types"]:
            raise HTTPException(status_code=403, detail=f"Tenant {client['tenant_id']} cannot use permit_type {application_id}")
        validate_municipality_criteria(client, payload, application_id)
        job = svc.start_ai_job(
            db(),
            case_id,
            application_id,
            str(payload.get("job_type") or "intake_analysis"),
            str(payload.get("title") or "AI job"),
            ["request_router"],
            eta_seconds=job_eta_seconds("intake_analysis"),
            tenant_id=client["tenant_id"],
        )
        return svc.ai_job_status_payload(job, case_id=case_id, application_id=application_id)

    @router.get("/api/v1/jobs/{job_id}")
    def api_v1_job_status(job_id: str, request: Request):
        found = _find_or_404(job_id)
        _authorize_job_request(request, found)
        return svc.ai_job_status_payload(found["job"], case_id=found["case_id"], application_id=found["application_id"])

    @router.get("/api/v1/jobs/{job_id}/stream")
    def api_v1_job_stream(job_id: str, request: Request):
        found = _find_or_404(job_id)
        _authorize_job_request(request, found)
        payload = svc.ai_job_status_payload(found["job"], case_id=found["case_id"], application_id=found["application_id"])
        return Response("event: status\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n", media_type="text/event-stream")

    @router.get("/api/v1/jobs/{job_id}/events")
    def api_v1_job_events(job_id: str, request: Request):
        found = _find_or_404(job_id)
        _authorize_job_request(request, found)
        payload = svc.ai_job_status_payload(found["job"], case_id=found["case_id"], application_id=found["application_id"])
        return {"job_id": job_id, "status": payload["status"], "events": payload["events"]}

    @router.get("/api/v1/jobs/{job_id}/result")
    def api_v1_job_result(job_id: str, request: Request):
        found = _find_or_404(job_id)
        _authorize_job_request(request, found)
        result = svc.artifact_json(db(), found["case_id"], "api_job_result", job_id, {})
        if not result:
            raise HTTPException(status_code=404, detail="result_not_ready")
        return result

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
        payload = security.validate_client_payload(client, await json_payload(request))
        application_id = svc.application_id_for(str(payload.get("permit_type") or "building_residential"))
        validate_municipality_criteria(client, payload, application_id)
        case_id = _case_for_api_client(client)
        request_id = str(payload.get("request_id") or f"field_{uuid.uuid4().hex[:8]}")
        job = svc.start_ai_job(db(), case_id, application_id, "field_suggestion", "Field suggestion API", ["field_suggestion_agent"], eta_seconds=job_eta_seconds("field_suggestion"), tenant_id=client["tenant_id"])
        result = security.annotate_api_response(client, svc.suggestion_service_field(service_payload(payload, request_id, application_id), db=db(), settings=settings(), case_id=case_id))
        completed = _complete_api_job(case_id, application_id, job, result)
        envelope = api_response(client, request_id, completed, result, case_id=case_id, application_id=application_id)
        svc.save_artifact(db(), case_id, "api_job_result", completed["id"], envelope)
        return envelope

    @router.post("/api/v1/applications:precheck")
    async def api_v1_applications_precheck(request: Request):
        client = security.require_api_client(request)
        payload = security.validate_client_payload(client, await json_payload(request))
        application_id = svc.application_id_for(str(payload.get("permit_type") or "building_residential"))
        validate_municipality_criteria(client, payload, application_id)
        case_id = _case_for_api_client(client)
        request_id = str(payload.get("request_id") or f"precheck_{uuid.uuid4().hex[:8]}")
        job = svc.start_ai_job(db(), case_id, application_id, "precheck", "Application precheck API", ["precheck_agent"], eta_seconds=job_eta_seconds("precheck"), tenant_id=client["tenant_id"])
        result = security.annotate_api_response(client, svc.suggestion_service_check(service_payload(payload, request_id, application_id), db=db(), settings=settings(), case_id=case_id))
        completed = _complete_api_job(case_id, application_id, job, result)
        envelope = api_response(client, request_id, completed, result, case_id=case_id, application_id=application_id)
        svc.save_artifact(db(), case_id, "api_job_result", completed["id"], envelope)
        return envelope

    @router.post("/api/v1/application-packages:analyze")
    async def api_v1_application_packages_analyze(request: Request):
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=422, detail="Invalid JSON in request body")
        client = security.require_api_client(request)
        payload = security.validate_client_payload(client, body)
        application_id = svc.application_id_for(str(payload.get("permit_type") or "building_residential"))
        validate_municipality_criteria(client, payload, application_id)
        case_id = _case_for_api_client(client)
        request_id = str(payload.get("request_id") or f"analyze_{uuid.uuid4().hex[:8]}")
        source_text, document_inventory, source_refs = parse_documents(payload)
        job = svc.start_ai_job(
            db(),
            case_id,
            application_id,
            "api_package_analysis",
            "Application package analysis API",
            ["request_validator", "document_classifier", "structure_extractor", "gap_analyzer"],
            eta_seconds=job_eta_seconds("package_analysis"),
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
                        "source_refs": source_refs,
                },
                db=db(),
                settings=settings(),
                case_id=case_id,
            ),
        )
        result["document_inventory"] = document_inventory
        completed = _complete_api_job(case_id, application_id, job, result)
        final_response = api_response(client, request_id, completed, result, case_id=case_id, application_id=application_id)
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
        payload = security.validate_client_payload(client, await json_payload(request))
        case_id = _case_for_api_client(client)
        application_id = svc.application_id_for(str(payload.get("permit_type") or "building_residential"))
        validate_municipality_criteria(client, payload, application_id)
        request_id = str(payload.get("request_id") or f"questions_{uuid.uuid4().hex[:8]}")
        app_payload = {"form_data": payload.get("form_data") or {}}
        questions = svc.intake_analysis_questions(svc.load_form_schema(application_id), app_payload, payload.get("attachments") or [])
        job = svc.start_ai_job(db(), case_id, application_id, "question_plan", "Question plan API", ["gap_analyzer"], eta_seconds=job_eta_seconds("question_plan"), tenant_id=client["tenant_id"])
        completed = _complete_api_job(case_id, application_id, job, {"questions": questions})
        envelope = api_response(client, request_id, completed, {"questions": questions}, case_id=case_id, application_id=application_id)
        svc.save_artifact(db(), case_id, "api_job_result", completed["id"], envelope)
        return envelope

    @router.post("/api/v1/authority:summarize")
    async def api_v1_authority_summarize(request: Request):
        client = security.require_api_client(request)
        payload = security.validate_client_payload(client, await json_payload(request))
        case_id = _case_for_api_client(client)
        application_id = svc.application_id_for(str(payload.get("permit_type") or payload.get("application_id") or "building_residential"))
        validate_municipality_criteria(client, payload, application_id)
        request_id = str(payload.get("request_id") or f"authority_{uuid.uuid4().hex[:8]}")
        job = svc.start_ai_job(db(), case_id, application_id, "authority_summary", "Authority summary API", ["authority_summary_agent"], eta_seconds=job_eta_seconds("authority_summary"), tenant_id=client["tenant_id"])
        result = svc.authority_summary(db(), case_id, application_id, settings=settings())
        completed = _complete_api_job(case_id, application_id, job, result)
        envelope = api_response(client, request_id, completed, result, case_id=case_id, application_id=application_id)
        svc.save_artifact(db(), case_id, "api_job_result", completed["id"], envelope)
        return envelope





    @router.post("/api/v1/suggestions/field")
    async def api_suggest_field(request: Request):
        try:
            client = security.require_api_client(request)
            payload = security.validate_client_payload(client, await json_payload(request))
            application_id = svc.application_id_for(str(payload.get("permit_type") or "building_residential"))
            validate_municipality_criteria(client, payload, application_id)
            case_id = _case_for_api_client(client)
            result = _legacy_api_response(
                client, payload, application_id, "field_suggestion", "Legacy field suggestion API", ["field_suggestion_agent"],
                svc.suggestion_service_field(payload, db=db(), settings=settings(), case_id=case_id),
                case_id,
            )
            svc.append_suggestion_service_log(db(), case_id, "api_v1_field", client["tenant_id"], payload.get("permit_type", ""), result.get("criteria_version", ""), endpoint="/api/v1/suggestions/field", request_id=payload.get("request_id", ""), status="ok", fallback_used=result.get("model_audit", {}).get("fallback_used", False))
            return result
        except HTTPException:
            raise
        except ValidationError as exc:
            return JSONResponse(status_code=422, content={"detail": exc.errors()})

    @router.post("/api/v1/suggestions/form")
    async def api_suggest_form(request: Request):
        try:
            client = security.require_api_client(request)
            payload = security.validate_client_payload(client, await json_payload(request))
            application_id = svc.application_id_for(str(payload.get("permit_type") or "building_residential"))
            validate_municipality_criteria(client, payload, application_id)
            case_id = _case_for_api_client(client)
            result = _legacy_api_response(
                client, payload, application_id, "form_suggestion", "Legacy form suggestion API", ["form_suggestion_agent"],
                svc.suggestion_service_form(payload, db=db(), settings=settings(), case_id=case_id),
                case_id,
            )
            svc.append_suggestion_service_log(db(), case_id, "api_v1_form", client["tenant_id"], payload.get("permit_type", ""), result.get("criteria_version", ""), endpoint="/api/v1/suggestions/form", request_id=payload.get("request_id", ""), status="ok", fallback_used=result.get("model_audit", {}).get("fallback_used", False))
            return result
        except HTTPException:
            raise
        except ValidationError as exc:
            return JSONResponse(status_code=422, content={"detail": exc.errors()})

    @router.post("/api/v1/checks/application")
    async def api_check(request: Request):
        try:
            client = security.require_api_client(request)
            payload = security.validate_client_payload(client, await json_payload(request))
            application_id = svc.application_id_for(str(payload.get("permit_type") or "building_residential"))
            validate_municipality_criteria(client, payload, application_id)
            case_id = _case_for_api_client(client)
            result = _legacy_api_response(
                client, payload, application_id, "precheck", "Legacy application check API", ["precheck_agent"],
                svc.suggestion_service_check(payload, db=db(), settings=settings(), case_id=case_id),
                case_id,
            )
            svc.append_suggestion_service_log(db(), case_id, "api_v1_check", client["tenant_id"], payload.get("permit_type", ""), result.get("criteria_version", ""), endpoint="/api/v1/checks/application", request_id=payload.get("request_id", ""), status="ok", fallback_used=result.get("model_audit", {}).get("fallback_used", False))
            return result
        except HTTPException:
            raise
        except ValidationError as exc:
            return JSONResponse(status_code=422, content={"detail": exc.errors()})

    @router.post("/api/v1/structure/extract")
    async def api_extract(request: Request):
        try:
            client = security.require_api_client(request)
            payload = security.validate_client_payload(client, await json_payload(request))
            application_id = svc.application_id_for(str(payload.get("permit_type") or "building_residential"))
            validate_municipality_criteria(client, payload, application_id)
            case_id = _case_for_api_client(client)
            request_id = str(payload.get("request_id") or f"extract_{uuid.uuid4().hex[:8]}")
            job = svc.start_ai_job(db(), case_id, application_id, "structure_extraction", "Structure extraction API", ["structure_extractor"], eta_seconds=job_eta_seconds("field_suggestion"), tenant_id=client["tenant_id"])
            result = security.annotate_api_response(
                client,
                svc.structure_extract(
                    {
                        **payload,
                        "request_id": request_id,
                        "permit_type": application_id,
                        "form_schema": payload.get("form_schema") or svc.load_form_schema(application_id),
                    },
                    db=db(),
                    settings=settings(),
                    case_id=case_id,
                ),
            )
            completed = _complete_api_job(case_id, application_id, job, result)
            envelope = api_response(client, request_id, completed, result, case_id=case_id, application_id=application_id)
            svc.save_artifact(db(), case_id, "api_job_result", completed["id"], envelope)
            return envelope
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
