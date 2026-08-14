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
from app.domains.lupademo.services import artifact_json, find_material_package  # noqa: E402
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
        self.assertNotIn("P2X:n vetylaitos", dashboard.text)
        self.assertEqual(client.post("/logout").status_code, 303)
        self.assertEqual(client.post("/login", data={"customer_id": "ren-gas"}).status_code, 303)
        self.assertIn("Ren-Gas biokaasulaitos", client.get("/").text)

    def test_case_urls_are_customer_scoped(self) -> None:
        client.post("/login", data={"customer_id": "ren-gas"})
        other_case_id = db.list_cases_for_customer("p2x")[0]["id"]
        self.assertEqual(client.get(f"/projects/{other_case_id}").status_code, 403)

    def test_package_upload_does_not_create_another_hanke(self) -> None:
        client.post("/login", data={"customer_id": "ren-gas"})
        before = len(db.list_cases_for_customer("ren-gas"))
        response = client.post(
            "/applicant/packages/new",
            data={
                "package_name": "Ren-Gas aineisto",
                "permit_type": "environmental_industrial",
                "municipality": "Vantaa",
                "text_body": "Biokaasulaitoksen kuvaus.",
            },
        )
        self.assertEqual(response.status_code, 303)
        package_id = response.headers["location"].rsplit("/", 1)[-1]
        self.assertEqual(len(db.list_cases_for_customer("ren-gas")), before)
        review_id = find_material_package(db, package_id)["case_id"]
        self.assertIsNone(db.get_case(review_id))
        self.assertIsNone(db.get_artifact(review_id, "permit_application", "environmental_industrial"))
        self.assertEqual(client.get(response.headers["location"]).status_code, 200)
        self.assertEqual(client.post(f"/applicant/packages/{package_id}/analyze").status_code, 303)
        self.assertIn("Aloita uusi hanke tarkastuksen pohjalta", client.get(response.headers["location"]).text)
        promoted = client.post(f"/applicant/packages/{package_id}/promote")
        self.assertEqual(promoted.status_code, 303)
        target_case = promoted.headers["location"].split("/cases/", 1)[1].split("/", 1)[0]
        self.assertEqual(len(db.list_cases_for_customer("ren-gas")), before + 1)
        self.assertIsNotNone(db.get_artifact(target_case, "permit_application", "environmental_industrial"))

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


if __name__ == "__main__":
    unittest.main()
