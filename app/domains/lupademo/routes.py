from __future__ import annotations

import json
import io
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from . import scenarios, security, services as svc


DOMAIN_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(DOMAIN_DIR / "templates"))


def create_lupademo_router(db_getter: Callable[[], Any], settings_getter: Callable[[], Any]) -> APIRouter:
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
            out.update(
                {
                    "application": app,
                    "schema": svc.load_form_schema(app["permit_type"]),
                    "criteria": svc.load_criteria(app["permit_type"]),
                    "attachments": svc.list_attachments(db(), case_id, app["permit_type"]),
                    "suggestions": svc.artifact_json(db(), case_id, "ai_suggestions", app["permit_type"], {"suggestions": []}).get("suggestions", []),
                    "check_report": svc.artifact_json(db(), case_id, "check_report", app["permit_type"], {}),
                    "precheck_gate": svc.precheck_gate_status(db(), case_id, app["permit_type"]),
                    "package": svc.artifact_json(db(), case_id, "submission_package", app["permit_type"], {}),
                    "receipt": svc.artifact_json(db(), case_id, "receipt", app["permit_type"], {}),
                    "wizard": svc.wizard_step_status(db(), case_id, app["permit_type"]),
                    "structure_extraction": svc.artifact_json(db(), case_id, "structure_extraction_result", app["permit_type"], {}),
                    "intake_analysis": svc.artifact_json(db(), case_id, "intake_analysis", app["permit_type"], {}),
                    "material_package": svc.current_material_package(db(), case_id, app["permit_type"]),
                    "package_analysis": svc.artifact_json(db(), case_id, "package_analysis_result", app["permit_type"], {}),
                    "field_extraction_queue": svc.artifact_json(db(), case_id, "field_extraction_queue", app["permit_type"], {"items": []}),
                    "ai_jobs": svc.list_ai_jobs(db(), case_id, app["permit_type"]),
                    "contextual_chat": svc.artifact_json(db(), case_id, "contextual_chat", app["permit_type"], {"messages": []}),
                    "voice_state": svc.voice_feature_state(settings()),
                    "voice_chat": svc.artifact_json(db(), case_id, "voice_chat", app["permit_type"], {"turns": []}),
                    "environmental_story": svc.artifact_json(db(), case_id, "environmental_material_story", "main", {}),
                }
            )
        return out

    @router.get("/", response_class=HTMLResponse)
    def landing(request: Request):
        return templates.TemplateResponse("landing.html", {"request": request, "scenarios": scenarios.list_scenarios()})

    @router.get("/demo/scenarios", response_class=HTMLResponse)
    def scenario_list(request: Request):
        return templates.TemplateResponse("scenarios.html", {"request": request, "scenarios": scenarios.list_scenarios()})

    @router.get("/demo-lab/presenter", response_class=HTMLResponse)
    def presenter_lab(request: Request):
        return templates.TemplateResponse("presenter.html", {"request": request, "scenarios": scenarios.list_scenarios()})

    @router.get("/applicant", response_class=HTMLResponse)
    def applicant_start(request: Request):
        return templates.TemplateResponse("applicant_start.html", {"request": request})

    @router.get("/applicant/packages/new", response_class=HTMLResponse)
    def applicant_package_new(request: Request):
        return templates.TemplateResponse("package_new.html", {"request": request})

    @router.post("/applicant/packages/new")
    async def applicant_package_create(
        request: Request,
        background_tasks: BackgroundTasks,
        package_name: str = Form("Hakemusaineisto"),
        permit_type: str = Form("environmental_industrial"),
        applicant_name: str = Form("Hakija Oy"),
        municipality: str = Form("Vantaa"),
        files: list[UploadFile] = File(default=[]),
        zip_file: Optional[UploadFile] = File(default=None),
        text_body: str = Form(""),
    ):
        application_id = svc.application_id_for(permit_type)
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

        started = scenarios.create_generic_project(
            db(),
            project_name=package_name or "Hakemusaineisto",
            permit_type=application_id,
            applicant_name=applicant_name,
            municipality=municipality,
            applicant_role="company",
            created_from="package_workbench",
        )
        case_id = started["case_id"]
        package = svc.create_pending_material_package(db(), case_id, application_id, package_name or "Hakemusaineisto", document_count)
        azure_enabled = str(getattr(settings(), "llm_provider", "mock") or "mock").lower() != "mock"
        job = svc.start_material_ingest_job(db(), case_id, application_id, document_count, azure_enabled=azure_enabled)
        background_tasks.add_task(
            svc.execute_material_ingest_job,
            db(),
            settings(),
            case_id,
            application_id,
            package["package_id"],
            job["id"],
            package_name or "Hakemusaineisto",
            zip_payload=zip_payload,
            files_payload=upload_payload,
            text_body=text_body,
        )
        return RedirectResponse(url=f"/applicant/packages/{package['package_id']}", status_code=303)

    def package_context(request: Request, package_id: str) -> dict[str, Any]:
        found = svc.find_material_package(db(), package_id)
        case_id = found["case_id"]
        permit_type = found["permit_type"]
        out = context(case_id, permit_type)
        attachments = svc.list_attachments(db(), case_id, permit_type)
        ai_jobs = svc.list_ai_jobs(db(), case_id, permit_type)
        active_job = next((job for job in ai_jobs if job.get("status") == "running"), None)
        # Ingestion may finish while context() refreshes the job list. Read the
        # package again so the page cannot combine a stale processing package
        # with an already completed job and strand the user without an action.
        found = svc.find_material_package(db(), package_id)
        material_package = found["package"]
        package_analysis = svc.artifact_json(db(), case_id, "package_analysis_result", permit_type, {})
        out.update(
            {
                "request": request,
                "package_id": package_id,
                "material_package": material_package,
                "attachments": attachments,
                "official_attachments": [item for item in attachments if item.get("delivery_scope") == "official_submission"],
                "context_attachments": [item for item in attachments if item.get("delivery_scope") == "ai_context_only"],
                "material_intake": svc.artifact_json(db(), case_id, "material_intake", permit_type, {}),
                "ai_jobs": ai_jobs,
                "active_job": active_job,
                "package_analysis": package_analysis,
                "can_start_analysis": material_package.get("status") == "ready" and not package_analysis and not active_job,
                "field_extraction_queue": svc.artifact_json(db(), case_id, "field_extraction_queue", permit_type, {"items": []}),
            }
        )
        return out

    @router.get("/applicant/packages/{package_id}", response_class=HTMLResponse)
    def applicant_package_workbench(request: Request, package_id: str):
        try:
            return templates.TemplateResponse("package_workbench.html", package_context(request, package_id))
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
        job = svc.start_intake_analysis_job(db(), found["case_id"], found["permit_type"], settings=settings(), managed_background=True)
        background_tasks.add_task(
            svc.execute_intake_analysis_job,
            db(),
            found["case_id"],
            found["permit_type"],
            job["id"],
            settings(),
        )
        return RedirectResponse(url=f"/applicant/packages/{package_id}#ai-job-banner", status_code=303)

    @router.get("/projects/new", response_class=HTMLResponse)
    def project_new(request: Request):
        return templates.TemplateResponse("landing.html", {"request": request, "scenarios": scenarios.list_scenarios()})

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
        project_name: str = Form("Uusi lupahanke"),
        permit_type: str = Form("building_residential"),
        applicant_name: str = Form(""),
        municipality: str = Form(""),
        address: str = Form(""),
        tenant_id: str = Form(""),
        applicant_role: str = Form("company"),
        created_from: str = Form("direct_demo"),
        start_at: str = Form("lv_project"),
    ):
        started = scenarios.create_generic_project(
            db(),
            project_name=project_name,
            permit_type=permit_type,
            applicant_name=applicant_name,
            municipality=municipality,
            address=address,
            tenant_id=tenant_id,
            applicant_role=applicant_role,
            created_from=created_from,
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
        return templates.TemplateResponse("lv_project.html", {"request": request, **context(project_id)})

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
        case_id = scenarios.active_case_id(db()) or svc.ensure_demo_case(db())
        return RedirectResponse(url=f"/lv/projects/{case_id}", status_code=303)

    @router.get("/lv/projects/{case_id}", response_class=HTMLResponse)
    def lv_project(request: Request, case_id: str):
        return templates.TemplateResponse("lv_project.html", {"request": request, **context(case_id)})

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
        if step == 5:
            ctx["package"] = svc.compose_submission_package(db(), case_id, application_id)
        return templates.TemplateResponse("wizard.html", {"request": request, **ctx, "step": step})

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
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/2", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/materials/upload-zip")
    async def upload_material_zip(case_id: str, application_id: str, zip_file: UploadFile = File(...)):
        filename = zip_file.filename or "aineisto.zip"
        if not filename.lower().endswith(".zip"):
            raise HTTPException(status_code=422, detail="Only .zip material packages are supported")
        content = await zip_file.read()
        try:
            svc.ingest_application_zip(db(), settings(), case_id, application_id, filename, content)
        except zipfile.BadZipFile:
            raise HTTPException(status_code=422, detail="Invalid zip file")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/2#materials", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/materials/upload-files")
    async def upload_material_files(
        case_id: str,
        application_id: str,
        files: list[UploadFile] = File(...),
        package_name: str = Form(""),
    ):
        payload: list[tuple[str, bytes]] = []
        for item in files:
            payload.append((item.filename or "dokumentti", await item.read()))
        try:
            svc.ingest_application_files(db(), settings(), case_id, application_id, payload, display_name=package_name)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/2#materials", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/materials/add-text")
    def add_material_text(
        case_id: str,
        application_id: str,
        text_title: str = Form(""),
        text_body: str = Form(...),
        delivery_scope: str = Form("ai_context_only"),
    ):
        try:
            svc.ingest_application_text(db(), settings(), case_id, application_id, text_title, text_body, delivery_scope=delivery_scope)
        except ValueError:
            raise HTTPException(status_code=422, detail="Text material is empty")
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/2#materials", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/attachments/{attachment_id}/scope")
    def change_attachment_scope(case_id: str, application_id: str, attachment_id: str, delivery_scope: str = Form(...)):
        svc.set_attachment_scope(db(), case_id, application_id, attachment_id, delivery_scope)
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/2", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/attachments/{attachment_id}/metadata")
    def update_attachment_metadata(case_id: str, application_id: str, attachment_id: str, primary_type: str = Form(""), delivery_scope: str = Form("")):
        try:
            svc.update_attachment_metadata(db(), case_id, application_id, attachment_id, primary_type=primary_type, delivery_scope=delivery_scope)
        except KeyError:
            raise HTTPException(status_code=404, detail="Attachment not found")
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/2", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/attachments/{attachment_id}/delete")
    def delete_attachment(case_id: str, application_id: str, attachment_id: str):
        svc.delete_attachment(db(), case_id, application_id, attachment_id)
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/2", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/ai/structure-from-attachments")
    def structure_from_attachments(case_id: str, application_id: str):
        if svc.application_id_for(application_id) != "environmental_industrial":
            raise HTTPException(status_code=400, detail="Structure extraction demo is available for environmental permits")
        svc.structure_environmental_application_from_attachments(db(), case_id, settings=settings())
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/2#structure-extraction", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/ai/intake-analysis")
    def intake_analysis(case_id: str, application_id: str):
        svc.start_intake_analysis_job(db(), case_id, application_id, settings=settings())
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/2#ai-jobs", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/extractions/accept")
    def accept_extraction(case_id: str, application_id: str, field_id: str = Form(...), edited_value: str = Form("")):
        try:
            svc.accept_extraction_queue_item(db(), case_id, application_id, field_id, edited_value)
        except KeyError:
            raise HTTPException(status_code=404, detail="Extraction not found")
        except ValueError:
            raise HTTPException(status_code=400, detail="Extraction value is empty")
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/2#package-analysis-result", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/ai/suggest-field")
    def suggest_field(case_id: str, application_id: str, field_id: str = Form(...)):
        suggestion = svc.create_field_suggestion(db(), case_id, application_id, field_id, settings=settings())
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/3#suggestion-{suggestion.id}", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/ai/suggest-form")
    def suggest_form(case_id: str, application_id: str):
        schema = svc.load_form_schema(svc.application_id_for(application_id))
        for field in schema.get("fields", []):
            if field.get("ai_suggest"):
                svc.create_field_suggestion(db(), case_id, application_id, field["id"], settings=settings())
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/3#suggestions", status_code=303)

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

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/suggestions/{suggestion_id}/reject")
    def reject_suggestion(case_id: str, application_id: str, suggestion_id: str):
        svc.reject_suggestion(db(), case_id, application_id, suggestion_id)
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/3", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/chat")
    def contextual_chat(case_id: str, application_id: str, message: str = Form(...)):
        svc.contextual_chat_answer(db(), case_id, application_id, message, settings=settings())
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/3#contextual-chat", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/voice/mock")
    def voice_mock(case_id: str, application_id: str, transcript: str = Form("")):
        svc.voice_mock_turn(db(), case_id, application_id, transcript, settings=settings())
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/3#voice-assistant", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/ai/check")
    def run_check(case_id: str, application_id: str):
        svc.run_application_check(db(), case_id, application_id, settings=settings())
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/4#check", status_code=303)

    @router.get("/cases/{case_id}/lupademo/application/{application_id}/package", response_class=HTMLResponse)
    def package(request: Request, case_id: str, application_id: str):
        package_data = svc.compose_submission_package(db(), case_id, application_id)
        return templates.TemplateResponse("package.html", {"request": request, **context(case_id, application_id), "package": package_data})

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/submit")
    def submit(case_id: str, application_id: str):
        try:
            svc.submit_application(db(), case_id, application_id, settings=settings())
        except RuntimeError:
            return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/4#check-blocking", status_code=303)
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/6", status_code=303)

    @router.get("/cases/{case_id}/lupademo/application/{application_id}/receipt", response_class=HTMLResponse)
    def receipt(request: Request, case_id: str, application_id: str):
        return templates.TemplateResponse("receipt.html", {"request": request, **context(case_id, application_id)})

    @router.get("/cases/{case_id}/lupademo/authority/queue", response_class=HTMLResponse)
    def authority_queue(request: Request, case_id: str, permit_type: str = "", municipality: str = ""):
        svc.ensure_seed_state(db(), case_id)
        queue = svc.artifact_json(db(), case_id, "authority_queue", "main", {"items": []})
        items = queue.get("items", [])
        if permit_type:
            items = [item for item in items if item.get("permit_type") == permit_type or item.get("application_id") == permit_type]
        if municipality:
            items = [item for item in items if str(item.get("municipality") or "").lower() == municipality.lower()]
        return templates.TemplateResponse("authority_queue.html", {"request": request, **context(case_id), "queue": {**queue, "items": items}, "filters": {"permit_type": permit_type, "municipality": municipality}})

    @router.get("/authority/queue", response_class=HTMLResponse)
    def authority_queue_global(permit_type: str = "", municipality: str = ""):
        case_id = scenarios.active_case_id(db()) or svc.ensure_demo_case(db())
        suffix = ""
        if permit_type or municipality:
            suffix = f"?permit_type={permit_type}&municipality={municipality}"
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/authority/queue{suffix}", status_code=303)

    @router.get("/cases/{case_id}/lupademo/authority/applications/{application_id}", response_class=HTMLResponse)
    def authority_detail(request: Request, case_id: str, application_id: str):
        return templates.TemplateResponse(
            "authority_detail.html",
            {
                "request": request,
                **context(case_id, application_id),
                "authority_summary": svc.artifact_json(db(), case_id, "authority_summary", svc.application_id_for(application_id), {}),
                "additional_info_draft": svc.artifact_json(db(), case_id, "additional_info_draft", svc.application_id_for(application_id), {}),
                "authority_memo": svc.artifact_json(db(), case_id, "authority_memo", svc.application_id_for(application_id), {}),
                "caseworker_observations": svc.caseworker_observation_panel(db(), case_id, application_id),
            },
        )

    @router.get("/authority/applications/{application_id}", response_class=HTMLResponse)
    def authority_detail_global(application_id: str):
        case_id = scenarios.active_case_id(db()) or svc.ensure_demo_case(db())
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/authority/applications/{application_id}", status_code=303)

    @router.post("/cases/{case_id}/lupademo/authority/applications/{application_id}/observations/{observation_id}/status")
    def update_observation_status(case_id: str, application_id: str, observation_id: str, status: str = Form(...), note: str = Form("")):
        try:
            svc.update_caseworker_observation_status(db(), case_id, application_id, observation_id, status, note)  # type: ignore[arg-type]
        except KeyError:
            raise HTTPException(status_code=404, detail="Observation not found")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid observation status")
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/authority/applications/{application_id}#observation-{observation_id}", status_code=303)

    @router.post("/cases/{case_id}/lupademo/authority/applications/{application_id}/ai/summary")
    def generate_authority_summary(case_id: str, application_id: str):
        svc.authority_summary(db(), case_id, application_id, settings=settings())
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/authority/applications/{application_id}", status_code=303)

    @router.post("/cases/{case_id}/lupademo/authority/applications/{application_id}/ai/request-info-draft")
    def generate_additional_info(case_id: str, application_id: str):
        svc.additional_info_draft(db(), case_id, application_id, settings=settings())
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/authority/applications/{application_id}", status_code=303)

    @router.post("/cases/{case_id}/lupademo/authority/applications/{application_id}/ai/decision-support")
    def generate_authority_memo(case_id: str, application_id: str):
        svc.authority_memo(db(), case_id, application_id, settings=settings())
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/authority/applications/{application_id}", status_code=303)

    @router.post("/cases/{case_id}/lupademo/authority/applications/{application_id}/status")
    def update_authority_status(case_id: str, application_id: str, status: str = Form(...)):
        queue = svc.artifact_json(db(), case_id, "authority_queue", "main", {"items": []})
        for item in queue.get("items", []):
            if item.get("application_id") == svc.application_id_for(application_id):
                item["status"] = status
        svc.save_artifact(db(), case_id, "authority_queue", "main", queue)
        svc.event(db(), case_id, "authority.status.updated", "Authority status updated", {"application_id": application_id, "status": status})
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/authority/applications/{application_id}", status_code=303)

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
            {"event": "tenant.authenticated", "detail": client.tenant_id, "request_id": payload["request_id"]},
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
        svc.append_suggestion_service_log(db(), case_id, "api_demo", client.tenant_id, payload.get("permit_type", ""), result.get("criteria_version", ""))
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/suggestion-service/api-demo", status_code=303)

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
                "created_at": uuid.uuid4().hex,
            }
        )
        svc.save_artifact(db(), case_id, "ui_only_acceptance_log", "main", log)
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/suggestion-service/ui-only#acceptance-log", status_code=303)

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
        return templates.TemplateResponse(
            "audit_trail.html",
            {"request": request, **context(case_id), "audit_events": events},
        )

    @router.get("/admin/audit-trail", response_class=HTMLResponse)
    def audit_trail_global(request: Request):
        case_id = scenarios.active_case_id(db()) or svc.ensure_demo_case(db())
        return audit_trail(request, case_id)

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
        return templates.TemplateResponse("metrics.html", {"request": request, **context(case_id), "metrics": svc.metrics_snapshot(db(), case_id)})

    @router.get("/cases/{case_id}/lupademo/metrics/report.json")
    def metrics_report_json(case_id: str):
        return JSONResponse(svc.metrics_snapshot(db(), case_id))

    @router.get("/cases/{case_id}/lupademo/metrics/report.html", response_class=HTMLResponse)
    def metrics_report_html(case_id: str):
        metrics_payload = svc.metrics_snapshot(db(), case_id)
        html = f"""<!doctype html>
<html lang="fi">
<head><meta charset="utf-8"><title>Lupademo mittariraportti</title></head>
<body>
<h1>Lupademo mittariraportti</h1>
<p>Hakemuksia: {metrics_payload.get("application_count", 0)} · Lähetetty: {metrics_payload.get("submitted_count", 0)}</p>
<h2>Ennen ja jälkeen</h2>
<ul>
  <li>Laatupisteet ennen AI:tä: {metrics_payload.get("quality_score_before_ai", 0)}</li>
  <li>Laatupisteet nyt: {metrics_payload.get("quality_score_after_ai", 0)}</li>
  <li>Puutteet ennen AI:tä: {metrics_payload.get("issue_count_before_ai", 0)}</li>
  <li>Puutteet nyt: {metrics_payload.get("issue_count_after_ai", 0)}</li>
  <li>Arvioitu käsittelijäaikasäästö: {metrics_payload.get("estimated_caseworker_minutes_saved", 0)} min</li>
</ul>
<h2>Auditointi</h2>
<ul>
  <li>LLM-kutsuja: {metrics_payload.get("agent_call_count", 0)}</li>
  <li>File search -kutsuja: {metrics_payload.get("file_search_call_count", 0)}</li>
  <li>Fallbackeja: {metrics_payload.get("fallback_count", 0)}</li>
</ul>
</body>
</html>"""
        return HTMLResponse(html)

    @router.post("/cases/{case_id}/lupademo/admin/agent-chain/audit-run")
    def agent_chain_audit_run(case_id: str):
        svc.run_agent_chain_audit(db(), settings(), case_id)
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/metrics", status_code=303)

    def _demo_case_for_api() -> str:
        return scenarios.active_case_id(db()) or svc.ensure_demo_case(db())

    def _api_response(client: security.ApiClient, request_id: str, job: dict[str, Any], result: dict[str, Any], *, case_id: str, application_id: str) -> dict[str, Any]:
        evidence_refs: list[dict[str, Any]] = []
        for key in ("suggestions", "issues", "extracted_fields", "questions"):
            rows = result.get(key) if isinstance(result.get(key), list) else []
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
            "tenant_id": client.tenant_id,
            "result": result,
        }

    def _complete_api_job(case_id: str, application_id: str, job: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        svc.complete_ai_job(db(), case_id, application_id, job["id"], summary="API job completed.", result_artifact=f"api_result:{job['kind']}")
        found = svc.find_ai_job(db(), job["id"])
        return found["job"]

    @router.post("/api/ai/jobs")
    async def api_ai_jobs(request: Request):
        payload = await request.json()
        case_id = str(payload.get("project_id") or payload.get("case_id") or _demo_case_for_api())
        application_id = svc.application_id_for(str(payload.get("application_id") or payload.get("permit_type") or "building_residential"))
        job = svc.start_ai_job(db(), case_id, application_id, str(payload.get("job_type") or "intake_analysis"), str(payload.get("title") or "AI job"), ["request_router"], eta_seconds=10)
        return svc.ai_job_status_payload(job, case_id=case_id, application_id=application_id)

    @router.get("/api/ai/jobs/{job_id}")
    def api_ai_job_status(job_id: str):
        try:
            svc.refresh_ai_job(db(), job_id)
            found = svc.find_ai_job(db(), job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="job_not_found")
        return svc.ai_job_status_payload(found["job"], case_id=found["case_id"], application_id=found["application_id"])

    @router.get("/api/ai/jobs/{job_id}/stream")
    def api_ai_job_stream(job_id: str):
        try:
            svc.refresh_ai_job(db(), job_id)
            found = svc.find_ai_job(db(), job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="job_not_found")
        payload = svc.ai_job_status_payload(found["job"], case_id=found["case_id"], application_id=found["application_id"])
        return Response("event: status\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n", media_type="text/event-stream")

    @router.get("/api/ai/jobs/{job_id}/events")
    def api_ai_job_events(job_id: str):
        try:
            svc.refresh_ai_job(db(), job_id)
            found = svc.find_ai_job(db(), job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="job_not_found")
        payload = svc.ai_job_status_payload(found["job"], case_id=found["case_id"], application_id=found["application_id"])
        return {"job_id": job_id, "status": payload["status"], "events": payload["events"]}

    @router.get("/api/ai/jobs/{job_id}/result")
    def api_ai_job_result(job_id: str):
        try:
            svc.refresh_ai_job(db(), job_id)
            found = svc.find_ai_job(db(), job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="job_not_found")
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

    @router.get("/api/v1/jobs/{job_id}")
    def api_v1_job_status(job_id: str):
        return api_ai_job_status(job_id)

    @router.get("/api/v1/jobs/{job_id}/stream")
    def api_v1_job_stream(job_id: str):
        return api_ai_job_stream(job_id)

    @router.get("/api/v1/jobs/{job_id}/events")
    def api_v1_job_events(job_id: str):
        return api_ai_job_events(job_id)

    @router.get("/api/v1/jobs/{job_id}/result")
    def api_v1_job_result(job_id: str):
        return api_ai_job_result(job_id)

    @router.post("/api/v1/jobs/{job_id}/cancel")
    def api_v1_job_cancel(job_id: str):
        return api_ai_job_cancel(job_id)

    @router.post("/api/v1/jobs/{job_id}/retry")
    def api_v1_job_retry(job_id: str):
        return api_ai_job_retry(job_id)

    @router.post("/api/v1/field-suggestions")
    async def api_v1_field_suggestions(request: Request):
        client = security.require_api_client(request)
        payload = security.validate_client_payload(client, await request.json())
        case_id = _demo_case_for_api()
        application_id = svc.application_id_for(str(payload.get("permit_type") or "building_residential"))
        request_id = str(payload.get("request_id") or f"field_{uuid.uuid4().hex[:8]}")
        job = svc.start_ai_job(db(), case_id, application_id, "field_suggestion", "Field suggestion API", ["field_suggestion_agent"], eta_seconds=8)
        result = security.annotate_api_response(client, svc.suggestion_service_field(payload, db=db(), settings=settings(), case_id=case_id))
        completed = _complete_api_job(case_id, application_id, job, result)
        return _api_response(client, request_id, completed, result, case_id=case_id, application_id=application_id)

    @router.post("/api/v1/applications:precheck")
    async def api_v1_applications_precheck(request: Request):
        client = security.require_api_client(request)
        payload = security.validate_client_payload(client, await request.json())
        case_id = _demo_case_for_api()
        application_id = svc.application_id_for(str(payload.get("permit_type") or "building_residential"))
        request_id = str(payload.get("request_id") or f"precheck_{uuid.uuid4().hex[:8]}")
        job = svc.start_ai_job(db(), case_id, application_id, "precheck", "Application precheck API", ["precheck_agent"], eta_seconds=10)
        result = security.annotate_api_response(client, svc.suggestion_service_check(payload, db=db(), settings=settings(), case_id=case_id))
        completed = _complete_api_job(case_id, application_id, job, result)
        return _api_response(client, request_id, completed, result, case_id=case_id, application_id=application_id)

    @router.post("/api/v1/application-packages:analyze")
    async def api_v1_application_packages_analyze(request: Request):
        client = security.require_api_client(request)
        payload = security.validate_client_payload(client, await request.json())
        case_id = _demo_case_for_api()
        application_id = svc.application_id_for(str(payload.get("permit_type") or "building_residential"))
        request_id = str(payload.get("request_id") or f"analyze_{uuid.uuid4().hex[:8]}")
        job = svc.start_ai_job(
            db(),
            case_id,
            application_id,
            "api_package_analysis",
            "Application package analysis API",
            ["request_validator", "document_classifier", "structure_extractor", "gap_analyzer"],
            eta_seconds=15,
        )
        documents = payload.get("documents") if isinstance(payload.get("documents"), list) else []
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
            status_code=202,
            content={
                "request_id": request_id,
                "job_id": completed["id"],
                "status": status_payload["status"],
                "events_url": f"/api/v1/jobs/{completed['id']}/events",
                "stream_url": f"/api/v1/jobs/{completed['id']}/stream",
                "result_url": f"/api/v1/jobs/{completed['id']}/result",
                "tenant_id": client.tenant_id,
            },
            headers={"Location": f"/api/v1/jobs/{completed['id']}"},
        )

    @router.post("/api/v1/question-plans")
    async def api_v1_question_plans(request: Request):
        client = security.require_api_client(request)
        payload = security.validate_client_payload(client, await request.json())
        case_id = _demo_case_for_api()
        application_id = svc.application_id_for(str(payload.get("permit_type") or "building_residential"))
        request_id = str(payload.get("request_id") or f"questions_{uuid.uuid4().hex[:8]}")
        app_payload = {"form_data": payload.get("form_data") or {}}
        questions = svc.intake_analysis_questions(svc.load_form_schema(application_id), app_payload, payload.get("attachments") or [])
        job = svc.start_ai_job(db(), case_id, application_id, "question_plan", "Question plan API", ["gap_analyzer"], eta_seconds=5)
        completed = _complete_api_job(case_id, application_id, job, {"questions": questions})
        return _api_response(client, request_id, completed, {"questions": questions}, case_id=case_id, application_id=application_id)

    @router.post("/api/v1/authority:summarize")
    async def api_v1_authority_summarize(request: Request):
        client = security.require_api_client(request)
        payload = security.validate_client_payload(client, await request.json())
        case_id = _demo_case_for_api()
        application_id = svc.application_id_for(str(payload.get("permit_type") or payload.get("application_id") or "building_residential"))
        request_id = str(payload.get("request_id") or f"authority_{uuid.uuid4().hex[:8]}")
        job = svc.start_ai_job(db(), case_id, application_id, "authority_summary", "Authority summary API", ["authority_summary_agent"], eta_seconds=8)
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
    async def api_suggest_field(request: Request, x_lupademo_api_key: Optional[str] = Header(None, alias="X-Lupademo-Api-Key")):
        try:
            client = security.require_api_client(request)
            payload = security.validate_client_payload(client, await request.json())
            result = security.annotate_api_response(client, svc.suggestion_service_field(payload, db=db(), settings=settings()))
            svc.append_suggestion_service_log(db(), svc.ensure_demo_case(db()), "api_v1_field", client.tenant_id, payload.get("permit_type", ""), result.get("criteria_version", ""), endpoint="/api/v1/suggestions/field", request_id=payload.get("request_id", ""), status="ok", fallback_used=result.get("model_audit", {}).get("fallback_used", False))
            return result
        except HTTPException:
            raise
        except ValidationError as exc:
            return JSONResponse(status_code=422, content={"detail": exc.errors()})

    @router.post("/api/v1/suggestions/form")
    async def api_suggest_form(request: Request, x_lupademo_api_key: Optional[str] = Header(None, alias="X-Lupademo-Api-Key")):
        try:
            client = security.require_api_client(request)
            payload = security.validate_client_payload(client, await request.json())
            result = security.annotate_api_response(client, svc.suggestion_service_form(payload, db=db(), settings=settings()))
            svc.append_suggestion_service_log(db(), svc.ensure_demo_case(db()), "api_v1_form", client.tenant_id, payload.get("permit_type", ""), result.get("criteria_version", ""), endpoint="/api/v1/suggestions/form", request_id=payload.get("request_id", ""), status="ok", fallback_used=result.get("model_audit", {}).get("fallback_used", False))
            return result
        except HTTPException:
            raise
        except ValidationError as exc:
            return JSONResponse(status_code=422, content={"detail": exc.errors()})

    @router.post("/api/v1/checks/application")
    async def api_check(request: Request, x_lupademo_api_key: Optional[str] = Header(None, alias="X-Lupademo-Api-Key")):
        try:
            client = security.require_api_client(request)
            payload = security.validate_client_payload(client, await request.json())
            result = security.annotate_api_response(client, svc.suggestion_service_check(payload, db=db(), settings=settings()))
            svc.append_suggestion_service_log(db(), svc.ensure_demo_case(db()), "api_v1_check", client.tenant_id, payload.get("permit_type", ""), result.get("criteria_version", ""), endpoint="/api/v1/checks/application", request_id=payload.get("request_id", ""), status="ok", fallback_used=result.get("model_audit", {}).get("fallback_used", False))
            return result
        except HTTPException:
            raise
        except ValidationError as exc:
            return JSONResponse(status_code=422, content={"detail": exc.errors()})

    @router.post("/api/v1/structure/extract")
    async def api_extract(request: Request, x_lupademo_api_key: Optional[str] = Header(None, alias="X-Lupademo-Api-Key")):
        try:
            client = security.require_api_client(request)
            payload = security.validate_client_payload(client, await request.json())
            result = security.annotate_api_response(client, svc.structure_extract(payload, db=db(), settings=settings()))
            svc.append_suggestion_service_log(db(), svc.ensure_demo_case(db()), "api_v1_extract", client.tenant_id, payload.get("permit_type", ""), result.get("criteria_version", ""), endpoint="/api/v1/structure/extract", request_id=payload.get("request_id", ""), status="ok", fallback_used=result.get("model_audit", {}).get("fallback_used", False))
            return result
        except HTTPException:
            raise
        except ValidationError as exc:
            return JSONResponse(status_code=422, content={"detail": exc.errors()})

    @router.get("/api/v1/criteria/{criteria_set_id}")
    def api_criteria(criteria_set_id: str, request: Request, x_lupademo_api_key: Optional[str] = Header(None, alias="X-Lupademo-Api-Key")):
        client = security.require_api_client(request)
        if not security.client_can_access_criteria(client, criteria_set_id):
            raise HTTPException(status_code=403, detail=f"Tenant {client.tenant_id} cannot use criteria_set_id {criteria_set_id}")
        for permit in svc.CRITERIA_FILES:
            criteria = svc.load_criteria(permit)
            if criteria.get("criteria_set_id") == criteria_set_id:
                result = security.annotate_api_response(client, {**criteria, "criteria_version": criteria.get("version", "demo")})
                svc.append_suggestion_service_log(db(), svc.ensure_demo_case(db()), "api_v1_criteria", client.tenant_id, permit, result.get("criteria_version", ""), endpoint="/api/v1/criteria/{criteria_set_id}", request_id=criteria_set_id, status="ok", fallback_used=False)
                return result
        raise HTTPException(status_code=404, detail="Criteria set not found")

    return router
