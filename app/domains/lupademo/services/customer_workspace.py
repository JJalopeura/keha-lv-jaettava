"""Customer workspace orchestration for the LV applicant portal.

Accounts are the login roster only. A customer starts with an empty workspace and
creates its own hankkeet — nothing is pre-seeded.
"""

from __future__ import annotations

from typing import Any

from .core import artifact_json
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

#: Login roster. Identities only — no starter projects, no seeded applications.
CUSTOMER_ACCOUNTS: tuple[dict[str, Any], ...] = (
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
    },
)


def ensure_customer_accounts(db: Any) -> None:
    """Make sure the login roster exists. Never creates projects or applications.

    Accounts removed from `CUSTOMER_ACCOUNTS` are pruned when they own no cases, so a
    database created by an older roster converges on the current one. An account that
    still owns cases is kept rather than orphaning them.
    """
    for account in CUSTOMER_ACCOUNTS:
        db.upsert_customer(str(account["id"]), str(account["display_name"]), dict(account["profile"]))
    known = {str(account["id"]) for account in CUSTOMER_ACCOUNTS}
    try:
        stored = db.list_customers()
    except Exception:
        return
    for customer in stored:
        customer_id = str(customer.get("id") or "")
        if not customer_id or customer_id in known:
            continue
        try:
            if db.list_cases_for_customer(customer_id):
                continue
        except Exception:
            continue
        try:
            db.delete_customer(customer_id)
        except Exception:
            pass


def customer_fixtures(db: Any | None = None) -> list[dict[str, Any]]:
    """Return the selectable login accounts, in roster order.

    The code roster is authoritative for who can log in; stored rows only supply the
    profile fields (a customer may have edited their contact details).
    """
    stored: dict[str, dict[str, Any]] = {}
    if db is not None:
        try:
            stored = {str(item.get("id") or ""): item for item in db.list_customers()}
        except Exception:
            stored = {}
    accounts: list[dict[str, Any]] = []
    for account in CUSTOMER_ACCOUNTS:
        customer_id = str(account["id"])
        row = stored.get(customer_id) or {}
        accounts.append(
            {
                "id": customer_id,
                "display_name": str(row.get("display_name") or account["display_name"]),
                "profile": dict(row.get("profile") or account["profile"]),
            }
        )
    return accounts


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
    wizard_url = f"/cases/{case_id}/lupademo/application/{permit_type}/wizard/1"
    # Kevytasiointi hankkeet (created_from="kevytasiointi") open the service view
    # directly — never the wizard (decision 2026-08-27).
    application_url = wizard_url
    if str(project.get("created_from") or "") == "kevytasiointi":
        pid = str(package.get("package_id") or "")
        if pid:
            application_url = f"/applicant/packages/{pid}"
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
        "application_url": application_url,
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
        # Kevytasiointi hankkeet open the service view ("Avaa hanke" → palvelunäkymä);
        # wizard hankkeet keep the project workspace.
        if str(project.get("created_from") or "") == "kevytasiointi" and applications:
            project_url = applications[0]["application_url"]
        else:
            project_url = f"/projects/{case_id}"
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
            "project_url": project_url,
            # Origin marker for the home view ("Kevytasiointi" badge).
            "created_from": str(project.get("created_from") or ""),
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
