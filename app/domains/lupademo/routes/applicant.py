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

def create_applicant_router(db_getter: Callable[[], Any], settings_getter: Callable[[], Any]) -> APIRouter:
    router = APIRouter()

    def db() -> Any:
        return db_getter()

    def settings() -> Any:
        return settings_getter()

    def material_return_url(case_id: str, application_id: str, package_id: str = "", fragment: str = "") -> str:
        if package_id:
            try:
                found = svc.find_material_package(db(), package_id)
                if found["case_id"] == case_id and found["permit_type"] == svc.application_id_for(application_id):
                    return f"/applicant/packages/{package_id}{fragment}"
            except KeyError:
                pass
        return f"/cases/{case_id}/lupademo/application/{application_id}/wizard/2{fragment}"

    def workspace_options() -> list[dict[str, str]]:
        options = []
        active_case_id = scenarios.active_case_id(db())
        active_package_id = str(db().get_app_setting("lupademo_active_package_id") or "")
        for case in db().list_cases():
            case_id = str(case.get("id") or "")
            project = svc.artifact_json(db(), case_id, "lv_project", "main", {})
            common = svc.artifact_json(db(), case_id, "lv_common_data", "main", {"data": {}})
            packages = [
                item.get("data_json")
                for item in db().list_artifacts(case_id)
                if item.get("type") == "material_package" and isinstance(item.get("data_json"), dict)
            ]
            for package in packages or [{}]:
                options.append(
                    {
                        "case_id": case_id,
                        "package_id": str(package.get("package_id") or ""),
                        "customer": str(common.get("data", {}).get("applicant_name") or "Hakija"),
                        "package": str(package.get("display_name") or project.get("name") or case.get("title") or "Lupahanke"),
                        "selected": case_id == active_case_id and (not active_package_id or package.get("package_id") == active_package_id),
                    }
                )
        return options

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
        ai_jobs = svc.list_ai_jobs(db_obj, case_id, app["permit_type"])
        material_jobs = [job for job in ai_jobs if job.get("kind") in {"material_ingest", "intake_analysis"}]
        material_job = next(
            (job for job in material_jobs if job.get("status") in {"pending", "running"}),
            material_jobs[0] if material_jobs else None,
        )
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
            "ai_jobs": ai_jobs,
            "material_job": material_job,
            "contextual_chat": aj("contextual_chat", {"messages": []}),
            "voice_state": svc.voice_feature_state(settings_obj),
            "voice_chat": aj("voice_chat", {"turns": []}),
            "environmental_story": svc.artifact_json(db_obj, case_id, "environmental_material_story", "main", {}),
        }

    @router.get("/", response_class=HTMLResponse)
    def landing(request: Request):
        return templates.TemplateResponse(
            request,
            "landing.html",
            {"scenarios": scenarios.list_scenarios(), "workspace_options": workspace_options()},
        )

    @router.post("/workspace/select")
    def workspace_select(workspace: str = Form(...)):
        option = next((item for item in workspace_options() if f"{item['case_id']}:{item['package_id']}" == workspace), None)
        if not option:
            raise HTTPException(status_code=404, detail="Case not found")
        case_id = option["case_id"]
        scenario = svc.artifact_json(db(), case_id, "demo_scenario", "main", {}).get("scenario", {})
        scenarios.set_active_scenario(db(), case_id, str(scenario.get("id") or "generic_project"))
        db().set_app_setting("lupademo_active_package_id", option["package_id"])
        return RedirectResponse(url="/", status_code=303)

    @router.get("/demo/scenarios", response_class=HTMLResponse)
    def scenario_list(request: Request):
        return templates.TemplateResponse(request, "scenarios.html", {"scenarios": scenarios.list_scenarios()})

    @router.get("/demo-lab/presenter", response_class=HTMLResponse)
    def presenter_lab(request: Request):
        return templates.TemplateResponse(request, "presenter.html", {"scenarios": scenarios.list_scenarios()})

    @router.get("/applicant", response_class=HTMLResponse)
    def applicant_start(request: Request):
        return templates.TemplateResponse(request, "applicant_start.html")

    @router.get("/applicant/packages/new", response_class=HTMLResponse)
    def applicant_package_new(request: Request):
        return templates.TemplateResponse(request, "package_new.html")

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
            svc.execute_material_ingest_and_analysis_job,
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

    def package_context(package_id: str) -> dict[str, Any]:
        found = svc.find_material_package(db(), package_id)
        case_id = found["case_id"]
        permit_type = found["permit_type"]
        out = context(case_id, permit_type)
        attachments = svc.list_attachments(db(), case_id, permit_type)
        ai_jobs = svc.list_ai_jobs(db(), case_id, permit_type)
        active_job = next((job for job in ai_jobs if job.get("status") == "running"), None)
        material_package = svc.find_material_package(db(), package_id)["package"]
        out.update(
            {
                "package_id": package_id,
                "material_package": material_package,
                "attachments": attachments,
                "official_attachments": [item for item in attachments if item.get("delivery_scope") == "official_submission"],
                "context_attachments": [item for item in attachments if item.get("delivery_scope") == "ai_context_only"],
                "material_intake": svc.artifact_json(db(), case_id, "material_intake", permit_type, {}),
                "ai_jobs": ai_jobs,
                "active_job": active_job,
                "package_analysis": svc.artifact_json(db(), case_id, "package_analysis_result", permit_type, {}),
                "field_extraction_queue": svc.artifact_json(db(), case_id, "field_extraction_queue", permit_type, {"items": []}),
                "can_start_analysis": material_package.get("status") == "ready" and active_job is None,
            }
        )
        return out

    @router.get("/applicant/packages/{package_id}", response_class=HTMLResponse)
    def applicant_package_workbench(request: Request, package_id: str):
        try:
            return templates.TemplateResponse(request, "package_workbench.html", package_context(package_id))
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
        return RedirectResponse(url=f"/applicant/packages/{package_id}#analysis", status_code=303)

    @router.get("/projects/new", response_class=HTMLResponse)
    def project_new(request: Request):
        return templates.TemplateResponse(request, "applicant_start.html")

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
        return templates.TemplateResponse(request, "lv_project.html", {**context(project_id)})

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
        return templates.TemplateResponse(request, "lv_project.html", {**context(case_id)})

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
        if step == 6:
            ctx["package"] = svc.compose_submission_package(db(), case_id, application_id)
        return templates.TemplateResponse(request, "wizard.html", {**ctx, "step": step})

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
        if not filename.lower().endswith(".zip"):
            raise HTTPException(status_code=422, detail="Only .zip material packages are supported")
        content = await zip_file.read()
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
        job = svc.start_material_ingest_job(db(), case_id, application_id, document_count, azure_enabled=azure_enabled)
        background_tasks.add_task(
            svc.execute_material_ingest_and_analysis_job,
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

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/materials/upload-files")
    async def upload_material_files(
        case_id: str,
        application_id: str,
        files: list[UploadFile] = File(...),
        package_name: str = Form(""),
        return_package_id: str = Form(""),
    ):
        payload: list[tuple[str, bytes]] = []
        for item in files:
            payload.append((item.filename or "dokumentti", await item.read()))
        try:
            svc.ingest_application_files(db(), settings(), case_id, application_id, payload, display_name=package_name)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return RedirectResponse(url=material_return_url(case_id, application_id, return_package_id, "#documents"), status_code=303)

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

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/ai/suggest-field")
    def suggest_field(case_id: str, application_id: str, field_id: str = Form(...)):
        suggestion = svc.create_field_suggestion(db(), case_id, application_id, field_id, settings=settings())
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/4#suggestion-{suggestion.id}", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/ai/suggest-form")
    def suggest_form(background_tasks: BackgroundTasks, case_id: str, application_id: str):
        job = svc.start_form_suggestions_job(db(), case_id, application_id, settings=settings())
        background_tasks.add_task(svc.execute_form_suggestions_job, db(), case_id, application_id, job["id"], settings())
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/4#ai-jobs", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/suggestions/{suggestion_id}/accept")
    def accept_suggestion(case_id: str, application_id: str, suggestion_id: str, edited_value: str = Form("")):
        try:
            svc.accept_suggestion(db(), case_id, application_id, suggestion_id, edited_value)
        except KeyError:
            raise HTTPException(status_code=404, detail="Suggestion not found")
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/4", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/suggestions/accept-selected")
    def accept_selected_suggestions(case_id: str, application_id: str, suggestion_ids: list[str] = Form([])):
        svc.accept_selected_suggestions(db(), case_id, application_id, suggestion_ids)
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/4", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/suggestions/{suggestion_id}/reject")
    def reject_suggestion(case_id: str, application_id: str, suggestion_id: str):
        svc.reject_suggestion(db(), case_id, application_id, suggestion_id)
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/4", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/chat")
    def contextual_chat(case_id: str, application_id: str, message: str = Form(...)):
        svc.contextual_chat_answer(db(), case_id, application_id, message, settings=settings())
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/4#contextual-chat", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/voice/mock")
    def voice_mock(case_id: str, application_id: str, transcript: str = Form("")):
        svc.voice_mock_turn(db(), case_id, application_id, transcript, settings=settings())
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/4#voice-assistant", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/ai/check")
    def run_check(case_id: str, application_id: str):
        svc.run_application_check(db(), case_id, application_id, settings=settings())
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/5#check", status_code=303)

    @router.get("/cases/{case_id}/lupademo/application/{application_id}/package", response_class=HTMLResponse)
    def package(case_id: str, application_id: str):
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/6", status_code=303)

    @router.post("/cases/{case_id}/lupademo/application/{application_id}/submit")
    def submit(case_id: str, application_id: str):
        try:
            svc.submit_application(db(), case_id, application_id, settings=settings())
        except RuntimeError:
            return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/wizard/5#check-blocking", status_code=303)
        return RedirectResponse(url=f"/cases/{case_id}/lupademo/application/{application_id}/receipt", status_code=303)

    @router.get("/cases/{case_id}/lupademo/application/{application_id}/receipt", response_class=HTMLResponse)
    def receipt(request: Request, case_id: str, application_id: str):
        return templates.TemplateResponse(request, "receipt.html", {**context(case_id, application_id)})

    return router
