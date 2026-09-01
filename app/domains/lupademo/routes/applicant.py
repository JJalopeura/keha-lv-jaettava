from __future__ import annotations
import json
import io
import logging
import os
import time
import uuid
import zipfile
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

from .. import scenarios, services as svc
from ..services import customer_workspace as customer_ws
from .common import RouteContext, ai_mode_status, context_app_with_material, job_eta_seconds
from app.infra.llm_factory import create_llm_client

logger = logging.getLogger(__name__)


def _process_material_zip(
    db, settings, background_tasks, case_id: str, application_id: str, filename: str, content: bytes
) -> RedirectResponse:
    """Validate a zip upload, create the material package, start ingest, return the wizard redirect."""
    if not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=422, detail="Only .zip material packages are supported")
    try:
        svc.validate_upload_bytes(filename, content, allow_zip=True)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            svc.validate_zip_archive(archive)
            document_count = sum(1 for item in archive.infolist() if not item.is_dir())
    except zipfile.BadZipFile:
        raise HTTPException(status_code=422, detail="Invalid zip file")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    package = svc.create_pending_material_package(db(), case_id, application_id, filename, document_count)
    azure_enabled = str(getattr(settings(), "llm_provider", "mock") or "mock").lower() != "mock"
    job = svc.start_material_ingest_job(
        db(), case_id, application_id, document_count, azure_enabled=azure_enabled, package_id=package["package_id"]
    )
    background_tasks.add_task(
        svc.execute_material_ingest_job,
        db(),
        settings(),
        case_id,
        application_id,
        package["package_id"],
        job["id"],
        filename,
        zip_payload=(filename, content),
    )
    return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/2#ai-jobs", status_code=303)

def create_applicant_router(ctx: RouteContext) -> APIRouter:
    templates = ctx.templates
    db = ctx.db
    settings = ctx.settings

    def current_customer_id(request: Request) -> str:
        return str(request.scope.get("session", {}).get("customer_id") or "")

    def current_customer(request: Request) -> dict[str, Any] | None:
        customer_id = current_customer_id(request)
        return db().get_customer(customer_id) if customer_id else None

    def customer_guard(request: Request) -> None:
        path = request.url.path
        public_paths = {"/", "/login", "/logout"}
        public_prefixes = (
            "/api/",
            "/authority",
            "/admin",
            "/demo",
        )
        if path in public_paths or path.startswith(public_prefixes):
            return
        # F28: substring skips are GET-only — case-scoped /authority/ & /admin/
        # mutations must pass the ownership checks below.
        if request.method == "GET" and ("/authority/" in path or "/admin/" in path):
            return

        customer_id = current_customer_id(request)
        case_id = str(request.path_params.get("case_id") or request.path_params.get("project_id") or "")
        package_id = str(request.path_params.get("package_id") or "")
        if package_id and not case_id:
            try:
                found_package = customer_ws.find_package_case(db(), package_id)
                package_owner = str(
                    found_package.get("customer_id")
                    or (found_package.get("review") or {}).get("customer_id")
                    or ""
                )
                if package_owner:
                    if package_owner != customer_id:
                        raise HTTPException(status_code=403, detail="customer_package_forbidden")
                    return
                case_id = str(found_package["case_id"])
            except KeyError:
                # F25b: unknown package id must not fall through anonymously.
                if not customer_id:
                    raise HTTPException(status_code=404, detail="package_not_found")
                return
        if case_id:
            case = db().get_case(case_id)
            review = db().get_package_review(case_id)
            # F25b: a truly nonexistent id must never fall through to routes
            # that auto-create artifacts. Existing-but-unowned demo-seeded
            # cases keep the legacy allow (ponytail: tighten when real auth lands).
            if case is None and review is None:
                raise HTTPException(status_code=404, detail="case_not_found")
            owner_id = str((case or {}).get("customer_id") or (review or {}).get("customer_id") or "")
            if owner_id:
                if owner_id != customer_id:
                    raise HTTPException(status_code=403, detail="customer_case_forbidden")
                return
            if not customer_id:
                return
            raise HTTPException(status_code=403, detail="unowned_case_forbidden")
        if not customer_id:
            raise HTTPException(status_code=303, headers={"Location": "/login"})

    router = APIRouter(dependencies=[Depends(customer_guard)])

    def ai_execution_label(case_id: str) -> str:
        configured = settings()
        force_mock = os.getenv("KEHA_LV_FORCE_MOCK_LLM", "").strip().lower() in {"1", "true", "yes", "on"}
        project = svc.artifact_json(db(), case_id, "lv_project", "main", {})
        if project.get("presenter_mode") or force_mock:
            return "AI-simulaatio"
        if str(configured.llm_provider or "mock").strip().lower() == "azure":
            return "Azure OpenAI ja sääntöpohjaiset tarkistukset"
        return "Azure OpenAI ei ole käytettävissä"

    def material_return_url(case_id: str, application_id: str, package_id: str = "", fragment: str = "") -> str:
        if package_id:
            try:
                found = svc.find_material_package(db(), package_id)
                if found["case_id"] == case_id and found["permit_type"] == svc.application_id_for(application_id):
                    return f"/applicant/packages/{package_id}{fragment}"
            except KeyError:
                pass
        return f"/cases/{case_id}/lupademo/application/{application_id}/wizard/2{fragment}"

    def context(case_id: str, permit_type: Optional[str] = None) -> dict[str, Any]:
        return ctx.context(case_id, permit_type, app_ctx_builder=context_app_with_material)

    def hanke_context(request: Request, case_id: str) -> dict[str, Any]:
        page_context = context(case_id)
        applications = customer_ws.case_application_rows(db(), case_id, page_context.get("project", {}))
        page_context.update(
            {
                "customer": current_customer(request),
                "hanke_applications": applications,
            }
        )
        return page_context

    @router.get("/login", response_class=HTMLResponse)
    def login(request: Request):
        customer_ws.ensure_customer_demo_data(db())
        if current_customer(request):
            return RedirectResponse(url="/", status_code=303)
        return templates.TemplateResponse(request, "applicant/login.html", {"customers": customer_ws.customer_fixtures()})

    @router.post("/login")
    def login_post(request: Request, customer_id: str = Form(...)):
        customer_ws.ensure_customer_demo_data(db())
        customer = db().get_customer(customer_id)
        if not customer:
            raise HTTPException(status_code=404, detail="customer_not_found")
        request.session.clear()
        request.session["customer_id"] = customer["id"]
        request.session["customer_name"] = customer["display_name"]
        return RedirectResponse(url="/", status_code=303)

    @router.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse(url="/login", status_code=303)

    @router.get("/", response_class=HTMLResponse)
    def landing(request: Request):
        customer = current_customer(request)
        if not customer:
            return RedirectResponse(url="/login", status_code=303)
        customer_ws.ensure_customer_demo_data(db())
        return templates.TemplateResponse(request, "applicant/landing.html", customer_ws.customer_dashboard(db(), customer["id"]))

    @router.get("/demo/scenarios", response_class=HTMLResponse)
    def scenario_list(request: Request):
        return templates.TemplateResponse(request, "admin/scenarios.html", {"scenarios": scenarios.list_scenarios()})

    @router.get("/demo-lab/presenter", response_class=HTMLResponse)
    def presenter_lab(request: Request):
        return templates.TemplateResponse(request, "admin/presenter.html", {"scenarios": scenarios.list_scenarios()})

    @router.get("/applicant", response_class=HTMLResponse)
    def applicant_start(request: Request):
        return RedirectResponse(url="/", status_code=303)

    @router.get("/applicant/packages/new", response_class=HTMLResponse)
    def applicant_package_new(request: Request):
        customer = current_customer(request)
        return templates.TemplateResponse(
            request,
            "applicant/package_new.html",
            {
                "customer": customer,
                "municipalities": svc.list_municipalities(db()),
            },
        )

    @router.post("/applicant/packages/new")
    async def applicant_package_create(
        request: Request,
        background_tasks: BackgroundTasks,
        package_name: str = Form("Hakemusaineisto"),
        permit_type: str = Form("environmental_industrial"),
        municipality: str = Form(""),
        project_name: str = Form(""),
        applicant_name: str = Form(""),
        business_id: str = Form(""),
        contact_person: str = Form(""),
        phone: str = Form(""),
        email: str = Form(""),
        address: str = Form(""),
        property_id: str = Form(""),
        files: list[UploadFile] = File(default=[]),
        zip_file: Optional[UploadFile] = File(default=None),
        text_body: str = Form(""),
    ):
        application_id = svc.application_id_for(permit_type)
        if application_id not in svc.PERMIT_SCHEMAS:
            raise HTTPException(status_code=422, detail="unknown_permit_type")
        municipality = municipality.strip()
        if not municipality:
            raise HTTPException(status_code=422, detail="municipality_required")
        zip_payload: Optional[tuple[str, bytes]] = None
        document_count = 0
        if zip_file is not None and zip_file.filename:
            filename = zip_file.filename or "aineisto.zip"
            if not filename.lower().endswith(".zip"):
                raise HTTPException(status_code=422, detail="Only .zip material packages are supported")
            content = await zip_file.read()
            try:
                svc.validate_upload_bytes(filename, content, allow_zip=True)
                with zipfile.ZipFile(io.BytesIO(content)) as zf:
                    svc.validate_zip_archive(zf)
                    document_count += sum(1 for item in zf.infolist() if not item.is_dir() and not item.filename.startswith("."))
            except zipfile.BadZipFile:
                raise HTTPException(status_code=422, detail="Invalid zip file")
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            zip_payload = (filename, content)
        upload_payload: list[tuple[str, bytes]] = []
        for item in files or []:
            if item.filename:
                content = await item.read()
                try:
                    svc.validate_upload_bytes(item.filename, content)
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc))
                upload_payload.append((item.filename, content))
        document_count += len(upload_payload)
        if text_body.strip():
            document_count += 1
        if not zip_payload and not upload_payload and not text_body.strip():
            raise HTTPException(status_code=422, detail="package_material_required")

        customer = current_customer(request)
        if not customer:
            return RedirectResponse(url="/login", status_code=303)
        # Kevytasiointi (Polku 2): the LV-side form collects the same shared fields
        # as the wizard plus the hanke's tausta-aineisto. Submitting creates a REAL
        # hanke (created_from="kevytasiointi") — no standalone package_review session.
        submitted_profile = {
            "applicant_name": applicant_name,
            "business_id": business_id,
            "contact_person": contact_person,
            "phone": phone,
            "email": email,
            "municipality": municipality,
            "address": address,
            "property_id": property_id,
        }
        project_profile = dict(customer.get("profile") or {})
        project_profile.update(
            {key: str(value).strip() for key, value in submitted_profile.items() if value is not None and str(value).strip()}
        )
        display_name = project_name.strip() or package_name.strip() or "Kevytasiointi"
        started = scenarios.create_generic_project(
            db(),
            project_name=display_name,
            permit_type=application_id,
            applicant_name=applicant_name,
            municipality=municipality,
            address=address,
            customer_id=customer["id"],
            customer_profile=project_profile,
            created_from="kevytasiointi",
        )
        case_id = started["case_id"]
        permit_type = started["permit_type"]
        svc.event(db(), case_id, "kevytasiointi.started", "Kevytasiointi started from LV", {"permit_type": permit_type})
        package = svc.create_pending_material_package(db(), case_id, permit_type, display_name, document_count)
        azure_enabled = str(getattr(settings(), "llm_provider", "mock") or "mock").lower() != "mock"
        job = svc.start_material_ingest_job(
            db(), case_id, permit_type, document_count, azure_enabled=azure_enabled, package_id=package["package_id"]
        )
        route_db = db()
        route_settings = settings()

        def _ingest_then_analyze() -> None:
            # Kevytasiointi: intake analysis runs automatically after the material
            # ingest finishes — there is no manual "Analysoi aineisto" step. The
            # wizard path (Path B) keeps its explicit analyze button.
            svc.execute_material_ingest_job(
                route_db,
                route_settings,
                case_id,
                permit_type,
                package["package_id"],
                job["id"],
                display_name,
                zip_payload=zip_payload,
                files_payload=upload_payload,
                text_body=text_body,
            )
            stored = (svc.find_material_package(route_db, package["package_id"]) or {}).get("package") or {}
            if str(stored.get("status") or "") != "ready":
                return
            analysis_job = svc.start_intake_analysis_job(route_db, case_id, permit_type, route_settings, managed_background=True)
            svc.execute_intake_analysis_job(route_db, case_id, permit_type, analysis_job["id"], route_settings)

        background_tasks.add_task(_ingest_then_analyze)
        return RedirectResponse(url=f"/applicant/packages/{package['package_id']}", status_code=303)

    def standalone_package_context(review_id: str, permit_type: str, review: dict[str, Any]) -> dict[str, Any]:
        municipality = str(review.get("municipality") or "")
        package = svc.current_material_package(db(), review_id, permit_type)
        package_analysis = svc.artifact_json(db(), review_id, "package_analysis_result", permit_type, {})
        criteria = svc.effective_criteria(db(), review_id, permit_type, municipality)
        field_extraction_queue = svc.artifact_json(db(), review_id, "field_extraction_queue", permit_type, {"items": []})
        ai_jobs = svc.list_ai_jobs(db(), review_id, permit_type)
        app = {
            "application_id": permit_type,
            "permit_type": permit_type,
            "status": "review_only",
            "form_data": {"municipality": municipality},
            "field_sources": {},
        }
        return {
            "case_id": review_id,
            "case": None,
            "project": {"name": review.get("display_name") or "Hakemuspaketin tarkistus", "presenter_mode": False, "permits": [permit_type]},
            "common": {"data": {"municipality": municipality}},
            "sync": {"changes": []},
            "ai_mode_status": ai_mode_status({}, settings()),
            "application": app,
            "schema": svc.load_form_schema(permit_type),
            "criteria": criteria,
            "criteria_municipality": str(criteria.get("municipality") or municipality or "Kansallinen oletus"),
            "criteria_version": str(criteria.get("version") or "demo"),
            "attachments": svc.list_attachments(db(), review_id, permit_type),
            "suggestions": [],
            "inline_field_suggestions": [],
            "check_report": {},
            "precheck_gate": {},
            "package": {},
            "receipt": {},
            "wizard": {
                "missing_attachments": package_analysis.get("missing_attachments", []),
                "steps": [
                    {"step": 1, "label": "Yhteiset tiedot"},
                    {"step": 2, "label": "Aineisto"},
                    {"step": 3, "label": "Hakemuksen tiedot"},
                    {"step": 4, "label": "Tarkistus"},
                    {"step": 5, "label": "Lähetys"},
                    {"step": 6, "label": "Vahvistus"},
                ],
            },
            "structure_extraction": svc.artifact_json(db(), review_id, "structure_extraction_result", permit_type, {}),
            "intake_analysis": svc.artifact_json(db(), review_id, "intake_analysis", permit_type, {}),
            "material_package": package,
            "package_analysis": package_analysis,
            "package_analysis_stale": False,
            "field_extraction_queue": field_extraction_queue,
            "ai_jobs": ai_jobs,
            "contextual_chat": {"messages": []},
            "voice_state": svc.voice_feature_state(settings()),
            "voice_chat": {"turns": []},
            "environmental_story": {},
            "is_package_review": True,
        }

    def package_context(package_id: str) -> dict[str, Any]:
        found = svc.find_material_package(db(), package_id)
        case_id = found["case_id"]
        permit_type = found["permit_type"]
        if found.get("review"):
            out = standalone_package_context(case_id, permit_type, found["review"])
        else:
            out = context(case_id, permit_type)
        attachments = svc.list_attachments(db(), case_id, permit_type)
        ai_jobs = svc.list_ai_jobs(db(), case_id, permit_type)
        active_job = next(
            (
                job
                for job in ai_jobs
                if job.get("kind") in {"material_ingest", "intake_analysis"}
                and job.get("status") in {"pending", "running"}
                and (job.get("kind") != "material_ingest" or job.get("package_id") == package_id)
            ),
            None,
        )
        active_job_view = (
            {**active_job, **svc.ai_job_status_payload(active_job, case_id=case_id, application_id=permit_type)}
            if active_job
            else None
        )
        material_package = svc.find_material_package(db(), package_id)["package"]
        analysis_jobs = [job for job in ai_jobs if job.get("kind") == "intake_analysis"]
        failed_analysis_job = next(
            (job for job in reversed(analysis_jobs) if job.get("status") == "failed"),
            None,
        )
        out.update(
            {
                "is_package_review": bool(found.get("review")),
                "service_mode": str((out.get("project") or {}).get("service_mode") or "kevytasiointi"),
                "package_id": package_id,
                "material_package": material_package,
                "attachments": attachments,
                "official_attachments": [item for item in attachments if item.get("delivery_scope") == "official_submission"],
                "context_attachments": [item for item in attachments if item.get("delivery_scope") == "ai_context_only"],
                "material_intake": svc.artifact_json(db(), case_id, "material_intake", permit_type, {}),
                "ai_jobs": ai_jobs,
                "active_job": active_job_view,
                "failed_analysis_job": failed_analysis_job,
                "package_analysis": svc.artifact_json(db(), case_id, "package_analysis_result", permit_type, {}),
                "prose_suggestion": svc.artifact_json(db(), case_id, "prose_suggestion", permit_type, {}),
                "submitted": bool((out.get("application") or {}).get("status") == "submitted"),
                "receipt": svc.artifact_json(db(), case_id, "receipt", permit_type, {}),
                "can_start_analysis": material_package.get("status") == "ready" and active_job is None,
                "ai_execution_label": ai_execution_label(case_id),
                "kevyt_permit_label": customer_ws.PERMIT_LABELS.get(permit_type, permit_type),
                "kevyt_municipality": str(
                    (out.get("common") or {}).get("data", {}).get("municipality")
                    or material_package.get("municipality")
                    or ""
                ),
            }
        )
        return out

    @router.get("/applicant/packages/{package_id}", response_class=HTMLResponse)
    def kevytasiointi_workbench(request: Request, package_id: str):
        try:
            return templates.TemplateResponse(request, "applicant/kevytasiointi.html", package_context(package_id))
        except KeyError:
            raise HTTPException(status_code=404, detail="Package not found")

    @router.get("/applicant/packages/{package_id}/suggestions", response_class=HTMLResponse)
    def kevytasiointi_suggestions(request: Request, package_id: str):
        try:
            return templates.TemplateResponse(
                request,
                "applicant/kevyt_suggestions.html",
                {**package_context(package_id), "standalone_page": True},
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="Package not found")

    @router.post("/applicant/packages/{package_id}/analyze")
    def applicant_package_analyze(package_id: str, background_tasks: BackgroundTasks):
        try:
            found = svc.find_material_package(db(), package_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Package not found")
        if found["package"].get("status") != "ready":
            raise HTTPException(status_code=409, detail="material_package_not_ready")
        if any(
            job.get("kind") == "intake_analysis" and job.get("status") in {"pending", "running"}
            for job in svc.list_ai_jobs(db(), found["case_id"], found["permit_type"])
        ):
            raise HTTPException(status_code=409, detail="analysis_already_running")
        job = svc.start_intake_analysis_job(
            db(),
            found["case_id"],
            found["permit_type"],
            settings=settings(),
            managed_background=True,
            attachment_only=True,
            review_only=bool(found.get("review")),
        )
        background_tasks.add_task(
            svc.execute_intake_analysis_job,
            db(),
            found["case_id"],
            found["permit_type"],
            job["id"],
            settings(),
            attachment_only=True,
            review_only=bool(found.get("review")),
        )
        return RedirectResponse(url=f"/applicant/packages/{package_id}#analysis", status_code=303)

    @router.post("/applicant/packages/{package_id}/ai-prose")
    def kevytasiointi_ai_prose(package_id: str):
        """AI-apuri (Kevytasiointipalvelu): LV returns a prose-form suggestion modal payload."""
        try:
            found = svc.find_material_package(db(), package_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Package not found")
        # Prose suggestions need the intake analysis; without it the LLM has no
        # retrieval context and returns junk. Analysis runs automatically after
        # ingest, so this only triggers if it failed or is still running.
        analysis = svc.artifact_json(db(), found["case_id"], "intake_analysis", found["permit_type"], {})
        if not analysis:
            raise HTTPException(status_code=409, detail="analysis_not_ready")
        return JSONResponse(svc.kevytasiointi_prose(db(), found["case_id"], found["permit_type"], settings()))

    @router.post("/applicant/packages/{package_id}/save")
    async def kevytasiointi_save(package_id: str, request: Request):
        """Save the Kevytasiointipalvelu form values (no review, no submit)."""
        try:
            found = svc.find_material_package(db(), package_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Package not found")
        form = await request.form()
        values = {str(k): str(v) for k, v in form.items() if k not in {"redirect_to", "submit"}}
        svc.update_application_data(db(), found["case_id"], found["permit_type"], values)
        return RedirectResponse(url=f"/applicant/packages/{package_id}?saved=1", status_code=303)

    @router.post("/applicant/packages/{package_id}/submit")
    async def kevytasiointi_submit(package_id: str, request: Request):
        """Submit the Kevytasiointipalvelu application straight to the authority (no review phase)."""
        try:
            found = svc.find_material_package(db(), package_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Package not found")
        form = await request.form()
        values = {str(k): str(v) for k, v in form.items() if k not in {"redirect_to", "submit"}}
        if values:
            svc.update_application_data(db(), found["case_id"], found["permit_type"], values)
        receipt = svc.submit_application(
            db(), found["case_id"], found["permit_type"], settings(), require_precheck=False
        )
        return RedirectResponse(url=f"/applicant/packages/{package_id}?submitted=1#receipt", status_code=303)

    @router.get("/projects/new", response_class=HTMLResponse)
    def project_new(request: Request):
        return templates.TemplateResponse(request, "applicant/applicant_start.html", {"customer": current_customer(request)})

    @router.post("/demo/scenarios/start")
    def scenario_start(scenario_id: str = Form(...)):
        try:
            started = scenarios.create_case_from_scenario(db(), scenario_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Scenario not found")
        return RedirectResponse(url=started["next_url"], status_code=303)

    @router.post("/demo/reset")
    def demo_reset():
        started = scenarios.create_case_from_scenario(db(), "building_vantaa_residential")
        return RedirectResponse(url=started["next_url"], status_code=303)

    @router.post("/projects/new")
    def create_project(
        request: Request,
        project_name: str = Form("Uusi lupahanke"),
        permit_type: str = Form("building_residential"),
        applicant_name: Optional[str] = Form(None),
        business_id: Optional[str] = Form(None),
        contact_person: Optional[str] = Form(None),
        phone: Optional[str] = Form(None),
        email: Optional[str] = Form(None),
        municipality: Optional[str] = Form(None),
        address: Optional[str] = Form(None),
        property_id: Optional[str] = Form(None),
        tenant_id: str = Form(""),
        applicant_role: str = Form("company"),
        start_at: str = Form("lv_project"),
    ):
        customer = current_customer(request)
        if not customer:
            return RedirectResponse(url="/login", status_code=303)
        project_profile = dict(customer.get("profile") or {})
        submitted_profile = {
            "applicant_name": applicant_name,
            "business_id": business_id,
            "contact_person": contact_person,
            "phone": phone,
            "email": email,
            "municipality": municipality,
            "address": address,
            "property_id": property_id,
        }
        project_profile.update(
            {key: str(value).strip() for key, value in submitted_profile.items() if value is not None}
        )
        started = scenarios.create_generic_project(
            db(),
            project_name=project_name,
            permit_type=permit_type,
            applicant_name=str(applicant_name or "").strip(),
            municipality=str(municipality or "").strip(),
            address=str(address or "").strip(),
            tenant_id=tenant_id,
            customer_id=customer["id"],
            customer_profile=project_profile,
            applicant_role=applicant_role,
            created_from="lv",
        )
        if start_at == "package_review":
            application_id = svc.application_id_for(started["permit_type"])
            return RedirectResponse(url=f"/cases/{started['case_id']}/lupademo/application/{application_id}/wizard/2#materials", status_code=303)
        if start_at == "wizard":
            application_id = svc.application_id_for(started["permit_type"])
            return RedirectResponse(url=f"/cases/{started['case_id']}/lupademo/application/{application_id}/wizard/1", status_code=303)
        return RedirectResponse(url=started["next_url"], status_code=303)

    @router.get("/projects/{project_id}", response_class=HTMLResponse)
    def project_workspace(request: Request, project_id: str):
        return templates.TemplateResponse(request, "applicant/lv_project.html", hanke_context(request, project_id))

    @router.post("/projects/{project_id}/applications")
    def create_application(project_id: str, permit_type: str = Form(...)):
        permit_type = svc.application_id_for(permit_type)
        if permit_type not in svc.PERMIT_SCHEMAS:
            raise HTTPException(status_code=400, detail="unknown_permit_type")
        project = svc.artifact_json(db(), project_id, "lv_project", "main", {})
        permits = project.setdefault("permits", [])
        if permit_type not in permits:
            permits.append(permit_type)
        applications = project.setdefault("applications", [])
        if permit_type not in applications:
            applications.append(permit_type)
        project.setdefault("statuses", {})[permit_type] = "draft"
        project["updated_at"] = time.time()
        svc.save_artifact(db(), project_id, "lv_project", "main", project)
        svc.ensure_application(db(), project_id, permit_type)
        svc.event(db(), project_id, "application.created", "Application created", {"permit_type": permit_type})
        return RedirectResponse(url=f"/cases/{project_id}/lupademo/application/{permit_type}/wizard/1", status_code=303)

    @router.get("/projects/{project_id}/intake", response_class=HTMLResponse)
    def project_intake(project_id: str, application_id: str = "building_residential"):
        return RedirectResponse(url=f"/cases/{project_id}/lupademo/application/{svc.application_id_for(application_id)}/wizard/2#materials", status_code=303)

    @router.post("/projects/{project_id}/intake")
    async def project_intake_post(project_id: str, zip_file: Optional[UploadFile] = File(None), application_id: str = Form("building_residential")):
        if zip_file is not None:
            filename = zip_file.filename or "aineisto.zip"
            content = await zip_file.read()
            if content:
                try:
                    svc.ingest_application_zip(db(), settings(), project_id, application_id, filename, content)
                except zipfile.BadZipFile:
                    raise HTTPException(status_code=422, detail="Invalid zip file")
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc))
        run_id = f"run_{uuid.uuid4().hex[:8]}"
        return RedirectResponse(url=f"/projects/{project_id}/intake/{run_id}", status_code=303)

    @router.get("/projects/{project_id}/intake/{run_id}", response_class=HTMLResponse)
    def project_intake_result(project_id: str, run_id: str, application_id: str = "building_residential"):
        return RedirectResponse(url=f"/cases/{project_id}/lupademo/application/{svc.application_id_for(application_id)}/wizard/2#intake-analysis", status_code=303)

    @router.get("/projects/{project_id}/applications/{application_id}/studio", response_class=HTMLResponse)
    def application_studio(project_id: str, application_id: str):
        return RedirectResponse(url=f"/cases/{project_id}/lupademo/application/{svc.application_id_for(application_id)}/wizard/1", status_code=303)

    @router.get("/demo/presenter", response_class=HTMLResponse)
    def presenter():
        return RedirectResponse(url="/demo-lab/presenter", status_code=303)

    @router.get("/lv", response_class=HTMLResponse)
    def lv(request: Request):
        return RedirectResponse(url="/", status_code=303)

    @router.get("/lv/projects/new", response_class=HTMLResponse)
    def lv_project_new():
        return RedirectResponse(url="/projects/new", status_code=303)

    @router.get("/lv/projects/{case_id}", response_class=HTMLResponse)
    def lv_project(request: Request, case_id: str):
        return templates.TemplateResponse(request, "applicant/lv_project.html", hanke_context(request, case_id))

    @router.get("/customer/profile", response_class=HTMLResponse)
    def customer_profile(request: Request):
        return templates.TemplateResponse(request, "applicant/customer_profile.html", {"customer": current_customer(request)})

    @router.post("/customer/profile")
    async def update_customer_profile(request: Request):
        customer = current_customer(request)
        if not customer:
            return RedirectResponse(url="/login", status_code=303)
        form = await request.form()
        profile = dict(customer.get("profile") or {})
        profile_fields = {"applicant_name", "business_id", "contact_person", "phone", "email", "municipality", "address"}
        profile.update({key: str(form.get(key) or "").strip() for key in profile_fields})
        display_name = str(profile.get("applicant_name") or "").strip() or customer["display_name"]
        db().upsert_customer(customer["id"], display_name, profile)
        request.session["customer_name"] = display_name
        return RedirectResponse(url="/customer/profile?saved=1", status_code=303)

    @router.post("/lv/projects/{case_id}/common-data")
    async def update_lv(case_id: str, request: Request):
        form = await request.form()
        svc.update_lv_common_data(db(), case_id, {k: str(v) for k, v in form.items() if k in svc.COMMON_FIELDS})
        return RedirectResponse(url=f"/lv/projects/{case_id}", status_code=303)

    @router.post("/lv/projects/{case_id}/sync")
    def accept_sync(
        case_id: str,
        field_id: str = Form(...),
        direction: str = Form(...),
        source_application_id: str = Form(""),
        resolution: str = Form(""),
        manual_value: str = Form(""),
    ):
        svc.accept_common_data_sync(db(), case_id, field_id, direction, source_application_id=source_application_id, resolution=resolution, manual_value=manual_value)
        return RedirectResponse(url=f"/lv/projects/{case_id}", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/common-data/field-sync")
    async def field_common_data_sync(case_id: str, application_id: str, request: Request):
        """Field-level shared-data dialog (wayfinder T3/T4): resolve a common-data
        discrepancy from inside the wizard or the project page.

        Actions:
        - keep: "eroavaisuus on ok" — resolve the proposal without copying values
        - pull: take LV's shared value into this application's field
        - push: push this application's field value into LV shared data
        """
        form = await request.form()
        action = str(form.get("action") or "")
        field_id = str(form.get("field_id") or "")
        if not field_id:
            raise HTTPException(status_code=422, detail="field_id_required")
        permit_type = svc.application_id_for(application_id)
        app = svc.ensure_application(db(), case_id, permit_type)
        common = svc.artifact_json(db(), case_id, "lv_common_data", "main", {"data": svc.DEFAULT_COMMON_DATA.copy()})
        sync = svc.detect_common_data_changes(db(), case_id)
        change = next((c for c in sync.get("changes", []) if c.get("field_id") == field_id), None)
        direction = str((change or {}).get("direction") or "")
        if action == "keep":
            svc.accept_common_data_sync(db(), case_id, field_id, direction or "app_to_lv", source_application_id=permit_type, resolution="keep_separate")
        elif action == "pull":
            svc.accept_common_data_sync(db(), case_id, field_id, "lv_to_app", source_application_id=permit_type, resolution="lv_wins")
        elif action == "push":
            field_value = str(app.get("form_data", {}).get(field_id, "") or "")
            svc.update_lv_common_data(db(), case_id, {field_id: field_value})
            svc.accept_common_data_sync(db(), case_id, field_id, "app_to_lv", source_application_id=permit_type, resolution="app_wins")
        else:
            raise HTTPException(status_code=422, detail="unknown_action")
        redirect_to = str(form.get("redirect_to") or "")
        if redirect_to.startswith("/"):
            return RedirectResponse(url=redirect_to, status_code=303)
        return RedirectResponse(url=f"/lv/projects/{case_id}", status_code=303)

    @router.get("/api/lv/projects/{case_id}/common-data")
    def common_data(case_id: str):
        svc.ensure_seed_state(db(), case_id)
        return svc.artifact_json(db(), case_id, "lv_common_data", "main", {"data": svc.DEFAULT_COMMON_DATA.copy()})

    @router.post("/api/lv/events")
    async def lv_events(request: Request):
        payload = await request.json()
        case_id = payload.get("case_id") or svc.ensure_demo_case(db())
        svc.event(db(), case_id, "lv.event.received", "LV event received", payload)
        return {"ok": True}

    @router.get("/cases/{case_id}/lupademo/start")
    def start(case_id: str, permit_type: str = "building_residential"):
        svc.ensure_seed_state(db(), case_id)
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{svc.application_id_for(permit_type)}", status_code=303)

    @router.get("/cases/{case_id}/lupademo/application/{application_id}", response_class=HTMLResponse)
    def application(request: Request, case_id: str, application_id: str):
        suffix = "?present=1" if request.query_params.get("present") == "1" else ""
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{svc.application_id_for(application_id)}/wizard/1{suffix}", status_code=303)

    @router.get("/cases/{case_id}/lupademo/application/{application_id}/wizard/{step}", response_class=HTMLResponse)
    def application_wizard(request: Request, case_id: str, application_id: str, step: int):
        step = min(max(int(step), 1), 6)
        ctx = context(case_id, application_id)
        # Step 5/6 shows the submission package preview. Compose it on demand if
        # it doesn't exist yet and there are attachments to package, so the
        # "Asiakirjaesitys" document list renders. Leave the empty state when
        # there is nothing to package yet.
        if step in (5, 6):
            attachments = svc.list_attachments(db(), case_id, application_id)
            if attachments and svc.submission_package_is_stale(
                ctx.get("package", {}),
                ctx.get("application", {}),
                attachments,
            ):
                ctx["package"] = svc.compose_submission_package(db(), case_id, application_id)
        return templates.TemplateResponse(request, "applicant/wizard.html", {**ctx, "step": step})

    def _case_scoped_job(case_id: str, application_id: str, job_id: str) -> dict[str, Any]:
        """Resolve a job that belongs to this case/application (browser session, no API key)."""
        permit_type = svc.application_id_for(application_id)
        job = next(
            (
                item
                for item in svc.list_ai_jobs(db(), case_id, permit_type)
                if item.get("id") == job_id
            ),
            None,
        )
        if job is None:
            raise HTTPException(status_code=404, detail="job_not_found")
        return job

    @router.get("/cases/{case_id}/lupademo/application/{application_id}/jobs/{job_id}")
    def application_job_status(case_id: str, application_id: str, job_id: str):
        job = _case_scoped_job(case_id, application_id, job_id)
        return svc.ai_job_status_payload(job, case_id=case_id, application_id=svc.application_id_for(application_id))

    @router.get("/cases/{case_id}/lupademo/application/{application_id}/jobs/{job_id}/stream")
    def application_job_stream(case_id: str, application_id: str, job_id: str):
        job = _case_scoped_job(case_id, application_id, job_id)
        payload = svc.ai_job_status_payload(job, case_id=case_id, application_id=svc.application_id_for(application_id))
        return Response("event: status\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n", media_type="text/event-stream")

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/jobs/{job_id}/cancel")
    def application_job_cancel(case_id: str, application_id: str, job_id: str, step: int = Form(1)):
        job = _case_scoped_job(case_id, application_id, job_id)
        if job.get("status") not in {"pending", "running"}:
            raise HTTPException(status_code=409, detail="job_not_cancellable")
        try:
            svc.cancel_ai_job(db(), job_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail="job_not_cancellable") from exc
        safe_step = min(max(int(step), 1), 6)
        return RedirectResponse(
            url=f"/cases/{case_id}/lupademo/application/{svc.application_id_for(application_id)}/wizard/{safe_step}#ai-jobs",
            status_code=303,
        )

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/materials/package")
    def create_application_material_package(case_id: str, application_id: str):
        permit_type = svc.application_id_for(application_id)
        svc.ensure_application(db(), case_id, permit_type)
        package = svc.current_material_package(db(), case_id, permit_type)
        if not package.get("package_id"):
            package = svc.create_pending_material_package(db(), case_id, permit_type, "Hakemusaineisto")
        return RedirectResponse(url=f"/applicant/packages/{package['package_id']}", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/save")
    async def save_application(case_id: str, application_id: str, request: Request):
        form = await request.form()
        redirect_to = str(form.get("redirect_to") or f"/cases/{case_id}/lupademo/application/{application_id}/wizard/2")
        svc.update_application_data(db(), case_id, application_id, {k: str(v) for k, v in form.items() if k != "redirect_to"})
        return RedirectResponse(url=redirect_to if redirect_to.startswith(f"/cases/{case_id}/") else f"/cases/{case_id}/lupademo/application/{application_id}/wizard/2", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/attachments/upload")
    async def upload_attachment(
        case_id: str,
        application_id: str,
        delivery_scope: str = Form("official_submission"),
        return_package_id: str = Form(""),
        file: UploadFile = File(...),
    ):
        content = await file.read()
        filename = file.filename or "liite"
        try:
            svc.validate_upload_bytes(filename, content)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        local_path = svc.save_upload_file(settings(), case_id, filename, content)
        excerpt = svc.extract_text_from_path(local_path, filename)
        svc.add_attachment(db(), case_id, application_id, filename, len(content), delivery_scope, excerpt, local_path=local_path, settings=settings())
        return RedirectResponse(url=material_return_url(case_id, application_id, return_package_id), status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/materials/upload-zip")
    async def upload_material_zip(
        case_id: str,
        application_id: str,
        background_tasks: BackgroundTasks,
        zip_file: UploadFile = File(...),
    ):
        filename = zip_file.filename or "aineisto.zip"
        content = await zip_file.read()
        return _process_material_zip(db, settings, background_tasks, case_id, application_id, filename, content)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/materials/upload-files")
    async def upload_material_files(
        case_id: str,
        application_id: str,
        background_tasks: BackgroundTasks,
        files: list[UploadFile] = File(default=[]),
        zip_file: UploadFile | None = File(default=None),
        package_name: str = Form(""),
        text_body: str = Form(""),
        return_package_id: str = Form(""),
    ):
        # --- ZIP path ---
        if zip_file and zip_file.filename:
            return _process_material_zip(
                db, settings, background_tasks, case_id, application_id, zip_file.filename or "aineisto.zip", await zip_file.read()
            )

        # --- Multi-file path ---
        payload: list[tuple[str, bytes]] = []
        for item in files:
            if not item.filename:
                continue
            payload.append((item.filename, await item.read()))

        # --- Text-only path ---
        if not payload and text_body.strip():
            try:
                svc.ingest_application_text(db(), settings(), case_id, application_id, "Hakijan kuvaus", text_body.strip(), delivery_scope="ai_context_only")
            except ValueError:
                raise HTTPException(status_code=422, detail="Text material is empty")
            return RedirectResponse(url=material_return_url(case_id, application_id, return_package_id, "#documents"), status_code=303)

        if not payload:
            raise HTTPException(status_code=422, detail="No files provided")

        try:
            result = svc.ingest_application_files(db(), settings(), case_id, application_id, payload, display_name=package_name)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        document_count = result.get("added_count", len(payload))
        azure_enabled = str(getattr(settings(), "llm_provider", "mock") or "mock").lower() != "mock"
        job = svc.start_material_ingest_job(
            db(), case_id, application_id, document_count, azure_enabled=azure_enabled, package_id=result.get("material_package", {}).get("package_id", "")
        )
        background_tasks.add_task(
            svc.execute_material_ingest_job,
            db(),
            settings(),
            case_id,
            application_id,
            result.get("material_package", {}).get("package_id", ""),
            job["id"],
            package_name or "Hakemusaineisto",
            zip_payload=None,
        )
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/2#ai-jobs", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/materials/add-text")
    def add_material_text(
        case_id: str,
        application_id: str,
        text_title: str = Form(""),
        text_body: str = Form(...),
        delivery_scope: str = Form("ai_context_only"),
        return_package_id: str = Form(""),
    ):
        try:
            svc.ingest_application_text(db(), settings(), case_id, application_id, text_title, text_body, delivery_scope=delivery_scope)
        except ValueError:
            raise HTTPException(status_code=422, detail="Text material is empty")
        return RedirectResponse(url=material_return_url(case_id, application_id, return_package_id, "#documents"), status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/attachments/{attachment_id}/scope")
    def change_attachment_scope(case_id: str, application_id: str, attachment_id: str, delivery_scope: str = Form(...)):
        svc.set_attachment_scope(db(), case_id, application_id, attachment_id, delivery_scope)
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/2", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/attachments/{attachment_id}/metadata")
    def update_attachment_metadata(case_id: str, application_id: str, attachment_id: str, primary_type: str = Form(""), delivery_scope: str = Form(""), return_package_id: str = Form("")):
        try:
            svc.update_attachment_metadata(db(), case_id, application_id, attachment_id, primary_type=primary_type, delivery_scope=delivery_scope)
        except KeyError:
            raise HTTPException(status_code=404, detail="Attachment not found")
        return RedirectResponse(url=material_return_url(case_id, application_id, return_package_id, "#documents"), status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/attachments/{attachment_id}/delete")
    def delete_attachment(case_id: str, application_id: str, attachment_id: str, return_package_id: str = Form("")):
        svc.delete_attachment(db(), case_id, application_id, attachment_id)
        return RedirectResponse(url=material_return_url(case_id, application_id, return_package_id, "#documents"), status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/ai/intake-analysis")
    def intake_analysis(case_id: str, application_id: str, background_tasks: BackgroundTasks):
        if any(
            job.get("kind") == "intake_analysis" and job.get("status") in {"pending", "running"}
            for job in svc.list_ai_jobs(db(), case_id, application_id)
        ):
            raise HTTPException(status_code=409, detail="analysis_already_running")
        job = svc.start_intake_analysis_job(
            db(), case_id, application_id, settings=settings(), managed_background=True, full_check=False,
        )
        background_tasks.add_task(
            svc.execute_intake_analysis_job,
            db(),
            case_id,
            application_id,
            job["id"],
            settings(),
            full_check=False,
        )
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/2#ai-jobs", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/extractions/accept")
    def accept_extraction(case_id: str, application_id: str, field_id: str = Form(...), edited_value: str = Form(""), redirect_to: str = Form("")):
        try:
            svc.accept_extraction_queue_item(db(), case_id, application_id, field_id, edited_value)
        except KeyError:
            raise HTTPException(status_code=404, detail="Extraction not found")
        except ValueError:
            raise HTTPException(status_code=400, detail="Extraction value is empty")
        default_url = f"/cases/{case_id}/lupademo/application/{application_id}/wizard/2#package-analysis-result"
        wizard_prefix = f"/cases/{case_id}/lupademo/application/{application_id}/wizard/"
        workbench_target = ""
        if redirect_to.startswith("/applicant/packages/"):
            package_id = redirect_to.removeprefix("/applicant/packages/").split("#", 1)[0]
            try:
                found = svc.find_material_package(db(), package_id)
                if found["case_id"] == case_id and found["permit_type"] == svc.application_id_for(application_id):
                    workbench_target = redirect_to
            except KeyError:
                pass
        target = redirect_to if redirect_to.startswith(wizard_prefix) else workbench_target or default_url
        return RedirectResponse(url=target, status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/extractions/reject")
    def reject_extraction(case_id: str, application_id: str, field_id: str = Form(...)):
        try:
            svc.reject_extraction_queue_item(db(), case_id, application_id, field_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Extraction not found")
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/2", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/ai/suggest-field")
    def suggest_field(case_id: str, application_id: str, field_id: str = Form(...), background_tasks: BackgroundTasks = BackgroundTasks()):
        permit_type = svc.application_id_for(application_id)
        schema = svc.load_form_schema(permit_type)
        field_label = next((field.get("label") for field in schema.get("fields", []) if field.get("id") == field_id), None) or "valittu kenttä"
        job = svc.start_ai_job(db(), case_id, permit_type, "field_suggestion", f"Kenttäehdotus: {field_label}", ["retrieval_context", "field_suggestion_agent"], eta_seconds=job_eta_seconds("field_suggestion_retrieval"), managed_background=True)
        background_tasks.add_task(svc.create_field_suggestion, db(), case_id, application_id, field_id, settings=settings(), job_id=job["id"])
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/3", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/ai/suggest-form")
    def suggest_form(case_id: str, application_id: str, background_tasks: BackgroundTasks):
        job = svc.start_form_suggestions_job(db(), case_id, application_id, settings=settings())
        background_tasks.add_task(svc.execute_form_suggestions_job, db(), case_id, application_id, job["id"], settings())
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/3", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/suggestions/{suggestion_id}/accept")
    def accept_suggestion(case_id: str, application_id: str, suggestion_id: str, edited_value: str = Form("")):
        try:
            svc.accept_suggestion(db(), case_id, application_id, suggestion_id, edited_value)
        except KeyError:
            raise HTTPException(status_code=404, detail="Suggestion not found")
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/3", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/suggestions/accept-selected")
    def accept_selected_suggestions(case_id: str, application_id: str, suggestion_ids: list[str] = Form([])):
        svc.accept_selected_suggestions(db(), case_id, application_id, suggestion_ids)
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/3", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/suggestions/accept-all")
    def accept_all_suggestions(case_id: str, application_id: str):
        svc.accept_all_suggestions(db(), case_id, application_id)
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/3#ai-suggestions", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/suggestions/{suggestion_id}/reject")
    def reject_suggestion(case_id: str, application_id: str, suggestion_id: str):
        # F23: symmetric with accept — unknown id is a 404, not a silent success/500.
        try:
            svc.reject_suggestion(db(), case_id, application_id, suggestion_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Suggestion not found")
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/3", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/chat")
    def contextual_chat(case_id: str, application_id: str, message: str = Form(...)):
        svc.contextual_chat_answer(db(), case_id, application_id, message, settings=settings())
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/3#contextual-chat", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/chat/messages")
    async def chat_messages(case_id: str, application_id: str, request: Request):
        payload = await request.json()
        message = str(payload.get("message") or "").strip()
        if not message:
            return JSONResponse({"error": "message_required"}, status_code=400)
        result = svc.contextual_chat_answer(db(), case_id, application_id, message, settings=settings())
        chat = svc.artifact_json(db(), case_id, "contextual_chat", svc.application_id_for(application_id), {"messages": []})
        last = chat.get("messages", [])[-1] if chat.get("messages") else None
        return {
            "answer": last.get("answer") or result.get("answer", "") if last else result.get("answer", ""),
            "messages": chat.get("messages", [])[-6:],
        }

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/chat/stream")
    async def chat_stream(case_id: str, application_id: str, request: Request):
        """SSE streaming chat endpoint. Yields text/event-stream."""
        payload = await request.json()
        message = str(payload.get("message") or "").strip()
        if not message:
            return JSONResponse({"error": "message_required"}, status_code=400)

        permit_type = svc.application_id_for(application_id)
        context = svc.contextual_chat_context(db(), case_id, permit_type)
        prompt = (
            "Olet lupahakemuksen contextual chat -avustaja. Vastaa suomeksi hakijan kysymykseen. "
            "Käytä vain annettua lomake- ja liitekontekstia. Älä käytä benchmark- tai viranomaisesimerkkiaineistoa. "
            "Älä muuta hakemuksen tietoja; jos ehdotat muutosta, kerro että käyttäjän pitää hyväksyä se erikseen.\n\n"
            f"Kysymys: {message}\n\n"
            f"Konteksti JSON: {json.dumps(context, ensure_ascii=False)}"
        )

        async def generate():
            full_answer = ""
            try:
                client = create_llm_client(settings(), purpose="chat", db=db(), case_id=case_id)
                for chunk in client.responses_stream(
                    input_text=prompt,
                    max_output_tokens=900,
                    reasoning_effort="low",
                    text_verbosity="medium",
                ):
                    full_answer += chunk
                    yield f"data: {json.dumps({'chunk': chunk})}\n\n"
                yield f"data: {json.dumps({'done': True})}\n\n"
            except Exception:
                logger.exception("Contextual chat stream failed for case %s", case_id)
                yield f"data: {json.dumps({'error': 'AI-avustaja ei voinut vastata juuri nyt.'})}\n\n"
                return

            # Save to artifact store
            history = svc.artifact_json(db(), case_id, "contextual_chat", permit_type, {"messages": []})
            turn = {
                "id": f"chat_{uuid.uuid4().hex[:10]}",
                "role": "user",
                "message": message,
                "answer": full_answer,
                "created_at": time.time(),
            }
            history.setdefault("messages", []).append(turn)
            history["messages"] = history["messages"][-30:]
            svc.save_artifact(db(), case_id, "contextual_chat", permit_type, history)
            svc.event(db(), case_id, "ai.chat.answered", "Contextual chat answered (stream)", {"permit_type": permit_type})

        return StreamingResponse(generate(), media_type="text/event-stream")

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/voice/mock")
    def voice_mock(case_id: str, application_id: str, transcript: str = Form("")):
        svc.voice_mock_turn(db(), case_id, application_id, transcript, settings=settings())
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/3#voice-assistant", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/ai/check")
    def run_check(case_id: str, application_id: str, background_tasks: BackgroundTasks):
        permit_type = svc.application_id_for(application_id)
        job = svc.start_ai_job(db(), case_id, permit_type, "application_check", "AI- ja sääntötarkistus", ["deterministic_checks", "completeness_validator", "consistency_validator", "criteria_assessor", "applicant_precheck_report"], eta_seconds=job_eta_seconds("application_check"), managed_background=True)
        background_tasks.add_task(svc.run_application_check, db(), case_id, application_id, settings=settings(), job_id=job["id"])
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/4#check", status_code=303)

    @router.get("/cases/{case_id}/lupademo/application/{application_id}/package", response_class=HTMLResponse)
    def package(case_id: str, application_id: str):
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/6", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/submit")
    def submit(case_id: str, application_id: str):
        try:
            svc.submit_application(db(), case_id, application_id, settings=settings())
        except RuntimeError:
            return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/4#check-blocking", status_code=303)
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/receipt", status_code=303)

    @router.get("/cases/{case_id}/lupademo/application/{application_id}/receipt", response_class=HTMLResponse)
    def receipt(request: Request, case_id: str, application_id: str):
        return templates.TemplateResponse(request, "applicant/receipt.html", {**context(case_id, application_id)})

    return router
