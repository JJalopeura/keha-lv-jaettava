"""End-to-end run of the 2026-11-04 Ren-Gas package through both entry paths.

Not a unit test: this drives the real FastAPI app in-process against Azure AI Foundry,
so it needs the env from `.env`. It writes to a throwaway data dir and prints an
honest report of what the AI produced, what had to be typed by hand, how long each
phase took and what the check found.

    set -a; source .env; set +a
    KEHA_LV_DATA_DIR=/tmp/keha-aa-e2e .venv/bin/python scripts/e2e_aa_package.py

Path B = wizard (/projects/new -> upload zip -> AI intake -> accept -> check -> submit)
Path A = Kevytasiointi (/applicant/packages/new -> upload zip -> save -> submit)
"""

from __future__ import annotations

import io
import os
import re
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA_DIR = Path(os.environ.get("KEHA_LV_DATA_DIR", "/tmp/keha-aa-e2e"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
# Must be set before app.main is imported: it pins its DB at import time.
os.environ["KEHA_LV_DB_PATH"] = str(DATA_DIR / "keha_lv.db")
os.environ["KEHA_LV_UPLOADS_DIR"] = str(DATA_DIR / "uploads")

from fastapi.testclient import TestClient  # noqa: E402

import app.main as main  # noqa: E402
import app.domains.lupademo.services as svc  # noqa: E402
from app.infra.config import load_settings  # noqa: E402
from app.infra.db import DB  # noqa: E402

PERMIT = "environmental_industrial"
PACKAGE = ROOT / "testiaineisto/esimerkkihakemukset/Ren-Gas pelkkä hakemus 20261104.zip"

# Values grounded in the package itself; used only for fields the AI left empty.
FORM_DATA = {
    "activity_name": "Puhtaiden P2X-kaasupolttoaineiden ja CO2-vapaan kaukolämmön yhteistuotantolaitos",
    "permit_basis": (
        "Laitos sijoittuu Alikeravan teollisuuskortteliin Keravan Energian biovoimalaitoksen tontille. "
        "Hanke tuottaa uusiutuvaa e-metaania 15 000 t/a ja kaukolämpöä noin 32 MW. Aikataulu ja "
        "vaikutusalue kuvataan hakemuksessa; lähestymiskartta on liitteenä."
    ),
    "site_location": "Kiinteistö 245-9-348-1, Alikeravan teollisuuskortteli, Kerava. Omistaja- ja haltijatiedot erillisellä liitteellä.",
    "zoning_context": "Alue on asemakaavassa teollisuuskorttelialuetta; kaavamerkinnät ja kaavatilanne hakemuksen kohdassa 2.3.",
    "activity_description": "Power-to-Gas-laitos: vedyn tuotanto, hiilidioksidin talteenotto savukaasuista, metaanin tuotanto ja kaukolämmön tuotanto Keravalla.",
    "production_capacity": "Metaania 15 000 t/a, metaanin tuotantoteho 25 MW ja kaukolämpöä noin 32 MW.",
    "byproducts": "Sivutuoteluokittelua ei haeta; prosessin jätteet ja sivuvirrat kuvataan hakemuksen kohdassa 3.8.",
    "process_description": "Vedyn tuotanto, CO₂:n talteenotto, metaanisynteesi, metaanin syöttö siirtoverkkoon sekä apujäähdytys ja kaukolämmön tuotanto (hakemus 3.3).",
    "risk_preparedness": "Liite 12 Ennaltavarautumissuunnitelma Ren-Gas Kerava; tunnistetut onnettomuus- ja häiriötilanteet hakemuksen kohdassa 5.",
    "land_use_environment": "Alueen itäpuolella valtatie 4, eteläpuolella metsäalue ja Keravanjoki; lähimmät asuinalueet ja herkät kohteet hakemuksen kohdassa 2.4.",
    "land_use_impacts": "YVA-selostuksen mukaiset vaikutukset maankäyttöön ja lähialueen asutukseen (Liite 4 a).",
}
PROFILE = {
    "applicant_name": "Nordic Ren-Gas Oy",
    "business_id": "2347291-4",
    "contact_person": "Mikko Mäki",
    "phone": "+358 40 000 2002",
    "email": "mikko.maki@ren-gas.example",
    "municipality": "Kerava",
    "address": "Laitostie 1",
    "property_id": "245-9-348-1",
}

settings = load_settings()
if not settings.azure_openai_host_core:
    raise SystemExit("AZURE_OPENAI_HOST_CORE missing — source .env first")
db = DB(settings.db_path)
client = TestClient(main.app, follow_redirects=False)


def log(message: str) -> None:
    print(message, flush=True)


def wait_for_jobs(case_id: str, kinds: set[str], timeout_s: float, label: str, *, need: str = "") -> dict:
    """Poll the AI job list until no job of `kinds` is pending/running.

    `need` additionally requires that a job of that kind exists at all: the ingest job
    does not chain into the intake analysis, the applicant starts it explicitly.
    """
    started = time.time()
    last = {}
    while time.time() - started < timeout_s:
        jobs = [job for job in svc.list_ai_jobs(db, case_id, PERMIT) if job.get("kind") in kinds]
        running = [job for job in jobs if job.get("status") in {"pending", "running"}]
        last = jobs[-1] if jobs else {}
        if jobs and not running and (not need or any(job.get("kind") == need for job in jobs)):
            log(f"  {label}: {len(jobs)} job(s) finished in {time.time() - started:.0f}s")
            return last
        time.sleep(2)
    raise SystemExit(f"{label} timed out after {timeout_s:.0f}s (last job: {last.get('kind')}={last.get('status')})")


def run_intake_analysis(case_id: str, application_id: str) -> None:
    """Click "Aineiston analyysi" — the zip ingest does not chain into it."""
    existing = [job for job in svc.list_ai_jobs(db, case_id, PERMIT) if job.get("kind") == "intake_analysis"]
    if not existing:
        response = client.post(f"/cases/{case_id}/lupademo/application/{application_id}/ai/intake-analysis")
        assert response.status_code == 303, response.text
    wait_for_jobs(case_id, {"intake_analysis"}, timeout_s=1800, label="intake analysis", need="intake_analysis")


def latest_job(case_id: str, kind: str) -> dict:
    jobs = [job for job in svc.list_ai_jobs(db, case_id, PERMIT) if job.get("kind") == kind]
    return jobs[-1] if jobs else {}


def post_check(case_id: str, application_id: str) -> dict:
    check_url = f"/cases/{case_id}/lupademo/application/{application_id}/ai/check"
    started = time.time()
    response = client.post(check_url, data={})
    assert response.status_code == 303, response.text
    job = wait_for_jobs(case_id, {"application_check"}, timeout_s=900, label="check")
    report = svc.artifact_json(db, case_id, "check_report", PERMIT, {})
    counts = report.get("counts") or {}
    log(
        f"  check {time.time() - started:.0f}s · score={report.get('score')} readiness={report.get('readiness')} "
        f"blocking={counts.get('blocking')} major={counts.get('major')} minor={counts.get('minor')} "
        f"provider={report.get('provider')} incomplete={((report.get('degraded') or {}).get('incomplete_agents'))}"
    )
    log(f"  summary: {str(report.get('summary'))[:200]}")
    log(f"  job status: {job.get('status')} step={job.get('current_step_label')}")
    return report


def print_criteria(report: dict) -> None:
    log("  criteria:")
    for item in report.get("criteria_results", []):
        log(f"    {str(item.get('criteria_id')):24s} {item.get('status'):6s} {str(item.get('rationale'))[:100]}")
    log("  issues:")
    for issue in report.get("issues", []):
        if issue.get("resolved"):
            continue
        log(f"    {str(issue.get('severity')):9s} {str(issue.get('title'))[:95]}")


def accept_extractions(case_id: str, application_id: str) -> list[str]:
    queue = svc.artifact_json(db, case_id, "field_extraction_queue", PERMIT, {"items": []}).get("items", [])
    pending = [item for item in queue if item.get("status") == "pending" and item.get("field_id")]
    accepted: list[str] = []
    for item in pending:
        response = client.post(
            f"/cases/{case_id}/lupademo/application/{application_id}/extractions/accept",
            data={"field_id": item["field_id"], "redirect_to": f"/cases/{case_id}/lupademo/application/{application_id}/wizard/3"},
        )
        if response.status_code == 303:
            accepted.append(str(item["field_id"]))
    return accepted


def main_path_b() -> dict:
    log("\n== Path B: wizard ==")
    login = client.post("/login", data={"customer_id": "ren-gas"})
    assert login.status_code == 303, login.text

    started = time.time()
    created = client.post(
        "/projects/new",
        data={
            "project_name": "Ren-Gas Kerava (2026-11-04 aineisto)",
            "permit_type": PERMIT,
            "start_at": "wizard",
            **PROFILE,
        },
    )
    assert created.status_code == 303, created.text
    match = re.search(r"/cases/([0-9a-f-]+)/lupademo/application/([a-z_]+)/wizard/", created.headers["location"])
    assert match, created.headers["location"]
    case_id, application_id = match.group(1), match.group(2)
    log(f"  case {case_id} created in {time.time() - started:.0f}s")

    started = time.time()
    with open(PACKAGE, "rb") as handle:
        uploaded = client.post(
            f"/cases/{case_id}/lupademo/application/{application_id}/materials/upload-zip",
            files={"zip_file": (PACKAGE.name, handle, "application/zip")},
        )
    assert uploaded.status_code == 303, uploaded.text
    wait_for_jobs(case_id, {"material_ingest"}, timeout_s=1800, label="ingest")
    attachments = svc.list_attachments(db, case_id, PERMIT)
    log(f"  ingest {time.time() - started:.0f}s · {len(attachments)} attachments stored")
    analysis_started = time.time()
    run_intake_analysis(case_id, application_id)
    queue = svc.artifact_json(db, case_id, "field_extraction_queue", PERMIT, {"items": []}).get("items", [])
    log(f"  analysis {time.time() - analysis_started:.0f}s · extraction queue {len(queue)} field(s)")
    for att in attachments:
        log(f"    {str(att.get('delivery_scope')):18s} {str(att.get('primary_type')):22s} {str(att.get('filename'))[:58]}")

    accepted = accept_extractions(case_id, application_id)
    log(f"  accepted AI extractions: {len(accepted)} {accepted}")

    form_data = svc.artifact_json(db, case_id, "permit_application", PERMIT, {}).get("form_data", {})
    supplied = []
    for field_id, value in FORM_DATA.items():
        if not str(form_data.get(field_id) or "").strip():
            supplied.append(field_id)
    if supplied:
        filled = {**{key: form_data.get(key, "") for key in FORM_DATA}, **{key: FORM_DATA[key] for key in supplied}}
        client.post(f"/cases/{case_id}/lupademo/application/{application_id}/save", data={**filled, "redirect_to": f"/cases/{case_id}/lupademo/application/{application_id}/wizard/4"})
    log(f"  typed by hand (AI left empty): {supplied or 'none'}")

    report = post_check(case_id, application_id)
    print_criteria(report)

    page = client.get(f"/cases/{case_id}/lupademo/application/{application_id}/wizard/4")
    text = page.text
    log(f"  wizard/4 renders: status={page.status_code} · criteria cards={text.count('criteria-result-grid')} · rule titles shown={sum(1 for title in ('Hankkeen yleiskuvaus', 'Kaavoitus', 'Prosessit', 'Riskit – yleiskuvaus') if title in text)}/4")
    incomplete_banner = "Tarkistus jäi kesken" in text or "incomplete" in text
    log(f"  incomplete banner present: {incomplete_banner}")

    gate_before = svc.precheck_gate_status(db, case_id, PERMIT)
    log(f"  gate: can_submit={gate_before.get('can_submit')} blocking={gate_before.get('blocking_count')} red={gate_before.get('red_count')} fresh={gate_before.get('is_fresh')}")

    submitted = client.post(f"/cases/{case_id}/lupademo/application/{application_id}/submit", data={})
    log(f"  submit -> {submitted.status_code} {submitted.headers.get('location')}")
    receipt = svc.artifact_json(db, case_id, "receipt", PERMIT, {})
    log(f"  receipt: {receipt.get('receipt_id')} attachments={receipt.get('official_attachment_count')}")
    return {"case_id": case_id, "report": report, "receipt": receipt, "accepted": accepted, "supplied": supplied}


def main_path_a() -> dict:
    log("\n== Path A: Kevytasiointi ==")
    client.post("/logout")
    login = client.post("/login", data={"customer_id": "ren-gas"})
    assert login.status_code == 303, login.text

    started = time.time()
    with open(PACKAGE, "rb") as handle:
        created = client.post(
            "/applicant/packages/new",
            data={"package_name": "Ren-Gas Kerava (2026-11-04 aineisto)", "permit_type": PERMIT, **PROFILE, "permit_basis": FORM_DATA["permit_basis"]},
            files={"zip_file": (PACKAGE.name, handle, "application/zip")},
        )
    assert created.status_code == 303, created.text
    match = re.search(r"/applicant/packages/([0-9a-zA-Z_-]+)", created.headers["location"])
    assert match, created.headers["location"]
    package_id = match.group(1)
    found = svc.find_material_package(db, package_id)
    case_id = found["case_id"]
    log(f"  package {package_id} · case {case_id} in {time.time() - started:.0f}s")

    wait_for_jobs(case_id, {"material_ingest"}, timeout_s=1800, label="ingest (kevyt)")
    attachments = svc.list_attachments(db, case_id, PERMIT)
    log(f"  {len(attachments)} attachments stored")
    analyzed = client.post(f"/applicant/packages/{package_id}/analyze")
    log(f"  analyze -> {analyzed.status_code}")
    wait_for_jobs(case_id, {"intake_analysis"}, timeout_s=1800, label="intake analysis (kevyt)", need="intake_analysis")

    saved = client.post(f"/applicant/packages/{package_id}/save", data=FORM_DATA)
    log(f"  save -> {saved.status_code}")
    submitted = client.post(f"/applicant/packages/{package_id}/submit", data=FORM_DATA)
    log(f"  submit -> {submitted.status_code} {submitted.headers.get('location')}")
    receipt = svc.artifact_json(db, case_id, "receipt", PERMIT, {})
    page = client.get(f"/applicant/packages/{package_id}")
    log(f"  workbench status {page.status_code} · receipt {receipt.get('receipt_id')}")
    return {"case_id": case_id, "package_id": package_id, "receipt": receipt, "attachments": len(attachments)}


if __name__ == "__main__":
    if not PACKAGE.exists():
        raise SystemExit(f"package missing: {PACKAGE}")
    zip_size = PACKAGE.stat().st_size
    with zipfile.ZipFile(PACKAGE) as archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
    log(f"package: {PACKAGE.name} · {zip_size / 1e6:.1f} MB · {len(members)} files")
    started = time.time()
    result_b = main_path_b()
    result_a = main_path_a()
    log(f"\ntotal e2e {time.time() - started:.0f}s")
    log(f"path B: {result_b['case_id']} receipt={result_b['receipt'].get('receipt_id')}")
    log(f"path A: {result_a['case_id']} receipt={result_a['receipt'].get('receipt_id')}")
