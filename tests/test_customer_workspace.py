"""Customer login, ownership, and hierarchy regression tests."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ["KEHA_LV_FORCE_MOCK_LLM"] = "true"
os.environ["KEHA_LV_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="keha-customer-"), "test.db")
os.environ["KEHA_LV_UPLOADS_DIR"] = tempfile.mkdtemp(prefix="keha-customer-uploads-")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.domains.lupademo.routes import create_lupademo_router  # noqa: E402
from app.domains.lupademo.services import artifact_json, find_material_package, save_artifact  # noqa: E402
from app.domains.lupademo.services.customer_workspace import ensure_customer_demo_data  # noqa: E402
from app.infra.config import load_settings  # noqa: E402
from app.infra.db import DB  # noqa: E402
from app.infra.session import DemoSessionMiddleware  # noqa: E402


settings = load_settings()
Path(settings.uploads_dir).mkdir(parents=True, exist_ok=True)
db = DB(settings.db_path)
ensure_customer_demo_data(db)
app = FastAPI(title="KEHA LV customer workspace tests")
app.add_middleware(
    DemoSessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="lv_session",
    same_site="lax",
)
app.include_router(create_lupademo_router(lambda: db, lambda: settings))
client = TestClient(app, follow_redirects=False)


class CustomerWorkspace(unittest.TestCase):
    def setUp(self) -> None:
        client.post("/logout")

    def test_login_dashboard_logout_and_relogin(self) -> None:
        self.assertEqual(client.get("/").status_code, 303)
        self.assertEqual(client.post("/login", data={"customer_id": "ren-gas"}).status_code, 303)
        dashboard = client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("Ren-Gas Oy", dashboard.text)
        self.assertIn("Uusi asiointi", dashboard.text)
        self.assertIn("Hankkeet", dashboard.text)
        self.assertNotIn("Hallitse hankkeita ja jatka keskeneräisiä asiointeja", dashboard.text)
        self.assertNotIn("Tehtävät", dashboard.text)
        self.assertNotIn("P2X:n vetylaitos", dashboard.text)
        self.assertEqual(client.post("/logout").status_code, 303)
        self.assertEqual(client.post("/login", data={"customer_id": "ren-gas"}).status_code, 303)
        self.assertIn("Ren-Gas biokaasulaitos", client.get("/").text)

    def test_case_urls_are_customer_scoped(self) -> None:
        client.post("/login", data={"customer_id": "ren-gas"})
        other_case_id = db.list_cases_for_customer("p2x")[0]["id"]
        self.assertEqual(client.get(f"/projects/{other_case_id}").status_code, 403)

    def test_hanke_page_prioritizes_applications_and_next_action(self) -> None:
        client.post("/login", data={"customer_id": "ren-gas"})
        case_id = next(
            case["id"]
            for case in db.list_cases_for_customer("ren-gas")
            if "environmental_industrial" in artifact_json(db, case["id"], "lv_project", "main", {}).get("permits", [])
        )
        page = client.get(f"/lv/projects/{case_id}")
        self.assertEqual(page.status_code, 200)
        self.assertIn('aria-label="Murupolku"', page.text)
        self.assertIn("Hakemukset", page.text)
        self.assertEqual(page.text.count("Jatka hakemusta"), 1)
        self.assertIn("Luo hakemus", page.text)
        self.assertNotIn("hanke-status-bar", page.text)
        self.assertNotIn("Seuraavaksi", page.text)
        self.assertNotIn("Lisätiedot", page.text)
        self.assertNotIn("Hankkeen perustiedot", page.text)
        self.assertIn("Hankkeen tiedot", page.text)

    def test_seeded_submitted_applications_are_visible_in_authority_queue(self) -> None:
        response = client.get("/authority/queue")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Ei hakemuksia valituilla rajauksilla", response.text)
        self.assertEqual(response.text.count('class="row-action'), 3)

    def test_existing_seeded_submissions_rebuild_missing_queue_items(self) -> None:
        for case in db.list_cases():
            project = artifact_json(db, case["id"], "lv_project", "main", {})
            if project.get("seeded_customer_workspace"):
                save_artifact(db, case["id"], "authority_queue", "main", {"items": []})

        ensure_customer_demo_data(db)

        response = client.get("/authority/queue")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text.count('class="row-action'), 3)

    def test_package_upload_does_not_create_another_hanke(self) -> None:
        """Kevytasiointi (Polku 2): LV-side entry creates a real hanke directly."""
        client.post("/login", data={"customer_id": "ren-gas"})
        before = len(db.list_cases_for_customer("ren-gas"))
        response = client.post(
            "/applicant/packages/new",
            data={
                "project_name": "Ren-Gas biokaasuhanke",
                "permit_type": "environmental_industrial",
                "municipality": "Vantaa",
                "text_body": "Biokaasulaitoksen kuvaus.",
            },
        )
        self.assertEqual(response.status_code, 303)
        package_id = response.headers["location"].rsplit("/", 1)[-1]
        self.assertEqual(len(db.list_cases_for_customer("ren-gas")), before + 1)
        found = find_material_package(db, package_id)
        case_id = found["case_id"]
        self.assertIsNotNone(db.get_case(case_id))
        self.assertIsNotNone(db.get_artifact(case_id, "permit_application", "environmental_industrial"))
        workbench = client.get(response.headers["location"])
        self.assertEqual(workbench.status_code, 200)
        self.assertIn("Kevyt integraatio", workbench.text)

        # AI-prose endpoint returns JSON modal payload
        prose_resp = client.post(f"/applicant/packages/{package_id}/ai-prose")
        self.assertEqual(prose_resp.status_code, 200)
        prose_data = prose_resp.json()
        self.assertIn("prose_md", prose_data)
        workbench_after = client.get(f"/applicant/packages/{package_id}")
        self.assertIn("Avaa suositukset", workbench_after.text)
        self.assertIn("kevyt-material__actions", workbench_after.text)
        self.assertIn('target="_blank"', workbench_after.text)
        self.assertNotIn('class="kevyt-panel"', workbench_after.text)
        self.assertNotIn('data-open-suggestions aria-disabled="true"', workbench_after.text)
        suggestions_page = client.get(f"/applicant/packages/{package_id}/suggestions")
        self.assertEqual(suggestions_page.status_code, 200)
        self.assertIn("Tekoälyapurin suositukset", suggestions_page.text)
        self.assertNotIn("Kopioi", suggestions_page.text)

        # Save and verify save works without intermediate promote
        save_resp = client.post(
            f"/applicant/packages/{package_id}/save",
            data={"activity_name": "Biokaasulaitos"},
        )
        self.assertEqual(save_resp.status_code, 303)
        saved_view = client.get(save_resp.headers["location"])
        self.assertEqual(saved_view.status_code, 200)
        self.assertIn("Biokaasulaitos", saved_view.text)

    def test_kevytasiointi_links_point_to_service_view_not_wizard(self) -> None:
        """Decision 2026-08-27: kevytasiointi hankkeet link to /applicant/packages/{pid},
        never the wizard — dashboard rows, next-task and project_url included."""
        from app.domains.lupademo.services import customer_workspace as customer_ws

        client.post("/login", data={"customer_id": "ren-gas"})
        response = client.post(
            "/applicant/packages/new",
            data={
                "project_name": "Ren-Gas kevytlinkkihanke",
                "permit_type": "environmental_industrial",
                "municipality": "Vantaa",
                "text_body": "Biokaasulaitoksen kuvaus.",
            },
        )
        self.assertEqual(response.status_code, 303)
        package_id = response.headers["location"].rsplit("/", 1)[-1]
        case_id = find_material_package(db, package_id)["case_id"]
        service_url = f"/applicant/packages/{package_id}"

        # Service-level rows
        rows = customer_ws.case_application_rows(db, case_id)
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["application_url"], service_url)

        # Dashboard: case row project_url + next-task url
        dashboard = customer_ws.customer_dashboard(db, "ren-gas")
        case_row = next(item for item in dashboard["cases"] if item["case_id"] == case_id)
        self.assertEqual(case_row["project_url"], service_url)
        task_urls = [task["url"] for task in dashboard["tasks"] if task["case_id"] == case_id]
        for url in task_urls:
            self.assertEqual(url, service_url)

        # Rendered dashboard HTML contains the service URL, not a wizard URL for this hanke
        page = client.get("/")
        self.assertIn(service_url, page.text)

    def test_ai_prose_endpoint_returns_prose_and_fields(self) -> None:
        """Kevytasiointi side panel payload: non-empty prose_md plus fields list (mock mode)."""
        client.post("/login", data={"customer_id": "ren-gas"})
        response = client.post(
            "/applicant/packages/new",
            data={
                "project_name": "Ren-Gas prose-testihanke",
                "permit_type": "environmental_industrial",
                "municipality": "Vantaa",
                "text_body": "Biokaasulaitoksen kuvaus. Toiminnanharjoittaja Ren-Gas Oy.",
            },
        )
        self.assertEqual(response.status_code, 303)
        package_id = response.headers["location"].rsplit("/", 1)[-1]

        prose_resp = client.post(f"/applicant/packages/{package_id}/ai-prose")
        self.assertEqual(prose_resp.status_code, 200)
        data = prose_resp.json()
        self.assertTrue(data.get("prose_md"))
        self.assertIsInstance(data.get("fields"), list)

    def test_anonymous_admin_mutation_is_forbidden(self) -> None:
        """F28: case-scoped criteria mutations require the owning customer's session."""
        other_case_id = db.list_cases_for_customer("p2x")[0]["id"]
        response = client.post(
            f"/cases/{other_case_id}/lupademo/admin/criteria/rule",
            data={
                "permit_type": "building_residential",
                "rule_id": "evil_rule",
                "instruction": "anonyymi saanto",
                "severity": "major",
            },
        )
        self.assertEqual(response.status_code, 403)
        history = artifact_json(db, other_case_id, "criteria_history", "building_residential", {})
        self.assertFalse(history, "anonymous mutation must not write criteria artifacts")

    def test_owner_can_post_admin_criteria_rule(self) -> None:
        """F28 positive case: logged-in owner can still mutate their case's criteria."""
        client.post("/login", data={"customer_id": "p2x"})
        own_case_id = db.list_cases_for_customer("p2x")[0]["id"]
        response = client.post(
            f"/cases/{own_case_id}/lupademo/admin/criteria/rule",
            data={
                "permit_type": "building_residential",
                "rule_id": "owner_rule_1",
                "instruction": "omistajan saanto",
                "severity": "minor",
            },
        )
        self.assertEqual(response.status_code, 303)

    def test_anonymous_unknown_case_gets_404_without_artifacts(self) -> None:
        """F25b: anonymous GET of a nonexistent case must not render or create artifacts."""
        fake = "00000000-0000-0000-0000-000000000000"
        response = client.get(f"/cases/{fake}/lupademo/application/building_residential/wizard/1")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(artifact_json(db, fake, "lv_project", "main", {}), {})
        self.assertEqual(artifact_json(db, fake, "permit_application", "building_residential", {}), {})

    def test_new_hanke_inherits_customer_profile(self) -> None:
        client.post("/login", data={"customer_id": "sharpcell"})
        response = client.post(
            "/projects/new",
            data={
                "project_name": "Uusi Sharpcell hanke",
                "permit_type": "event_public",
                "start_at": "lv_project",
            },
        )
        self.assertEqual(response.status_code, 303)
        case_id = response.headers["location"].rsplit("/", 1)[-1]
        common = artifact_json(db, case_id, "lv_common_data", "main", {})["data"]
        self.assertEqual(common["applicant_name"], "Sharpcell")
        self.assertEqual(common["municipality"], "")
        self.assertEqual(common["address"], "")

    def test_new_hanke_prefills_editable_applicant(self) -> None:
        client.post("/login", data={"customer_id": "ren-gas"})
        form = client.get("/projects/new")
        for field_name in (
            "applicant_name",
            "business_id",
            "contact_person",
            "phone",
            "email",
            "municipality",
            "address",
            "property_id",
            "project_name",
        ):
            self.assertIn(f'name="{field_name}"', form.text)
        for value in ("Ren-Gas Oy", "0000000-2", "Mikko Mäki", "+358 40 000 2002", "mikko.maki@ren-gas.example"):
            self.assertIn(f'value="{value}"', form.text)
        self.assertNotIn('type="hidden" name="applicant_name"', form.text)
        self.assertNotIn(">Yritys</span>", form.text)

        response = client.post(
            "/projects/new",
            data={
                "project_name": "Muokattu hakija hanke",
                "permit_type": "building_residential",
                "applicant_name": "Muokattu hakija Oy",
                "business_id": "9999999-9",
                "contact_person": "Uusi yhteyshenkilö",
                "phone": "+358 50 123 4567",
                "email": "uusi@example.fi",
                "municipality": "Kerava",
                "address": "Uusi osoite 1",
                "property_id": "123-456-7-89",
                "start_at": "lv_project",
            },
        )
        case_id = response.headers["location"].rsplit("/", 1)[-1]
        common = artifact_json(db, case_id, "lv_common_data", "main", {})["data"]
        self.assertEqual(common["applicant_name"], "Muokattu hakija Oy")
        self.assertEqual(common["business_id"], "9999999-9")
        self.assertEqual(common["contact_person"], "Uusi yhteyshenkilö")
        self.assertEqual(common["phone"], "+358 50 123 4567")
        self.assertEqual(common["email"], "uusi@example.fi")
        self.assertEqual(common["municipality"], "Kerava")
        self.assertEqual(common["address"], "Uusi osoite 1")
        self.assertEqual(common["property_id"], "123-456-7-89")


if __name__ == "__main__":
    unittest.main()
