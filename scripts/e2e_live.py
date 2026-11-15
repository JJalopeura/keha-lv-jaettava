"""Live e2e on the deployed App Service: the 2026-11-04 Ren-Gas package, both paths.

Drives the running site over HTTP exactly as a browser would (session cookie, form
posts, zip upload, job polling through the same status JSON the UI polls), so it proves
the *deployed* app, not the local code. Needs no secrets.

    .venv/bin/python scripts/e2e_live.py
    KEHA_LV_BASE_URL=https://keha-lv-web.azurewebsites.net .venv/bin/python scripts/e2e_live.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

BASE = os.environ.get("KEHA_LV_BASE_URL", "https://keha-lv-web.azurewebsites.net").rstrip("/")
PACKAGE = Path(__file__).resolve().parent.parent / "testiaineisto/esimerkkihakemukset/Ren-Gas pelkka\u0308 hakemus 20261104.zip"
# The filename above is a fallback spelling; resolve the real file by prefix.
if not PACKAGE.exists():
    candidates = list((Path(__file__).resolve().parent.parent / "testiaineisto/esimerkkihakemukset").glob("Ren-Gas pelk*20261104.zip"))
    if not candidates:
        raise SystemExit("Ren-Gas 2026-11-04 package not found")
    PACKAGE = candidates[0]

PERMIT = "environmental_industrial"
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
FORM_DATA = {
    "activity_name": "Puhtaiden P2X-kaasupolttoaineiden ja CO2-vapaan kaukolämmön yhteistuotantolaitos",
    "permit_basis": (
        "Laitos sijoittuu Alikeravan teollisuuskortteliin Keravan Energian biovoimalaitoksen tontille. "
        "Hanke tuottaa uusiutuvaa e-metaania 15 000 t/a ja kaukolämpöä noin 32 MW. Aikataulu ja "
        "vaikutusalue kuvataan hakemuksessa; lähestymiskartta on liitteenä."
    ),
    "byproducts": "Sivutuoteluokittelua ei haeta; prosessin jätteet ja sivuvirrat kuvataan hakemuksen kohdassa 3.8.",
}

session = httpx.Client(
    base_url=BASE,
    follow_redirects=False,
    timeout=180.0,
    headers={"User-Agent": "keha-lv-e2e/1.0"},
)


def log(message: str) -> None:
    print(message, flush=True)


def must(response: httpx.Response, code: int = 200, label: str = "") -> httpx.Response:
    if response.status_code != code:
        raise SystemExit(f"{label or 'request'} -> {response.status_code} (expected {code})\n{response.text[:600]}")
    return response


def active_job(page_html: str, label_prefix: str) -> str:
    """Return the status URL of the active job the page is showing.

    The wizard sidebar renders possibly several labelled jobs; the Kevytasiointi page
    renders one `data-ai-job` element without a kind label. Match the wizard markup by
    label first, then fall back to any single status URL.
    """
    for match in re.finditer(
        r'<article class="wizard-ai-job" data-wizard-ai-job data-status-url="([^"]+)"(.*?)</article>',
        page_html,
        re.DOTALL,
    ):
        if label_prefix in match.group(2):
            return match.group(1)
    fallback = re.search(r'data-status-url="([^"]+)"', page_html)
    return fallback.group(1) if fallback else ""


def wait_job(page_url: str, label_prefix: str, timeout_s: float, label: str) -> None:
    started = time.time()
    status_url = ""
    transient_errors = 0
    # The job may take a few seconds to appear after the POST/redirect; do not read
    # "not on the page yet" as "already finished".
    appear_deadline = started + 90
    while time.time() - started < timeout_s:
        page = session.get(BASE + page_url)
        if page.status_code >= 500:
            # A deployed instance can restart under load (and the first probe after a
            # deployment may land on a starting container). Count it, keep polling, but
            # never hide a persistent failure.
            transient_errors += 1
            log(f"  {label}: transient {page.status_code} on {page_url} ({transient_errors})")
            if transient_errors >= 8:
                raise SystemExit(f"{label}: {page_url} kept returning {page.status_code}")
            time.sleep(5)
            continue
        html = must(page, label=f"GET {page_url}").text
        status_url = status_url or active_job(html, label_prefix)
        if not status_url:
            if time.time() > appear_deadline:
                log(f"  {label}: no active job on the page after 90 s (assuming finished)")
                return
            time.sleep(3)
            continue
        payload = json.loads(must(session.get(BASE + status_url), label="GET job").text)
        if payload.get("status") in {"succeeded", "failed", "cancelled"}:
            log(f"  {label}: {payload.get('status')} in {time.time() - started:.0f}s")
            if payload.get("status") != "succeeded":
                raise SystemExit(f"{label} failed: {payload.get('error_message')}")
            return
        time.sleep(4)
    raise SystemExit(f"{label} timed out after {timeout_s:.0f}s")


def extraction_fields(*pages: str) -> list[str]:
    fields: list[str] = []
    for html in pages:
        for block in re.finditer(r'action="[^"]*/extractions/accept"(.*?)</form>', html, re.DOTALL):
            match = re.search(r'name="field_id" value="([^"]+)"', block.group(1))
            if match and match.group(1) not in fields:
                fields.append(match.group(1))
    return fields


def login() -> None:
    must(session.post(BASE + "/login", data={"customer_id": "ren-gas"}), 303, "login")


def path_b() -> None:
    log("\n== Path B: wizard (live) ==")
    login()
    created = must(
        session.post(
            BASE + "/projects/new",
            data={"project_name": "Ren-Gas Kerava (2026-11-04 aineisto)", "permit_type": PERMIT, "start_at": "wizard", **PROFILE},
        ),
        303,
        "create project",
    )
    match = re.search(r"/cases/([0-9a-f-]+)/lupademo/application/([a-z_]+)/wizard/", created.headers["location"])
    if not match:
        raise SystemExit(f"unexpected redirect: {created.headers['location']}")
    case_id, application_id = match.group(1), match.group(2)
    base = f"/cases/{case_id}/lupademo/application/{application_id}"
    log(f"  case {case_id}")

    started = time.time()
    with open(PACKAGE, "rb") as handle:
        must(
            session.post(
                BASE + base + "/materials/upload-zip",
                files={"zip_file": (PACKAGE.name, handle, "application/zip")},
            ),
            303,
            "upload zip",
        )
    wait_job(base + "/wizard/2", "Aineiston valmistelu", 1800, "ingest")
    log(f"  ingest round-trip {time.time() - started:.0f}s")

    started = time.time()
    must(session.post(BASE + base + "/ai/intake-analysis"), 303, "intake analysis")
    wait_job(base + "/wizard/2", "Aineiston analyysi", 1800, "analysis")
    log(f"  analysis round-trip {time.time() - started:.0f}s")

    page2 = must(session.get(BASE + base + "/wizard/2")).text
    page3 = must(session.get(BASE + base + "/wizard/3")).text
    fields = extraction_fields(page2, page3)
    log(f"  AI extractions offered: {len(fields)} {fields}")
    for field_id in fields:
        response = session.post(
            BASE + base + "/extractions/accept",
            data={"field_id": field_id, "redirect_to": base + "/wizard/3#ai-suggestions"},
        )
        if response.status_code != 303:
            log(f"    accept {field_id} -> {response.status_code}")
    saved = {**FORM_DATA, "redirect_to": base + "/wizard/4"}
    must(session.post(BASE + base + "/save", data=saved), 303, "save")

    started = time.time()
    must(session.post(BASE + base + "/ai/check"), 303, "run check")
    wait_job(base + "/wizard/4", "Hakemuksen tarkistus", 1800, "check")
    log(f"  check round-trip {time.time() - started:.0f}s")

    check_page = must(session.get(BASE + base + "/wizard/4")).text
    blocking = check_page.count("Estävä ·")
    log(f"  check page: 'Estävä ·' occurrences={blocking} · 'lähetys on mahdollinen'={'lähetys on mahdollinen' in check_page}")
    submitted = session.post(BASE + base + "/submit")
    log(f"  submit -> {submitted.status_code} {submitted.headers.get('location')}")
    receipt = must(session.get(BASE + base + "/receipt")).text
    receipt_id = re.search(r"Kuittitunnus\s*<strong>([^<]+)</strong>", receipt)
    log(f"  receipt: {receipt_id.group(1) if receipt_id else 'missing'}")


def path_a() -> None:
    log("\n== Path A: Kevytasiointi (live) ==")
    session.post(BASE + "/logout")
    login()
    started = time.time()
    with open(PACKAGE, "rb") as handle:
        created = session.post(
            BASE + "/applicant/packages/new",
            data={"package_name": "Ren-Gas Kerava (2026-11-04 aineisto)", "permit_type": PERMIT, **PROFILE, "permit_basis": FORM_DATA["permit_basis"]},
            files={"zip_file": (PACKAGE.name, handle, "application/zip")},
        )
    if created.status_code != 303:
        raise SystemExit(f"kevyt package create -> {created.status_code}\n{created.text[:600]}")
    match = re.search(r"/applicant/packages/([0-9a-zA-Z_-]+)", created.headers["location"])
    if not match:
        raise SystemExit(f"unexpected redirect: {created.headers['location']}")
    package_id = match.group(1)
    log(f"  package {package_id} in {time.time() - started:.0f}s")

    page = must(session.get(f"{BASE}/applicant/packages/{package_id}")).text
    case_match = re.search(r"/cases/([0-9a-f-]+)/lupademo/application/", page)
    if not case_match:
        raise SystemExit("case id not found on the Kevytasiointi page")
    case_id = case_match.group(1)
    base = f"/cases/{case_id}/lupademo/application/{PERMIT}"
    wait_job(f"/applicant/packages/{package_id}", "Aineiston valmistelu", 1800, "ingest (kevyt)")
    must(session.post(f"{BASE}/applicant/packages/{package_id}/analyze"), 303, "kevyt analyze")
    wait_job(f"/applicant/packages/{package_id}", "Aineiston analyysi", 1800, "analysis (kevyt)")

    must(session.post(f"{BASE}/applicant/packages/{package_id}/save", data=FORM_DATA), 303, "kevyt save")
    submitted = session.post(f"{BASE}/applicant/packages/{package_id}/submit", data=FORM_DATA)
    log(f"  submit -> {submitted.status_code} {submitted.headers.get('location')}")
    final = must(session.get(f"{BASE}/applicant/packages/{package_id}")).text
    receipt_id = re.search(r"Kuittitunnus\s*<strong>([^<]+)</strong>", final)
    log(f"  receipt: {receipt_id.group(1) if receipt_id else 'missing'}")


if __name__ == "__main__":
    log(f"target: {BASE}")
    health = must(session.get(BASE + "/healthz"), label="healthz")
    log(f"healthz: {health.text.strip()[:200]}")
    started = time.time()
    paths = os.environ.get("KEHA_LV_PATHS", "ab").lower()
    if "b" in paths:
        path_b()
    if "a" in paths:
        path_a()
    log(f"\nlive e2e total {time.time() - started:.0f}s (paths={paths})")
