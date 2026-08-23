"""Customer workspace orchestration for the mocked LV applicant portal."""

from __future__ import annotations

import time
from typing import Any

from .core import artifact_json, ensure_application, load_form_schema, save_artifact
from .intake import find_material_package, list_attachments
from .jobs import list_ai_jobs

PERMIT_LABELS = {
    "building_residential": "Rakennuslupa",
    "environmental_industrial": "Ympäristölupa",
    "event_public": "Tapahtumalupa",
}

STATUS_LABELS = {
    "draft": "Luonnos",
    "not_started": "Ei aloitettu",
    "submitted": "Lähetetty",
    "application_draft": "Luonnos",
    "new": "Aloittamatta",
}

PACKAGE_STATUS_LABELS = {
    "processing": "Aineistoa käsitellään",
    "ready": "Valmis analysoitavaksi",
    "analysis_ready": "Analyysi valmis",
    "failed": "Käsittely epäonnistui",
}

CUSTOMER_FIXTURES: tuple[dict[str, Any], ...] = (
    {
        "id": "p2x",
        "display_name": "P2X",
        "profile": {
            "applicant_name": "P2X",
            "business_id": "0000000-1",
            "contact_person": "Laura Laine",
            "phone": "+358 40 000 1001",
            "email": "laura.laine@p2x.example",
        },
        "projects": (
            {
                "project_name": "P2X:n vetylaitos",
                "permit_type": "environmental_industrial",
                "municipality": "Oulu",
                "address": "Teollisuustie 10",
                "status": "draft",
            },
            {
                "project_name": "P2X:n tuotantoalueen laajennus",
                "permit_type": "building_residential",
                "municipality": "Oulu",
                "address": "Energiakatu 4",
                "status": "submitted",
            },
        ),
    },
    {
        "id": "ren-gas",
        "display_name": "Ren-Gas Oy",
        "profile": {
            "applicant_name": "Ren-Gas Oy",
            "business_id": "0000000-2",
            "contact_person": "Mikko Mäki",
            "phone": "+358 40 000 2002",
            "email": "mikko.maki@ren-gas.example",
        },
        "projects": (
            {
                "project_name": "Ren-Gas biokaasulaitos",
                "permit_type": "environmental_industrial",
                "municipality": "Vantaa",
                "address": "Kierrätystie 8",
                "status": "draft",
            },
            {
                "project_name": "Ren-Gas tankkausaseman muutos",
                "permit_type": "building_residential",
                "municipality": "Vantaa",
                "address": "Lentokentäntie 22",
                "status": "submitted",
            },
        ),
    },
    {
        "id": "sharpcell",
        "display_name": "Sharpcell",
        "profile": {
            "applicant_name": "Sharpcell",
            "business_id": "0000000-3",
            "contact_person": "Salla Salmi",
            "phone": "+358 40 000 3003",
            "email": "salla.salmi@sharpcell.example",
        },
        "projects": (
            {
                "project_name": "Sharpcell akkukennotuotanto",
                "permit_type": "environmental_industrial",
                "municipality": "Tampere",
                "address": "Akkukatu 1",
                "status": "draft",
            },
            {
                "project_name": "Sharpcell pilottilaboratorio",
                "permit_type": "building_residential",
                "municipality": "Tampere",
                "address": "Innovaatiokatu 6",
                "status": "submitted",
            },
        ),
    },
)


def customer_fixtures() -> list[dict[str, Any]]:
    """Return a copy-safe list of the selectable demo customers."""
    return [
        {
            "id": fixture["id"],
            "display_name": fixture["display_name"],
            "profile": dict(fixture["profile"]),
        }
        for fixture in CUSTOMER_FIXTURES
    ]


def ensure_customer_demo_data(db: Any) -> None:
    """Create the customer identities and their starter hankkeet once."""
    from .. import scenarios

    for fixture in CUSTOMER_FIXTURES:
        customer_id = str(fixture["id"])
        profile = dict(fixture["profile"])
        db.upsert_customer(customer_id, str(fixture["display_name"]), profile)
        existing_cases = db.list_cases_for_customer(customer_id)
        if existing_cases:
            for case in existing_cases:
                case_id = str(case["id"])
                project = artifact_json(db, case_id, "lv_project", "main", {})
                if not project.get("seeded_customer_workspace"):
                    continue
                for permit_type, status in (project.get("statuses") or {}).items():
                    if status == "submitted":
                        _set_seed_application_state(db, case_id, str(permit_type), status)
            continue
        for project in fixture["projects"]:
            started = scenarios.create_generic_project(
                db,
                project_name=str(project["project_name"]),
                permit_type=str(project["permit_type"]),
                applicant_name=str(profile.get("applicant_name") or ""),
                municipality=str(project.get("municipality") or ""),
                address=str(project.get("address") or ""),
                customer_id=customer_id,
                customer_profile=profile,
                created_from="lv",
            )
            _set_seed_application_state(
                db,
                started["case_id"],
                started["permit_type"],
                str(project.get("status") or "draft"),
            )


def _set_seed_application_state(db: Any, case_id: str, permit_type: str, status: str) -> None:
    app = ensure_application(db, case_id, permit_type)
    app["status"] = status
    save_artifact(db, case_id, "permit_application", permit_type, app)
    project = artifact_json(db, case_id, "lv_project", "main", {})
    project.setdefault("statuses", {})[permit_type] = status
    project["seeded_customer_workspace"] = True
    save_artifact(db, case_id, "lv_project", "main", project)
    db.set_case_status(case_id, "submitted" if status == "submitted" else "application_draft")
    if status != "submitted":
        return

    received_at = time.strftime("%Y-%m-%d %H:%M:%S")
    receipt_id = f"LV-SEED-{case_id[:8].upper()}"
    save_artifact(
        db,
        case_id,
        "receipt",
        permit_type,
        {
            "receipt_id": receipt_id,
            "application_id": permit_type,
            "received_at": received_at,
            "authority": load_form_schema(permit_type).get("authority_default", "Viranomainen"),
            "package_id": f"seeded-{case_id[:8]}",
            "official_attachment_count": 0,
        },
    )
    common = artifact_json(db, case_id, "lv_common_data", "main", {}).get("data", {})
    queue = artifact_json(db, case_id, "authority_queue", "main", {"items": []})
    if not any(item.get("application_id") == permit_type for item in queue.get("items", [])):
        queue.setdefault("items", []).append(
            {
                "application_id": permit_type,
                "application_title": load_form_schema(permit_type).get("title", permit_type),
                "receipt_id": receipt_id,
                "applicant_name": (app.get("form_data") or {}).get("applicant_name") or common.get("applicant_name", ""),
                "municipality": (app.get("form_data") or {}).get("municipality") or common.get("municipality", ""),
                "status": "käsittelyssä",
                "submitted_at": received_at,
                "quality_score": 75,
                "open_issue_count": 0,
                "info_request_risk": "matala",
                "permit_type": permit_type,
                "processing_stage": "vastaanotettu",
                "sla_due_at": time.strftime("%Y-%m-%d", time.localtime(time.time() + 30 * 24 * 60 * 60)),
                "urgency": "normaali",
            }
        )
    save_artifact(db, case_id, "authority_queue", "main", queue)


def customer_case(db: Any, customer_id: str, case_id: str) -> dict[str, Any]:
    """Return a case only when it belongs to the current customer."""
    case = db.get_case(case_id)
    if not case or str(case.get("customer_id") or "") != customer_id:
        raise KeyError(case_id)
    return case


def find_package_case(db: Any, package_id: str) -> dict[str, Any]:
    """Resolve a package through the existing material-package index."""
    return find_material_package(db, package_id)


def customer_application_options(db: Any, customer_id: str) -> list[dict[str, str]]:
    """Flatten customer-owned applications for package and application pickers."""
    options: list[dict[str, str]] = []
    for case in db.list_cases_for_customer(customer_id):
        case_id = str(case["id"])
        project = artifact_json(db, case_id, "lv_project", "main", {})
        project_name = str(project.get("name") or case.get("title") or "Lupahanke")
        for permit_type in project.get("permits") or project.get("applications") or []:
            options.append(
                {
                    "case_id": case_id,
                    "case_name": project_name,
                    "permit_type": str(permit_type),
                    "label": PERMIT_LABELS.get(str(permit_type), str(permit_type)),
                    "value": f"{case_id}|{permit_type}",
                }
            )
    return options


def _application_row(db: Any, case_id: str, permit_type: str, project: dict[str, Any]) -> dict[str, Any]:
    app = artifact_json(db, case_id, "permit_application", permit_type, {})
    status = str(app.get("status") or project.get("statuses", {}).get(permit_type) or "not_started")
    suggestions = artifact_json(db, case_id, "ai_suggestions", permit_type, {}).get("suggestions", [])
    pending_suggestions = [item for item in suggestions if str(item.get("status") or "pending") == "pending"]
    check_report = artifact_json(db, case_id, "check_report", permit_type, {})
    blocking = [
        issue for issue in check_report.get("issues", [])
        if issue.get("severity") == "blocking" and not issue.get("resolved")
    ]
    package = artifact_json(db, case_id, "material_package", permit_type, {})
    attachments = list_attachments(db, case_id, permit_type)
    if status == "submitted":
        task = "Hakemus lähetetty"
        task_kind = "done"
    elif blocking:
        task = "Korjaa estävä puute"
        task_kind = "blocking"
    elif pending_suggestions:
        task = "Tarkista AI-ehdotukset"
        task_kind = "review"
    elif package.get("status") in {"processing", "queued"}:
        task = "Aineistoa käsitellään"
        task_kind = "processing"
    elif attachments:
        task = "Jatka hakemusta"
        task_kind = "continue"
    else:
        task = "Aloita hakemus"
        task_kind = "start"
    return {
        "permit_type": permit_type,
        "label": PERMIT_LABELS.get(permit_type, permit_type),
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "task": task,
        "task_kind": task_kind,
        "application_url": f"/cases/{case_id}/lupademo/application/{permit_type}/wizard/1",
        "package": package,
    }


def _package_review_row(db: Any, review: dict[str, Any]) -> dict[str, Any]:
    review_id = str(review.get("id") or "")
    permit_type = str(review.get("permit_type") or "")
    package = artifact_json(db, review_id, "material_package", permit_type, {})
    analysis = artifact_json(db, review_id, "package_analysis_result", permit_type, {})
    jobs = [
        job
        for job in list_ai_jobs(db, review_id, permit_type)
        if job.get("kind") in {"material_ingest", "intake_analysis"}
    ]
    active_job = next((job for job in jobs if job.get("status") in {"pending", "running"}), None)
    if active_job:
        status_key = "processing"
        next_action = "Seuraa käsittelyä"
    elif package.get("status") == "failed" or any(job.get("status") == "failed" for job in jobs):
        status_key = "failed"
        next_action = "Avaa paketti ja yritä uudelleen"
    elif analysis:
        status_key = "analysis_ready"
        next_action = "Katso tarkistuksen tulokset"
    elif package.get("status") == "ready":
        status_key = "ready"
        next_action = "Aloita analyysi"
    else:
        status_key = "processing"
        next_action = "Avaa paketti"
    return {
        "review_id": review_id,
        "name": str(review.get("display_name") or "Hakemuspaketin tarkistus"),
        "permit_label": PERMIT_LABELS.get(permit_type, permit_type),
        "municipality": str(review.get("municipality") or "Kunta ei tiedossa"),
        "status": status_key,
        "status_label": PACKAGE_STATUS_LABELS[status_key],
        "next_action": next_action,
        "url": f"/applicant/packages/{package.get('package_id')}" if package.get("package_id") else "/applicant/packages/new",
    }


def case_application_rows(db: Any, case_id: str, project: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if not isinstance(project, dict):
        stored = artifact_json(db, case_id, "lv_project", "main", {})
        project = stored if isinstance(stored, dict) else {}
    permit_types = list(project.get("permits") or project.get("applications") or [])
    return [_application_row(db, case_id, permit_type, project) for permit_type in permit_types]


def customer_dashboard(db: Any, customer_id: str) -> dict[str, Any]:
    """Build the complete LV home view for one signed-in customer."""
    customer = db.get_customer(customer_id)
    if not customer:
        raise KeyError(customer_id)
    cases: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    for case in db.list_cases_for_customer(customer_id):
        case_id = str(case["id"])
        project = artifact_json(db, case_id, "lv_project", "main", {})
        common = artifact_json(db, case_id, "lv_common_data", "main", {"data": {}}).get("data", {})
        permit_types = list(project.get("permits") or project.get("applications") or [])
        applications = [_application_row(db, case_id, permit_type, project) for permit_type in permit_types]
        actionable = [item for item in applications if item["task_kind"] != "done"]
        next_task = actionable[0] if actionable else None
        case_row = {
            "case_id": case_id,
            "name": str(project.get("name") or case.get("title") or "Lupahanke"),
            "location": str(project.get("location") or common.get("municipality") or ""),
            "status": str(case.get("status") or "new"),
            "status_label": STATUS_LABELS.get(str(case.get("status") or "new"), str(case.get("status") or "new")),
            "updated_at": case.get("updated_at") or project.get("updated_at"),
            "applications": applications,
            "application_count": len(applications),
            "next_task": next_task,
            "project_url": f"/projects/{case_id}",
        }
        cases.append(case_row)
        for application in actionable:
            tasks.append({
                "case_id": case_id,
                "case_name": case_row["name"],
                "label": application["task"],
                "permit_label": application["label"],
                "url": application["application_url"],
                "kind": application["task_kind"],
            })
    package_reviews = [
        _package_review_row(db, review)
        for review in db.list_package_reviews(customer_id)
        if str(review.get("status") or "active") == "active"
    ]
    return {
        "customer": customer,
        "cases": cases,
        "tasks": tasks,
        "primary_task": tasks[0] if tasks else None,
        "package_reviews": package_reviews,
        "case_count": len(cases),
        "application_count": sum(case["application_count"] for case in cases),
    }
