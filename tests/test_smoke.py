"""Regression guards for the bugs fixed in the 2026-07 cleanup.

Stdlib unittest on purpose: no pytest dependency.
    .venv/bin/python -m unittest discover tests
"""

import io
import os
import tempfile
import unittest

os.environ["KEHA_LV_FORCE_MOCK_LLM"] = "true"
os.environ["KEHA_LV_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="keha-test-"), "test.db")

from fastapi.testclient import TestClient  # noqa: E402

import app.domains.lupademo.security as security  # noqa: E402
import app.domains.lupademo.services as svc  # noqa: E402
from app.core.db import DB  # noqa: E402
from app.main import app  # noqa: E402

PERMIT = "environmental_industrial"
KEY = {"X-Lupademo-Api-Key": "demo-avi-key"}
SAMPLE = "Biokaasulaitos Vantaalla, kasittelymaara 18000 t/a, toiminnanharjoittaja Oy Testi Ab."

client = TestClient(app, follow_redirects=False)
db: DB
case: str
base: str


def setUpModule() -> None:
    """Run the applicant flow once; the agent tests all read its artifacts."""
    global db, case, base
    db = DB(os.environ["KEHA_LV_DB_PATH"])
    case = svc.ensure_demo_case(db)
    base = f"/cases/{case}/lupademo/application/{PERMIT}"
    client.post(
        f"{base}/attachments/upload",
        files={"file": ("hakemus.txt", io.BytesIO(SAMPLE.encode()), "text/plain")},
        data={"delivery_scope": "official_submission"},
    )
    client.post(f"{base}/ai/check", data={})
    client.post(f"{base}/ai/structure-from-attachments", data={})


class AgentPipeline(unittest.TestCase):
    """Every run_json_agent call used to raise; artifacts must be real dicts."""

    def test_check_report_is_a_populated_dict(self) -> None:
        report = svc.artifact_json(db, case, "check_report", PERMIT, {})
        self.assertIsInstance(report, dict)
        self.assertTrue(report["issues"], "agent produced no issues")
        self.assertTrue(report["checked_at"])
        self.assertIsInstance(report["issues"][0], dict, "issues must be dicts, not dataclasses")

    def test_attachment_was_classified_and_text_extracted(self) -> None:
        attachments = svc.list_attachments(db, case, PERMIT)
        self.assertEqual(len(attachments), 1)
        self.assertTrue(attachments[0]["primary_type"])
        self.assertGreater(attachments[0]["text_chars"], 0)

    def test_material_package_documents_are_dicts(self) -> None:
        package = svc.current_material_package(db, case, PERMIT)
        self.assertIsInstance(package, dict)
        self.assertTrue(package["documents"])
        self.assertIsInstance(package["documents"][0], dict)

    def test_authority_summary_renders_markdown(self) -> None:
        summary = svc.authority_summary(db, case, PERMIT, settings=None)
        self.assertIsInstance(summary, dict)
        self.assertTrue(summary["summary_md"])


class PublicApi(unittest.TestCase):
    def _check(self, **overrides: object) -> dict:
        payload = {
            "permit_type": PERMIT,
            "form_schema": svc.load_form_schema(PERMIT),
            "form_data": {"activity_name": "Biokaasulaitos"},
            "request_id": "test-1",
        }
        payload.update(overrides)
        return payload

    def test_check_response_is_annotated_with_tenant(self) -> None:
        body = client.post("/api/v1/checks/application", json=self._check(), headers=KEY).json()
        self.assertEqual(body["tenant_id"], "avi_demo")
        self.assertTrue(body["criteria_version"].endswith(":avi-v1"))
        self.assertTrue(body["issues"])
        self.assertIn("model_audit", body)

    def test_field_suggestion_carries_evidence(self) -> None:
        body = client.post(
            "/api/v1/suggestions/field",
            json={
                "permit_type": PERMIT,
                "field_id": "activity_name",
                "form_schema": svc.load_form_schema(PERMIT),
                "form_data": {},
                "request_id": "test-2",
            },
            headers=KEY,
        ).json()
        self.assertTrue(body["suggestions"])
        self.assertTrue(body["suggestions"][0]["evidence_refs"])

    def test_route_fills_in_schema_the_caller_omitted(self) -> None:
        """The route resolved request_id and form_schema then threw them away."""
        response = client.post(
            "/api/v1/applications:precheck",
            json={"permit_type": PERMIT, "request_id": "test-3"},
            headers=KEY,
        )
        self.assertEqual(response.status_code, 200)


class Authentication(unittest.TestCase):
    def test_missing_key_is_rejected(self) -> None:
        response = client.post("/api/v1/applications:precheck", json={"permit_type": PERMIT})
        self.assertEqual(response.status_code, 401)

    def test_unknown_key_is_rejected(self) -> None:
        response = client.post(
            "/api/v1/applications:precheck",
            json={"permit_type": PERMIT},
            headers={"X-Lupademo-Api-Key": "bogus"},
        )
        self.assertEqual(response.status_code, 403)

    def test_non_ascii_key_is_rejected_not_crashed(self) -> None:
        """hmac.compare_digest raises TypeError on non-ASCII str; headers arrive latin-1 decoded.

        Tested below the HTTP layer because httpx refuses to send such a header at all.
        """
        for key in ("\u00e4-non-ascii", "\u043a\u043b\u044e\u0447", "demo-avi-ke\u00ff"):
            with self.subTest(key=key):
                self.assertIsNone(security._get_client_by_key(key))

    def test_valid_key_still_resolves(self) -> None:
        self.assertEqual(security._get_client_by_key("demo-avi-key")["tenant_id"], "avi_demo")

    def test_cross_tenant_payload_is_rejected(self) -> None:
        """This check was dead code: the assignment ran before the comparison."""
        response = client.post(
            "/api/v1/applications:precheck",
            json={"permit_type": PERMIT, "tenant_id": "vantaa_demo"},
            headers=KEY,
        )
        self.assertEqual(response.status_code, 403)

    def test_malformed_body_is_422_not_500(self) -> None:
        """These used to raise TypeError inside Model(**payload) and surface as 500."""
        for path in (
            "/api/v1/suggestions/field",
            "/api/v1/suggestions/form",
            "/api/v1/checks/application",
        ):
            with self.subTest(path=path):
                response = client.post(path, json={"junk": True}, headers=KEY)
                self.assertEqual(response.status_code, 422)

    def test_precheck_defaults_missing_fields_instead_of_failing(self) -> None:
        """precheck fills permit_type/form_schema itself, so junk keys are dropped, not fatal."""
        response = client.post("/api/v1/applications:precheck", json={"junk": True}, headers=KEY)
        self.assertEqual(response.status_code, 200)


class Uploads(unittest.TestCase):
    def _upload(self, filename: str) -> int:
        return client.post(
            f"{base}/attachments/upload",
            files={"file": (filename, io.BytesIO(b"data"), "text/plain")},
            data={"delivery_scope": "official_submission"},
        ).status_code

    def test_executable_extension_is_rejected(self) -> None:
        self.assertEqual(self._upload("payload.exe"), 422)

    def test_path_traversal_filename_is_sanitised(self) -> None:
        self.assertEqual(self._upload("../../etc/passwd.txt"), 303)
        self.assertFalse(os.path.exists("/etc/passwd.txt"))


class Pages(unittest.TestCase):
    def test_every_demo_page_renders(self) -> None:
        paths = [
            "/healthz",
            "/",
            "/applicant",
            "/projects/new",
            "/demo/scenarios",
            f"/cases/{case}/lupademo/metrics",
            f"/cases/{case}/lupademo/admin/audit-trail",
            f"{base}/package",
            f"/cases/{case}/lupademo/authority/applications/{PERMIT}",
            *[f"{base}/wizard/{step}" for step in range(1, 7)],
        ]
        for path in paths:
            with self.subTest(path=path):
                response = client.get(path)
                self.assertIn(response.status_code, (200, 303))
                self.assertNotIn("Traceback", response.text)


if __name__ == "__main__":
    unittest.main()
