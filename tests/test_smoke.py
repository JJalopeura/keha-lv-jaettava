"""Regression guards for the bugs fixed in the 2026-07 cleanup.

Stdlib unittest on purpose: no pytest dependency.
    .venv/bin/python -m unittest discover tests
"""

import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root, so `import app...` works from any cwd

os.environ["KEHA_LV_FORCE_MOCK_LLM"] = "true"
os.environ["KEHA_LV_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="keha-test-"), "test.db")

from fastapi.testclient import TestClient  # noqa: E402

import app.domains.lupademo.security as security  # noqa: E402
import app.domains.lupademo.services as svc  # noqa: E402
from app.core.azure_client import AzureOpenAIClient  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.core.db import DB  # noqa: E402
from app.core.llm_factory import create_llm_client  # noqa: E402
from app.core.llm_utils import call_llm_json, vector_store_ids  # noqa: E402
from app.core.mock_llm import MockLLMClient  # noqa: E402
from app.domains.lupademo.services.application import deterministic_suggestion  # noqa: E402
from app.domains.lupademo.services.intake import azure_vector_store_ids, build_retrieval_context  # noqa: E402
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

    def test_field_suggestion_agent_loads_its_strict_prompt(self) -> None:
        prompt = svc.render_agent_prompt(
            "field_suggestion_agent",
            {
                "request_id": "req_test",
                "criteria_version": "v1",
                "field_id": "activity_description",
                "field_definition": {"id": "activity_description"},
                "form_data": {},
                "common_data": {},
                "criteria": {},
                "retrieved_context": {},
            },
        )

        self.assertIn('"suggestions"', prompt)
        self.assertIn('"field_id": "activity_description"', prompt)

    def test_authority_agents_load_their_authored_prompts(self) -> None:
        cases = {
            "caseworker_summary_agent": ("neutraali yhteenveto", {"form_data": "{}", "context": "{}"}),
            "additional_info_request_agent": ("täydennyspyyntöluonnos", {"check_report": "{}", "form_data": "{}"}),
            "authority_memo_draft_agent": ("valmistelumuistion luonnostelija", {"context": "{}"}),
        }

        for agent, (expected, values) in cases.items():
            with self.subTest(agent=agent):
                self.assertIn(expected, svc.render_agent_prompt(agent, values))

    def test_field_suggestion_agent_normalizes_suggestions(self) -> None:
        payload = svc.normalize_agent_payload(
            svc.FieldSuggestionResponse,
            {
                "request_id": "req_test",
                "criteria_version": "v1",
                "suggestions": [{"field_id": "activity_description", "suggested_value": "Test value"}],
            },
        )

        self.assertIsInstance(payload["suggestions"][0], svc.Suggestion)

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

    def test_unsupported_fallback_is_not_presented_as_ai_output(self) -> None:
        suggestion = deterministic_suggestion(PERMIT, "process_description", {}, [])
        self.assertTrue(suggestion.fallback_used)
        self.assertEqual(suggestion.suggested_value, "")
        self.assertTrue(suggestion.warnings)

    def test_permit_specific_suggestions_are_supported_for_every_permit_type(self) -> None:
        fields = {
            "building_residential": "project_description",
            "environmental_industrial": "activity_description",
            "event_public": "rescue_plan",
        }

        for permit_type, field_id in fields.items():
            with self.subTest(permit_type=permit_type):
                schema_field_ids = {field["id"] for field in svc.load_form_schema(permit_type)["fields"]}
                self.assertIn(field_id, schema_field_ids)

                suggestion = deterministic_suggestion(permit_type, field_id, {}, [])
                self.assertEqual(suggestion.field_id, field_id)
                self.assertTrue(suggestion.suggested_value)
                self.assertGreater(suggestion.confidence, 0)
                self.assertTrue(suggestion.evidence_refs)
                self.assertFalse(suggestion.warnings)

                unsupported = deterministic_suggestion(permit_type, "unsupported_semantic_field", {}, [])
                self.assertEqual(unsupported.suggested_value, "")
                self.assertEqual(unsupported.confidence, 0)
                self.assertTrue(unsupported.fallback_used)
                self.assertFalse(unsupported.evidence_refs)
                self.assertTrue(unsupported.warnings)

    def test_managed_job_completion_is_owned_by_its_executor(self) -> None:
        job = svc.start_ai_job(
            db,
            case,
            PERMIT,
            "test_background",
            "Test background job",
            ["text_extractor"],
            eta_seconds=1,
            managed_background=True,
        )
        store = svc.artifact_json(db, case, "ai_jobs", PERMIT, {"jobs": []})
        stored_job = next(item for item in store["jobs"] if item["id"] == job["id"])
        stored_job["created_at"] = time.time() - 10
        svc.save_artifact(db, case, "ai_jobs", PERMIT, store)

        overdue = next(item for item in svc.list_ai_jobs(db, case, PERMIT) if item["id"] == job["id"])
        self.assertEqual(overdue["status"], "running")
        self.assertEqual(overdue["progress_pct"], 95)

        completed = svc.complete_ai_job(db, case, PERMIT, job["id"], summary="Valmis.")
        self.assertEqual(completed["status"], "completed")

    def test_cancelled_job_stays_cancelled_and_retry_requires_an_operation(self) -> None:
        job = svc.start_ai_job(
            db,
            case,
            PERMIT,
            "test_background",
            "Tenant-owned background job",
            ["text_extractor"],
            managed_background=True,
            tenant_id="avi_demo",
        )

        cancelled = svc.cancel_ai_job(db, job["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        with self.assertRaises(ValueError):
            svc.cancel_ai_job(db, job["id"])

        late_completion = svc.complete_ai_job(db, case, PERMIT, job["id"], summary="Late result")
        self.assertEqual(late_completion["status"], "cancelled")

        with self.assertRaisesRegex(ValueError, "job_retry_not_supported"):
            svc.retry_ai_job(db, job["id"])


class AzureFileUpload(unittest.TestCase):
    def test_upload_uses_direct_files_endpoint(self) -> None:
        client = AzureOpenAIClient("https://example.services.ai.azure.com", "preview", "key", "model")
        calls = []

        def request_json(method: str, path: str, **kwargs: object) -> dict:
            calls.append((method, path, kwargs))
            return {"id": "file_test"}

        client.request_json = request_json  # type: ignore[method-assign]
        with tempfile.NamedTemporaryFile(suffix=".txt") as source:
            source.write(b"permit material")
            source.flush()
            upload_id, file_id = client.upload_file_from_path(source.name)

        self.assertEqual((upload_id, file_id), ("file_test", "file_test"))
        self.assertEqual(calls[0][0:2], ("POST", "/openai/v1/files"))
        self.assertEqual(calls[0][2]["form_data"], {"purpose": "assistants"})


class ProviderContracts(unittest.TestCase):
    def _settings(self, **overrides: object) -> Settings:
        values = {
            "db_path": ":memory:",
            "uploads_dir": tempfile.gettempdir(),
            "llm_provider": "azure",
            "demo_mode": False,
            "azure_openai_host_core": "https://core.openai.azure.com",
            "azure_openai_host_chat": "https://chat.services.ai.azure.com",
            "azure_openai_api_key_core": "core-key",
            "azure_openai_api_key_chat": "chat-key",
            "azure_openai_api_version": "2025-04-01-preview",
            "azure_openai_model_core": "core-model",
            "azure_openai_model_chat": "chat-model",
            "azure_openai_timeout_s_core": 30.0,
            "azure_openai_timeout_s_chat": 45.0,
            "company_vector_store_ids": [],
        }
        values.update(overrides)
        return Settings(**values)

    def test_factory_selects_core_and_chat_configuration(self) -> None:
        settings = self._settings()
        with patch.dict(os.environ, {"KEHA_LV_FORCE_MOCK_LLM": "false"}):
            core = create_llm_client(settings, purpose="core")
            chat = create_llm_client(settings, purpose="chat")

        self.assertIsInstance(core, AzureOpenAIClient)
        self.assertEqual((core.host, core.api_key, core.model, core.timeout_s), (
            settings.azure_openai_host_core,
            settings.azure_openai_api_key_core,
            settings.azure_openai_model_core,
            settings.azure_openai_timeout_s_core,
        ))
        self.assertEqual((chat.host, chat.api_key, chat.model, chat.timeout_s), (
            settings.azure_openai_host_chat,
            settings.azure_openai_api_key_chat,
            settings.azure_openai_model_chat,
            settings.azure_openai_timeout_s_chat,
        ))

    def test_factory_force_mock_overrides_azure(self) -> None:
        with patch.dict(os.environ, {"KEHA_LV_FORCE_MOCK_LLM": "true"}):
            selected = create_llm_client(self._settings())

        self.assertIsInstance(selected, MockLLMClient)

    def test_azure_responses_request_contract(self) -> None:
        captured: dict[str, object] = {}

        def handle(request: httpx.Request) -> httpx.Response:
            captured["request"] = request
            return httpx.Response(200, json={"output": []})

        transport = httpx.MockTransport(handle)
        real_client = httpx.Client
        azure = AzureOpenAIClient(
            "https://example.openai.azure.com",
            "2025-04-01-preview",
            "secret-key",
            "test-model",
            timeout_s=12.0,
        )
        with patch("app.core.azure_client.httpx.Client", side_effect=lambda **kwargs: real_client(transport=transport, **kwargs)):
            azure.responses(input_text="test", response_format="json_object", max_output_tokens=50)

        request = captured["request"]
        self.assertIsInstance(request, httpx.Request)
        self.assertEqual(request.url.path, "/openai/responses")
        self.assertEqual(request.url.params["api-version"], "2025-04-01-preview")
        self.assertEqual(request.headers["api-key"], "secret-key")
        self.assertEqual(json.loads(request.content)["model"], "test-model")

    def test_azure_http_error_exposes_status_and_service_message(self) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(429, json={"error": {"message": "rate limited"}})
        )
        real_client = httpx.Client
        azure = AzureOpenAIClient("https://example.services.ai.azure.com", "preview", "key", "model")

        with patch("app.core.azure_client.httpx.Client", side_effect=lambda **kwargs: real_client(transport=transport, **kwargs)):
            with self.assertRaisesRegex(RuntimeError, "HTTP 429: rate limited"):
                azure.responses(input_text="test")


class StructuredCallContracts(unittest.TestCase):
    class FakeClient:
        model = "fake-model"

        def __init__(self, *, output: str | None = None, error: Exception | None = None) -> None:
            self.output = output
            self.error = error

        def responses(self, **kwargs: object) -> dict:
            if self.error:
                raise self.error
            if self.output is None:
                return {"output": []}
            return {"output": [{"type": "message", "content": [{"type": "output_text", "text": self.output}]}]}

        def extract_output_text(self, response: dict) -> str | None:
            try:
                return response["output"][0]["content"][0]["text"]
            except (KeyError, IndexError):
                return None

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = DB(str(Path(self.temp_dir.name) / "structured.db"))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_valid_json_is_returned_and_logged(self) -> None:
        result = call_llm_json(
            self.FakeClient(output='{"ok": true}'),
            "synthetic prompt",
            db=self.db,
            case_id="case_test",
            agent="test.agent",
        )

        self.assertEqual(result, {"ok": True})
        log = self.db.list_ai_logs(agent="test.agent")[0]
        self.assertIsNone(log["error"])
        self.assertIsNotNone(log["response_json"])

    def test_missing_output_is_logged_as_error(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "No output_text"):
            call_llm_json(self.FakeClient(), "synthetic prompt", db=self.db, agent="test.missing")

        self.assertIn("No output_text", self.db.list_ai_logs(agent="test.missing")[0]["error"])

    def test_malformed_json_is_logged_as_error(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Failed to parse JSON output"):
            call_llm_json(self.FakeClient(output="not json"), "synthetic prompt", db=self.db, agent="test.invalid")

        self.assertIn("Failed to parse JSON output", self.db.list_ai_logs(agent="test.invalid")[0]["error"])

    def test_provider_failure_is_logged_as_error(self) -> None:
        with self.assertRaisesRegex(TimeoutError, "provider timed out"):
            call_llm_json(
                self.FakeClient(error=TimeoutError("provider timed out")),
                "synthetic prompt",
                db=self.db,
                agent="test.timeout",
            )

        self.assertEqual(self.db.list_ai_logs(agent="test.timeout")[0]["error"], "provider timed out")


class RetrievalContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = DB(str(Path(self.temp_dir.name) / "retrieval.db"))
        self.case_id = self.db.create_case("Retrieval contract")
        self.db.set_case_vector_store(self.case_id, "case-store")
        self.db.set_research_vector_store(self.case_id, "research-store")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_general_vector_store_selection_prioritizes_and_deduplicates(self) -> None:
        self.db.set_app_setting("offerings_vector_store_id", "offerings-store")
        settings = ProviderContracts()._settings(
            company_vector_store_ids=["case-store", "company-store"],
        )

        selected = vector_store_ids(
            "case-store",
            settings,
            self.db,
            include_offerings=True,
            max_ids=2,
        )

        self.assertEqual(selected, ["case-store", "offerings-store"])

    def test_applicant_agent_uses_only_case_and_research_stores(self) -> None:
        settings = ProviderContracts()._settings(
            company_vector_store_ids=["company-a", "company-b"],
        )

        with patch.dict(os.environ, {"LUPADEMO_INCLUDE_COMPANY_VECTOR_STORES": "true"}):
            selected = azure_vector_store_ids(self.db, settings, self.case_id)

        self.assertEqual(selected, ["case-store", "research-store"])

    def test_agent_provider_receives_only_bounded_retrieval_ids(self) -> None:
        settings = ProviderContracts()._settings(
            company_vector_store_ids=["company-a", "company-b"],
        )
        captured_ids = []

        def fake_call(*_args, **kwargs):
            captured_ids.extend(kwargs["vector_store_ids_list"])
            return {"request_id": "retrieval-provider", "extracted_fields": []}

        with (
            patch.dict(os.environ, {"LUPADEMO_INCLUDE_COMPANY_VECTOR_STORES": "true"}),
            patch("app.domains.lupademo.services.intake.call_llm_json", side_effect=fake_call),
        ):
            svc.run_json_agent(
                self.db,
                settings,
                self.case_id,
                "structure_extractor",
                {"request_id": "retrieval-provider", "permit_type": PERMIT, "form_schema": "{}", "source_text": SAMPLE},
                svc.StructureExtractionResponse,
                vector_ids=azure_vector_store_ids(self.db, settings, self.case_id),
            )

        self.assertEqual(captured_ids, ["case-store", "research-store"])
        audit = svc.artifact_json(self.db, self.case_id, "agent_output", "structure_extractor", {})
        self.assertEqual(audit["provider"], "azure")
        self.assertEqual(audit["execution_mode"], "retrieval")
        self.assertFalse(audit["fallback_used"])
        self.assertIsNone(audit["error"])

    def test_applicant_cannot_request_benchmark_or_authority_examples(self) -> None:
        for scope in ("example_case_benchmark", "authority_example"):
            with self.subTest(scope=scope):
                with self.assertRaisesRegex(PermissionError, "benchmark_scope_forbidden"):
                    build_retrieval_context(
                        self.db,
                        self.case_id,
                        PERMIT,
                        "test",
                        [scope],
                        user_role="applicant",
                    )


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

    def test_field_suggestion_has_evidence_or_is_an_honest_fallback(self) -> None:
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
        suggestion = body["suggestions"][0]
        if suggestion["suggested_value"]:
            self.assertTrue(suggestion["evidence_refs"])
        else:
            self.assertTrue(suggestion["fallback_used"])
            self.assertTrue(suggestion["warnings"])

    def test_route_fills_in_schema_the_caller_omitted(self) -> None:
        """The route resolved request_id and form_schema then threw them away."""
        response = client.post(
            "/api/v1/applications:precheck",
            json={"permit_type": PERMIT, "request_id": "test-3"},
            headers=KEY,
        )
        self.assertEqual(response.status_code, 200)

    def test_package_analysis_is_synchronous_and_result_is_tenant_owned(self) -> None:
        response = client.post(
            "/api/v1/application-packages:analyze",
            json={
                "permit_type": PERMIT,
                "request_id": "package-contract",
                "source_text": SAMPLE,
            },
            headers=KEY,
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "succeeded")
        result = client.get(body["result_url"], headers=KEY)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["tenant_id"], "avi_demo")

        other_tenant = {"X-Lupademo-Api-Key": "demo-vantaa-key"}
        for method, path in (
            (client.get, body["result_url"]),
            (client.get, f"/api/v1/jobs/{body['job_id']}"),
            (client.post, f"/api/v1/jobs/{body['job_id']}/cancel"),
            (client.post, f"/api/v1/jobs/{body['job_id']}/retry"),
        ):
            with self.subTest(path=path):
                self.assertEqual(method(path, headers=other_tenant).status_code, 403)

        self.assertEqual(
            client.post(f"/api/v1/jobs/{body['job_id']}/cancel", headers=KEY).status_code,
            409,
        )
        self.assertEqual(
            client.post(f"/api/v1/jobs/{body['job_id']}/retry", headers=KEY).status_code,
            409,
        )

    def test_api_tenants_use_distinct_persisted_cases(self) -> None:
        avi = client.post(
            "/api/v1/field-suggestions",
            json={
                "permit_type": PERMIT,
                "request_id": "avi-isolation",
                "field_id": "activity_description",
            },
            headers=KEY,
        ).json()
        vantaa = client.post(
            "/api/v1/field-suggestions",
            json={
                "permit_type": "building_residential",
                "request_id": "vantaa-isolation",
                "field_id": "project_description",
            },
            headers={"X-Lupademo-Api-Key": "demo-vantaa-key"},
        ).json()

        avi_status = client.get(f"/api/v1/jobs/{avi['job_id']}", headers=KEY).json()
        vantaa_status = client.get(
            f"/api/v1/jobs/{vantaa['job_id']}",
            headers={"X-Lupademo-Api-Key": "demo-vantaa-key"},
        ).json()
        self.assertNotEqual(avi_status["case_id"], vantaa_status["case_id"])
        self.assertEqual(db.get_app_setting("lupademo_api_case_id:avi_demo"), avi_status["case_id"])
        self.assertEqual(db.get_app_setting("lupademo_api_case_id:vantaa_demo"), vantaa_status["case_id"])


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

    def test_wizard_zip_upload_starts_background_ingest_job(self) -> None:
        archive = io.BytesIO()
        import zipfile

        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("hakemus.txt", SAMPLE)
        response = client.post(
            f"{base}/materials/upload-zip",
            files={"zip_file": ("aineisto.zip", archive.getvalue(), "application/zip")},
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], f"{base}/wizard/2#ai-jobs")
        job = next(job for job in svc.list_ai_jobs(db, case, PERMIT) if job["kind"] == "material_ingest")
        self.assertEqual(job["kind"], "material_ingest")
        self.assertTrue(job["package_id"].startswith("pkg_"))

    def test_zip_upload_stops_after_ingest_until_analysis_is_requested(self) -> None:
        original_analysis = svc.execute_intake_analysis_job
        calls = []
        svc.execute_intake_analysis_job = lambda *_args, **_kwargs: calls.append("analysis")
        try:
            archive = io.BytesIO()
            import zipfile

            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("hakemus.txt", SAMPLE)
            client.post(
                f"{base}/materials/upload-zip",
                files={"zip_file": ("aineisto.zip", archive.getvalue(), "application/zip")},
            )
            self.assertEqual(calls, [])

            response = client.post(f"{base}/ai/intake-analysis")
        finally:
            svc.execute_intake_analysis_job = original_analysis

        self.assertEqual(response.status_code, 303)
        self.assertEqual(calls, ["analysis"])

    def test_package_workbench_ignores_another_packages_ingest_job(self) -> None:
        old_package = svc.save_material_package(db, case, PERMIT, "Vanha paketti", [])
        job = svc.start_material_ingest_job(
            db, case, PERMIT, 1, azure_enabled=False, package_id=old_package["package_id"]
        )
        current_package = svc.save_material_package(db, case, PERMIT, "Nykyinen paketti", [])
        try:
            response = client.get(f"/applicant/packages/{current_package['package_id']}")
        finally:
            svc.cancel_ai_job(db, job["id"])

        self.assertEqual(response.status_code, 200)
        self.assertIn("Analysoi hakemuspaketti", response.text)
        self.assertNotIn("Hakukontekstin valmistelu käynnissä", response.text)

    def test_intake_analysis_rejects_duplicate_active_job(self) -> None:
        job = svc.start_intake_analysis_job(db, case, PERMIT, managed_background=True)
        try:
            response = client.post(f"{base}/ai/intake-analysis")
        finally:
            svc.cancel_ai_job(db, job["id"])

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "analysis_already_running")


class Pages(unittest.TestCase):
    def test_form_suggestions_start_visible_background_job(self) -> None:
        original_executor = svc.execute_form_suggestions_job
        svc.execute_form_suggestions_job = lambda *args, **kwargs: None
        try:
            response = client.post(f"{base}/ai/suggest-form")
        finally:
            svc.execute_form_suggestions_job = original_executor

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], f"{base}/wizard/3#ai-jobs")
        job = next(job for job in svc.list_ai_jobs(db, case, PERMIT) if job["kind"] == "form_suggestions")
        self.assertEqual(job["kind"], "form_suggestions")
        self.assertEqual(job["status"], "running")
        self.assertTrue(job["managed_background"])

    def test_wizard_reviews_extraction_suggestions_without_redirecting(self) -> None:
        queue = svc.artifact_json(db, case, "field_extraction_queue", PERMIT, {"items": []})["items"]
        pending_count = sum(item.get("status") == "pending" for item in queue)
        self.assertGreater(pending_count, 0)
        wizard = svc.wizard_step_status(db, case, PERMIT)
        self.assertEqual(wizard["pending_extraction_count"], pending_count)
        self.assertEqual(
            wizard["pending_suggestion_count"],
            wizard["pending_extraction_count"] + wizard["pending_field_suggestion_count"],
        )

        suggestions_page = client.get(f"{base}/wizard/3")
        self.assertEqual(suggestions_page.status_code, 200)
        self.assertIn("Tarkista AI-ehdotukset", suggestions_page.text)
        self.assertIn(f"{pending_count} odottaa käsittelyä", suggestions_page.text)
        self.assertIn("Seuraava: Tarkistus", suggestions_page.text)

        check_page = client.get(f"{base}/wizard/4")
        self.assertEqual(check_page.status_code, 200)
        self.assertIn("AI- ja sääntötarkistus", check_page.text)

    def test_package_workbench_and_wizard_are_independent(self) -> None:
        package_id = svc.current_material_package(db, case, PERMIT)["package_id"]
        workbench = client.get(f"/applicant/packages/{package_id}")
        self.assertEqual(workbench.status_code, 200)
        self.assertIn("Aineistotyöpöytä", workbench.text)
        self.assertNotIn("Vaihe 2 / 6", workbench.text)

        wizard = client.get(f"{base}/wizard/2")
        self.assertEqual(wizard.status_code, 200)
        self.assertIn("Vaihe 2 / 6", wizard.text)
        self.assertNotIn("Aineistotyöpöytä", wizard.text)

    def test_step_two_collapses_help_and_defers_suggestion_review(self) -> None:
        page = client.get(f"{base}/wizard/2")

        self.assertEqual(page.status_code, 200)
        self.assertIn('class="assistant-disclosure"', page.text)
        self.assertIn('class="field-section" open', page.text)
        self.assertNotIn("Pyydä ehdotus", page.text)
        self.assertNotIn("Hyväksy lomakkeelle", page.text)
        self.assertIn("AI-ehdotukset tarkistetaan seuraavassa vaiheessa", page.text)

    def test_front_page_picker_switches_customer_package(self) -> None:
        package = svc.current_material_package(db, case, PERMIT)
        common = svc.artifact_json(db, case, "lv_common_data", "main", {"data": {}})

        page = client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Asiakas ja paketti", page.text)
        self.assertIn(common["data"]["applicant_name"], page.text)
        self.assertIn(package["display_name"], page.text)

        switched = client.post("/workspace/select", data={"workspace": f"{case}:{package['package_id']}"})
        self.assertEqual(switched.status_code, 303)
        self.assertEqual(switched.headers["location"], "/")
        self.assertEqual(db.get_app_setting("lupademo_active_case_id"), case)
        self.assertEqual(db.get_app_setting("lupademo_active_package_id"), package["package_id"])

        selected_page = client.get("/")
        selected_value = f'value="{case}:{package["package_id"]}" selected'
        self.assertIn(selected_value, selected_page.text)

    def test_project_page_uses_shared_branding_without_mock_metadata(self) -> None:
        page = client.get(f"/lv/projects/{case}")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Lupa-apurin etusivu", page.text)
        self.assertNotIn("LV-mock", page.text)
        self.assertNotIn("system-badge", page.text)

    def test_specs_resolve_to_runtime_paths(self) -> None:
        ui = json.loads(Path("docs/specs/ui.json").read_text(encoding="utf-8"))
        roles = json.loads(Path("docs/specs/roles.json").read_text(encoding="utf-8"))
        workflow = json.loads(Path("docs/specs/workflow.json").read_text(encoding="utf-8"))
        self.assertIn("material_package_workbench", workflow["workflows"])
        self.assertIn("deterministic_checks", workflow["workflows"]["applicant_application_check"])
        self.assertIn("use_package_workbench", next(role for role in roles["roles"] if role["key"] == "applicant")["capabilities"])
        for page in ui["pages"]:
            route = page["route"].replace("{case_id}", case)
            with self.subTest(page=page["slug"]):
                self.assertIn(client.get(route).status_code, (200, 303))

    def test_wizard_job_script_is_well_formed(self) -> None:
        wizard = Path("app/domains/lupademo/templates/wizard.html").read_text(encoding="utf-8")
        assistant_start = wizard.index('<aside class="assistant-rail"')
        assistant_end = wizard.index("</aside>", assistant_start)
        assistant = wizard[assistant_start:assistant_end]
        self.assertNotIn("data-ai-jobs-panel", wizard[:assistant_start])
        self.assertIn('data-job-initial-status="{{ assistant_job.status }}"', assistant)
        self.assertIn("data-ai-job", assistant)
        self.assertIn("data-ai-jobs-panel", assistant)
        self.assertIn("window.location.reload();", wizard)
        self.assertIn("}, 5000);", wizard)
        self.assertIn("step == 2 and material_job_view", assistant)
        self.assertNotIn("ai_jobs[0].status in", wizard)
        self.assertIn('new Set(["succeeded", "failed", "cancelled"])', wizard)

        workbench = Path("app/domains/lupademo/templates/package_workbench.html").read_text(encoding="utf-8")
        self.assertIn("new EventSource(row.dataset.streamUrl)", workbench)
        self.assertIn("window.setInterval(poll, 5000)", workbench)
        self.assertIn("window.location.reload();", workbench)

    def test_wizard_progress_renders_in_assistant_on_material_step_for_terminal_jobs(self) -> None:
        job = svc.start_material_ingest_job(db, case, PERMIT, 1, azure_enabled=False)
        try:
            step_two = client.get(f"{base}/wizard/2")
            step_three = client.get(f"{base}/wizard/3")
            svc.cancel_ai_job(db, job["id"])
            terminal_step_two = client.get(f"{base}/wizard/2")
        finally:
            if svc.find_ai_job(db, job["id"])["job"].get("status") in {"pending", "running"}:
                svc.cancel_ai_job(db, job["id"])

        self.assertEqual(step_two.status_code, 200)
        self.assertIn('class="assistant-job"', step_two.text)
        self.assertNotIn('class="assistant-job"', step_three.text)
        self.assertEqual(terminal_step_two.status_code, 200)
        self.assertIn('class="assistant-job"', terminal_step_two.text)
        self.assertIn('data-job-initial-status="cancelled"', terminal_step_two.text)

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

    def test_audit_page_shows_ai_call_metadata_without_payloads(self) -> None:
        response = client.get(f"/cases/{case}/lupademo/admin/audit-trail")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Toteutuneet AI-kutsut", response.text)
        self.assertNotIn("request_json", response.text)
        self.assertNotIn("response_json", response.text)

    def test_package_step_renders_before_package_exists(self) -> None:
        empty_case = db.create_case("Empty package test", language="fi")
        svc.ensure_seed_state(db, empty_case)
        response = client.get(f"/cases/{empty_case}/lupademo/application/{PERMIT}/wizard/5")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Lähetyspakettia ei ole vielä muodostettu", response.text)
        self.assertIn("disabled", response.text)

    def test_demo_lab_links_to_standalone_tools(self) -> None:
        response = client.get("/demo-lab")
        self.assertEqual(response.status_code, 200)
        paths = [
            f"/cases/{case}/lupademo/metrics",
            f"/cases/{case}/lupademo/admin/criteria",
            f"/cases/{case}/lupademo/suggestion-service",
            f"/cases/{case}/lupademo/admin/benchmark-lab",
            f"/cases/{case}/lupademo/admin/audit-trail",
            f"/cases/{case}/lupademo/admin/production-hardening",
            "/demo/scenarios",
            "/demo-lab/presenter",
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertIn(f'href="{path}"', response.text)
                self.assertEqual(client.get(path).status_code, 200)

    def test_navigation_is_scoped_to_active_workspace(self) -> None:
        applicant = client.get(f"{base}/wizard/1").text
        authority = client.get(f"/cases/{case}/lupademo/authority/queue").text
        demo_lab = client.get("/demo-lab").text

        self.assertIn(">Hanke</a>", applicant)
        self.assertIn(">Hakemus</a>", applicant)
        self.assertNotIn(">Viranomainen</a>", applicant.split("</header>", 1)[0])
        self.assertNotIn(">Demo Lab</a>", applicant.split("</header>", 1)[0])

        authority_header = authority.split("</header>", 1)[0]
        self.assertIn(">Työjono</a>", authority_header)
        self.assertNotIn(">Hanke</a>", authority_header)
        self.assertNotIn(">Hakemus</a>", authority_header)

        demo_header = demo_lab.split("</header>", 1)[0]
        self.assertIn(">Yleiskuva</a>", demo_header)
        self.assertIn(">Integraatiot</a>", demo_header)
        self.assertNotIn(">Hanke</a>", demo_header)
        self.assertNotIn(">Hakemus</a>", demo_header)

        for page in (applicant, authority, demo_lab):
            footer = page.split('<footer class="service-footer">', 1)[1]
            self.assertIn('href="/">Hakija ja LV</a>', footer)
            self.assertIn(">Käsittelijä</a>", footer)
            self.assertIn(">Demo Lab ja alusta</a>", footer)
            self.assertEqual(page.count('<footer class="service-footer">'), 1)


if __name__ == "__main__":
    unittest.main()
