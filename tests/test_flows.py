"""End-to-end user journey tests for the applicant wizard and authority workflow.

Complements test_smoke.py (which focuses on agent/API contracts). These tests
walk the full user paths through the HTTP layer:

- Applicant: create project -> save common data (step 1) -> upload material
  (step 2) -> run check (step 4) -> submit (step 5) -> receipt (step 6).
- Authority: queue -> detail -> update observation status -> update status.

Stdlib unittest on purpose: no pytest dependency.
    .venv/bin/python -m unittest discover tests
"""

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root

os.environ["KEHA_LV_FORCE_MOCK_LLM"] = "true"

from fastapi import FastAPI  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.domains.lupademo.services as svc  # noqa: E402
from app.domains.lupademo.routes import create_lupademo_router  # noqa: E402
from app.infra.config import load_settings  # noqa: E402
from app.infra.db import DB  # noqa: E402

# Build a fully isolated app + DB so these journey tests never touch the
# shared `app.main` state that test_smoke.py relies on (which asserts exact
# attachment counts on its demo case).
os.environ["KEHA_LV_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="keha-flows-"), "test.db")
os.environ["KEHA_LV_UPLOADS_DIR"] = tempfile.mkdtemp(prefix="keha-flows-uploads-")
_flow_settings = load_settings()
Path(_flow_settings.uploads_dir).mkdir(parents=True, exist_ok=True)

_flow_db = DB(_flow_settings.db_path)
app = FastAPI(title="KEHA LV AI-demo (flow tests)")
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).resolve().parents[1] / "app" / "domains" / "lupademo" / "static")),
    name="static",
)
app.include_router(create_lupademo_router(lambda: _flow_db, lambda: _flow_settings))

PERMIT = "environmental_industrial"
SAMPLE = "Biokaasulaitos Vantaalla, kasittelymaara 18000 t/a, toiminnanharjoittaja Oy Testi Ab."

client = TestClient(app, follow_redirects=False)
db: DB
case: str
base: str


def setUpModule() -> None:
    """Create a fresh, dedicated case for the journey tests."""
    global db, case, base
    db = _flow_db
    case = db.create_case("Flow journey test", language="fi")
    svc.ensure_seed_state(db, case)
    base = f"/cases/{case}/lupademo/application/{PERMIT}"


class ApplicantJourney(unittest.TestCase):
    """Full applicant path: common data -> materials -> check -> submit -> receipt."""

    def test_two_entry_paths_are_distinct(self) -> None:
        """Path A (package workbench) and Path B (wizard) must stay separate.

        Guard against mix-match: the two entry routes render different templates
        and must not be conflated. See AGENTS.md -> 'Two entry paths'.
        """
        # Path A: material-first package workbench entry
        workbench_entry = client.get("/applicant/packages/new")
        self.assertEqual(workbench_entry.status_code, 200)
        self.assertIn("Lataa valmis hakemuspaketti AI-tarkistukseen", workbench_entry.text)
        self.assertNotIn("Hankkeen perustiedot", workbench_entry.text)

        # Path B: form-first wizard entry
        wizard_entry = client.get("/projects/new")
        self.assertEqual(wizard_entry.status_code, 200)
        self.assertIn("Hankkeen perustiedot", wizard_entry.text)
        self.assertNotIn("Lataa valmis hakemuspaketti AI-tarkistukseen", wizard_entry.text)

    def test_step1_saves_common_data_and_redirects(self) -> None:
        response = client.post(
            f"{base}/save",
            data={
                "redirect_to": f"{base}/wizard/2",
                "activity_name": "Biokaasulaitos",
                "activity_description": "Biokaasun tuotanto",
            },
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], f"{base}/wizard/2")

        app_data = svc.artifact_json(db, case, "permit_application", PERMIT, {})
        self.assertEqual(app_data["form_data"].get("activity_name"), "Biokaasulaitos")

    def test_step2_upload_attachment_and_scope(self) -> None:
        upload = client.post(
            f"{base}/attachments/upload",
            files={"file": ("hakemus.txt", io.BytesIO(SAMPLE.encode()), "text/plain")},
            data={"delivery_scope": "official_submission"},
        )
        self.assertEqual(upload.status_code, 303)

        attachments = svc.list_attachments(db, case, PERMIT)
        self.assertEqual(len(attachments), 1)
        attachment_id = attachments[0]["id"]

        # Re-scope to AI-context-only and back
        rescope = client.post(
            f"{base}/attachments/{attachment_id}/scope",
            data={"delivery_scope": "ai_context_only"},
        )
        self.assertEqual(rescope.status_code, 303)
        self.assertEqual(svc.list_attachments(db, case, PERMIT)[0]["delivery_scope"], "ai_context_only")

    def test_step4_check_runs_and_writes_report(self) -> None:
        response = client.post(f"{base}/ai/check", data={})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], f"{base}/wizard/4#check")

        report = svc.artifact_json(db, case, "check_report", PERMIT, {})
        self.assertIsInstance(report.get("issues"), list)

    def test_submit_is_blocked_by_blocking_issues(self) -> None:
        """An incomplete application must be blocked at the submit gate."""
        response = client.post(f"{base}/submit", data={})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], f"{base}/wizard/5#check-blocking")

    def test_wizard_steps_all_render(self) -> None:
        for step in range(1, 7):
            with self.subTest(step=step):
                response = client.get(f"{base}/wizard/{step}")
                self.assertEqual(response.status_code, 200)
                self.assertIn(f"Vaihe {step} / 6", response.text)


class AuthorityJourney(unittest.TestCase):
    """Caseworker path: queue -> detail -> observations -> status update."""

    def _seed_queue_item(self) -> None:
        """Create a submitted application so the authority queue has an item."""
        queue = svc.artifact_json(db, case, "authority_queue", "main", {"items": []})
        queue.setdefault("items", []).append(
            {
                "application_id": PERMIT,
                "application_title": "Ympäristölupa / teollinen uusiutuvan energian toiminta",
                "receipt_id": "LV-TEST-000001",
                "applicant_name": "Testi Hakija",
                "municipality": "Vantaa",
                "status": "käsittelyssä",
                "submitted_at": "2026-08-03 10:00:00",
                "quality_score": 75,
                "open_issue_count": 0,
                "info_request_risk": "matala",
                "permit_type": PERMIT,
                "processing_stage": "vastaanotettu",
                "sla_due_at": "2026-09-02",
                "urgency": "normaali",
            }
        )
        svc.save_artifact(db, case, "authority_queue", "main", queue)

    def test_queue_renders_and_contains_case(self) -> None:
        self._seed_queue_item()
        response = client.get("/authority/queue")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Työjono", response.text)
        self.assertIn("Testi Hakija", response.text)

    def test_detail_renders_authority_panels(self) -> None:
        response = client.get(f"/cases/{case}/lupademo/authority/applications/{PERMIT}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("AI-yhteenveto", response.text)
        self.assertIn("Tarkista seuraavaksi", response.text)

    def test_generate_authority_summary(self) -> None:
        response = client.post(
            f"/cases/{case}/lupademo/authority/applications/{PERMIT}/ai/summary",
            data={},
        )
        self.assertEqual(response.status_code, 303)
        summary = svc.artifact_json(db, case, "authority_summary", PERMIT, {})
        self.assertTrue(summary.get("summary_md"))

    def test_update_authority_status(self) -> None:
        self._seed_queue_item()
        response = client.post(
            f"/cases/{case}/lupademo/authority/applications/{PERMIT}/status",
            data={"status": "in_review"},
        )
        self.assertEqual(response.status_code, 303)

        queue = svc.artifact_json(db, case, "authority_queue", "main", {"items": []})
        item = next(i for i in queue["items"] if i.get("application_id") == PERMIT)
        self.assertEqual(item["status"], "in_review")

    def test_observation_status_update(self) -> None:
        panel = svc.caseworker_observation_panel(db, case, PERMIT)
        observations = panel.get("top_ai_observations") or []
        if not observations:
            self.skipTest("No observations available to update")
        observation_id = observations[0]["id"]

        response = client.post(
            f"/cases/{case}/lupademo/authority/applications/{PERMIT}/observations/{observation_id}/status",
            data={"status": "accepted", "note": "Käsitelty"},
        )
        self.assertEqual(response.status_code, 303)

        decisions = svc.artifact_json(db, case, "caseworker_observation_decisions", PERMIT, {"items": {}})
        self.assertEqual(decisions["items"][observation_id]["status"], "accepted")


if __name__ == "__main__":
    unittest.main()
