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
from app.infra.azure_client import AzureOpenAIClient  # noqa: E402
from app.infra.config import Settings, load_settings  # noqa: E402
from app.infra.db import DB  # noqa: E402
from app.infra.llm_factory import create_llm_client  # noqa: E402
from app.infra.llm_utils import call_llm_json, vector_store_ids  # noqa: E402
from app.infra.mock_llm import MockLLMClient  # noqa: E402
from app.domains.lupademo.services.application import deterministic_suggestion  # noqa: E402
from app.domains.lupademo.services.intake import azure_vector_store_ids, build_retrieval_context  # noqa: E402
from app.main import app, db as app_db  # noqa: E402

PERMIT = "environmental_industrial"
KEY = {"X-Lupademo-Api-Key": "demo-avi-key"}
VANTAA_KEY = {"X-Lupademo-Api-Key": "demo-vantaa-key"}
SAMPLE = "Biokaasulaitos Vantaalla, kasittelymaara 18000 t/a, toiminnanharjoittaja Oy Testi Ab."

client = TestClient(app, follow_redirects=False)
db: DB
case: str
base: str


def setUpModule() -> None:
    """Run the applicant flow once; the agent tests all read its artifacts."""
    global db, case, base
    db = app_db
    case = svc.ensure_demo_case(db)
    base = f"/cases/{case}/lupademo/application/{PERMIT}"
    client.post(
        f"{base}/attachments/upload",
        files={"file": ("hakemus.txt", io.BytesIO(SAMPLE.encode()), "text/plain")},
        data={"delivery_scope": "official_submission"},
    )
    client.post(f"{base}/ai/check", data={})
    client.post(f"{base}/ai/intake-analysis", data={})


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

        retried = svc.retry_ai_job(db, job["id"])
        self.assertEqual(retried["status"], "running")
        self.assertEqual(retried["progress_pct"], 0)


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
        with patch("app.infra.azure_client.httpx.Client", side_effect=lambda **kwargs: real_client(transport=transport, **kwargs)):
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

        with patch("app.infra.azure_client.httpx.Client", side_effect=lambda **kwargs: real_client(transport=transport, **kwargs)):
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

    def test_output_limit_retries_with_larger_budget_and_low_reasoning(self) -> None:
        class RetryClient(self.FakeClient):
            def __init__(self) -> None:
                super().__init__()
                self.requests: list[dict[str, object]] = []

            def responses(self, **kwargs: object) -> dict:
                self.requests.append(kwargs)
                if len(self.requests) == 1:
                    return {
                        "status": "incomplete",
                        "incomplete_details": {"reason": "max_output_tokens"},
                        "output": [],
                    }
                return {"output": [{"type": "message", "content": [{"type": "output_text", "text": '{"ok": true}'}]}]}

        client = RetryClient()
        result = call_llm_json(
            client,
            "synthetic prompt",
            db=self.db,
            agent="test.retry",
            max_output_tokens=6000,
            reasoning_effort="medium",
        )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(client.requests[1]["max_output_tokens"], 12000)
        self.assertEqual(client.requests[1]["reasoning_effort"], "low")
        self.assertIsNone(self.db.list_ai_logs(agent="test.retry")[0]["error"])

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

    def test_suggestion_api_uses_municipality_specific_criteria_version(self) -> None:
        payload = {
            "tenant_id": "kerava_demo",
            "permit_type": "building_residential",
            "field_id": "project_description",
            "form_schema": svc.load_form_schema("building_residential"),
            "form_data": {"municipality": "Kerava"},
            "common_data": {"municipality": "Kerava"},
            "request_id": "kerava-suggestion",
        }

        result = svc.suggestion_service_field(payload, db=db, settings=load_settings(), case_id=case)

        self.assertEqual(result["criteria_version"], "2026-08-demo-1")
        self.assertEqual(result["suggestions"][0]["criteria_version"], "2026-08-demo-1")

    def test_precheck_api_uses_municipality_specific_criteria_version(self) -> None:
        result = svc.suggestion_service_check(
            {
                "tenant_id": "kerava_demo",
                "permit_type": "building_residential",
                "form_schema": svc.load_form_schema("building_residential"),
                "form_data": {"municipality": "Kerava"},
                "common_data": {"municipality": "Kerava"},
                "request_id": "kerava-precheck",
            },
            db=db,
            settings=load_settings(),
            case_id=case,
        )

        self.assertEqual(result["criteria_version"], "2026-08-demo-1")

        response = client.post(
            "/api/v1/applications:precheck",
            json={
                "permit_type": "building_residential",
                "form_schema": svc.load_form_schema("building_residential"),
                "form_data": {"municipality": "Kerava"},
                "common_data": {"municipality": "Kerava"},
                "request_id": "kerava-precheck-http",
            },
            headers=VANTAA_KEY,
        )

        self.assertEqual(response.status_code, 403)

    def test_legacy_api_rejects_tenant_disallowed_municipality(self) -> None:
        payload = {
            "tenant_id": "vantaa_demo",
            "permit_type": "building_residential",
            "field_id": "project_description",
            "form_schema": svc.load_form_schema("building_residential"),
            "form_data": {"municipality": "Kerava"},
            "common_data": {"municipality": "Kerava"},
            "request_id": "legacy-kerava-rejected",
        }

        for path in ("/api/v1/suggestions/field", "/api/v1/suggestions/form", "/api/v1/checks/application"):
            with self.subTest(path=path):
                response = client.post(path, json=payload, headers=VANTAA_KEY)
                self.assertEqual(response.status_code, 403)

    def test_legacy_api_responses_expose_job_lifecycle_urls(self) -> None:
        payload = {
            "tenant_id": "avi_demo",
            "permit_type": PERMIT,
            "field_id": "activity_description",
            "form_schema": svc.load_form_schema(PERMIT),
            "form_data": {},
            "request_id": "legacy-lifecycle-contract",
        }

        for path in ("/api/v1/suggestions/field", "/api/v1/suggestions/form", "/api/v1/checks/application"):
            with self.subTest(path=path):
                response = client.post(path, json=payload, headers=KEY)
                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertIn("job_id", body)
                for key in ("events_url", "stream_url", "result_url"):
                    self.assertIn(key, body)

    def test_legacy_result_url_returns_canonical_envelope(self) -> None:
        payload = {
            "tenant_id": "avi_demo",
            "permit_type": PERMIT,
            "field_id": "activity_description",
            "form_schema": svc.load_form_schema(PERMIT),
            "form_data": {},
            "request_id": "legacy-result-envelope",
        }
        body = client.post("/api/v1/suggestions/field", json=payload, headers=KEY).json()
        result = client.get(body["result_url"], headers=KEY).json()
        self.assertEqual(result["job_id"], body["job_id"])
        self.assertIn("result", result)

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
                self.assertIn(method(path).status_code, {401, 403})

        self.assertEqual(
            client.post(f"/api/v1/jobs/{body['job_id']}/cancel", headers=KEY).status_code,
            409,
        )
        self.assertEqual(
            client.post(f"/api/v1/jobs/{body['job_id']}/retry", headers=KEY).status_code,
            409,
        )

    def test_ai_job_creation_requires_tenant_and_owns_job(self) -> None:
        payload = {"permit_type": "environmental_industrial", "title": "API job contract"}
        self.assertIn(client.post("/api/v1/ai/jobs", json=payload).status_code, {401, 403})

        response = client.post("/api/v1/ai/jobs", json=payload, headers=KEY)
        self.assertEqual(response.status_code, 200)
        job_id = response.json()["job_id"]
        self.assertEqual(client.get(f"/api/v1/jobs/{job_id}", headers=KEY).status_code, 200)

        denied = client.post(
            "/api/v1/ai/jobs",
            json={"application_id": "building_residential", "title": "Disallowed permit"},
            headers=KEY,
        )
        self.assertEqual(denied.status_code, 403)

    def test_suggestion_response_exposes_job_lifecycle_urls(self) -> None:
        response = client.post(
            "/api/v1/field-suggestions",
            json={"permit_type": PERMIT, "field_id": "activity_description", "request_id": "lifecycle-contract"},
            headers=KEY,
        )

        body = response.json()
        self.assertEqual(response.status_code, 200)
        for key in ("events_url", "stream_url", "result_url"):
            self.assertIn(key, body)

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
        self.assertEqual(response.headers["location"], f"{base}/wizard/3#suggestions")
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

    def test_package_workbench_renders_pending_extraction_without_accepted_value(self) -> None:
        package_id = svc.current_material_package(db, case, PERMIT)["package_id"]
        queue = svc.artifact_json(db, case, "field_extraction_queue", PERMIT, {"items": []})
        original_items = queue.get("items", [])
        original_analysis = svc.artifact_json(db, case, "package_analysis_result", PERMIT, {})
        queue["items"] = [
            {
                "field_id": "applicant_name",
                "suggested_value": "Esimerkki Oy",
                "source_document": "hakemus.txt",
                "confidence": 0.9,
                "status": "pending",
            }
        ]
        svc.save_artifact(db, case, "field_extraction_queue", PERMIT, queue)
        svc.save_artifact(
            db,
            case,
            "package_analysis_result",
            PERMIT,
            {"summary": "Analyysi valmis", "readiness": "incomplete", "readiness_score": 60, "findings": []},
        )
        try:
            response = client.get(f"/applicant/packages/{package_id}")
        finally:
            queue["items"] = original_items
            svc.save_artifact(db, case, "field_extraction_queue", PERMIT, queue)
            svc.save_artifact(db, case, "package_analysis_result", PERMIT, original_analysis)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Esimerkki Oy", response.text)

    def test_step_two_collapses_help_and_defers_suggestion_review(self) -> None:
        page = client.get(f"{base}/wizard/2")

        self.assertEqual(page.status_code, 200)
        self.assertIn('class="field-section" open', page.text)
        self.assertNotIn("Pyydä ehdotus", page.text)
        self.assertNotIn("Hyväksy lomakkeelle", page.text)

    def test_front_page_picker_switches_customer_package(self) -> None:
        package = svc.current_material_package(db, case, PERMIT)
        common = svc.artifact_json(db, case, "lv_common_data", "main", {"data": {}})

        page = client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Hakija", page.text)
        self.assertIn(common["data"]["applicant_name"], page.text)
        # The picker shows only the customer, not the package name
        self.assertNotIn(package["display_name"], page.text)

        switched = client.post("/workspace/select", data={"workspace": f"{case}:{package['package_id']}"})
        self.assertEqual(switched.status_code, 303)
        self.assertEqual(switched.headers["location"], "/")
        self.assertEqual(db.get_app_setting("lupademo_active_case_id"), case)
        self.assertEqual(db.get_app_setting("lupademo_active_package_id"), package["package_id"])

        selected_page = client.get("/")
        selected_value = f'value="{case}:{package["package_id"]}" selected'
        self.assertIn(f'value="{case}:{package["package_id"]}"', selected_page.text)
        self.assertIn("selected", selected_page.text)

    def test_project_page_uses_shared_branding_without_mock_metadata(self) -> None:
        page = client.get(f"/lv/projects/{case}")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Lupa-apurin etusivu", page.text)
        self.assertNotIn("LV-mock", page.text)
        self.assertNotIn("system-badge", page.text)

    def test_specs_resolve_to_runtime_paths(self) -> None:
        ui = json.loads(Path("docs/archive/v5-specs/ui.json").read_text(encoding="utf-8"))
        roles = json.loads(Path("docs/archive/v5-specs/roles.json").read_text(encoding="utf-8"))
        workflow = json.loads(Path("docs/archive/v5-specs/workflow.json").read_text(encoding="utf-8"))
        self.assertIn("material_package_workbench", workflow["workflows"])
        self.assertIn("deterministic_checks", workflow["workflows"]["applicant_application_check"])
        self.assertIn("use_package_workbench", next(role for role in roles["roles"] if role["key"] == "applicant")["capabilities"])
        for page in ui["pages"]:
            route = page["route"].replace("{case_id}", case)
            with self.subTest(page=page["slug"]):
                self.assertIn(client.get(route).status_code, (200, 301, 303))

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
            "/metrics",
            "/admin/criteria",
            "/admin/suggestion-service",
            "/admin/benchmarks",
            "/admin/audit-trail",
            "/admin/production-hardening",
            "/demo/scenarios",
            "/demo-lab/presenter",
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertIn(f'href="{path}"', response.text)
                self.assertEqual(client.get(path).status_code, 200)

    def test_criteria_workspace_explains_active_municipality_and_rule_impact(self) -> None:
        response = client.get("/admin/criteria")

        self.assertEqual(response.status_code, 200)
        self.assertIn("ohjaa nyt AI-tarkistuksia", response.text)
        self.assertIn("Vaikutus AI-tarkistukseen", response.text)
        self.assertIn("Koko kriteeristön JSON-muokkaus", response.text)

        kerava = client.get("/admin/criteria?municipality=Kerava")
        self.assertEqual(kerava.status_code, 200)
        self.assertIn("Kerava ohjaa nyt AI-tarkistuksia", kerava.text)
        self.assertIn('value="Kerava" selected', kerava.text)

    def test_api_playground_renders_integration_contract_summary(self) -> None:
        response = client.get(f"/cases/{case}/lupademo/suggestion-service/api-demo")

        self.assertEqual(response.status_code, 200)
        self.assertIn("API-työkalu", response.text)
        self.assertIn("Lopullinen JSON-vastaus", response.text)

    def test_api_playground_rejects_tenant_disallowed_municipality(self) -> None:
        payload = {
            "tenant_id": "vantaa_demo",
            "permit_type": "building_residential",
            "form_data": {"municipality": "Kerava"},
            "field_id": "project_description",
        }

        response = client.post(
            f"/cases/{case}/lupademo/suggestion-service/api-demo",
            data={"api_key": "demo-vantaa-key", "request_json": json.dumps(payload)},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 403)

    def test_api_playground_uses_selected_tenant_case(self) -> None:
        payload = {
            "tenant_id": "avi_demo",
            "permit_type": "environmental_industrial",
            "source_text": "Biokaasulaitoksen hakemus.",
        }

        response = client.post(
            f"/cases/{case}/lupademo/suggestion-service/api-demo",
            data={"api_key": "demo-avi-key", "request_json": json.dumps(payload)},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertIn("/cases/", response.headers["location"])
        self.assertNotEqual(response.headers["location"], f"/cases/{case}/lupademo/suggestion-service/api-demo")

    def test_api_playground_rejects_unknown_municipality(self) -> None:
        payload = {
            "tenant_id": "vantaa_demo",
            "permit_type": "building_residential",
            "form_data": {"municipality": "Espoo"},
            "field_id": "project_description",
        }

        response = client.post(
            f"/cases/{case}/lupademo/suggestion-service/api-demo",
            data={"api_key": "demo-vantaa-key", "request_json": json.dumps(payload)},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 403)

    def test_api_playground_rejects_empty_document(self) -> None:
        payload = {
            "tenant_id": "avi_demo",
            "permit_type": "environmental_industrial",
            "documents": [{"filename": "empty.txt"}],
        }

        response = client.post(
            f"/cases/{case}/lupademo/suggestion-service/api-demo",
            data={"api_key": "demo-avi-key", "request_json": json.dumps(payload)},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 422)

    def test_api_rejects_malformed_nested_payload_shapes(self) -> None:
        response = client.post(
            "/api/v1/suggestions/field",
            json={"permit_type": PERMIT, "field_id": "activity_description", "form_data": "bad"},
            headers=KEY,
        )
        self.assertEqual(response.status_code, 422)

    def test_api_rejects_malformed_json(self) -> None:
        for path in (
            "/api/v1/field-suggestions",
            "/api/v1/applications:precheck",
            "/api/v1/suggestions/field",
            "/api/v1/suggestions/form",
            "/api/v1/checks/application",
        ):
            with self.subTest(path=path):
                response = client.post(path, content="{", headers={**KEY, "Content-Type": "application/json"})
                self.assertEqual(response.status_code, 422)

    def test_package_api_rejects_malformed_document_entries(self) -> None:
        response = client.post(
            "/api/v1/application-packages:analyze",
            json={"permit_type": PERMIT, "documents": ["invalid", {"text": "Biokaasu"}]},
            headers=KEY,
        )
        self.assertEqual(response.status_code, 422)

    def test_api_playground_accepts_source_text_package_contract(self) -> None:
        payload = {
            "tenant_id": "avi_demo",
            "permit_type": "environmental_industrial",
            "source_text": "Biokaasulaitoksen ympäristölupahakemus, kapasiteetti 18000 tonnia vuodessa.",
            "request_id": "playground-source-text",
        }

        response = client.post(
            f"/cases/{case}/lupademo/suggestion-service/api-demo",
            data={"api_key": "demo-avi-key", "request_json": json.dumps(payload)},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        page = client.get(response.headers["location"])
        self.assertIn("Lopullinen JSON-vastaus", page.text)
        self.assertIn("result_url", page.text)

    def test_api_playground_renders_document_inventory_from_result(self) -> None:
        payload = {
            "tenant_id": "avi_demo",
            "permit_type": "environmental_industrial",
            "documents": [{"filename": "hakemus.txt", "document_role": "application_form", "text": "Ympäristölupahakemus."}],
            "request_id": "playground-inventory",
        }
        response = client.post(
            f"/cases/{case}/lupademo/suggestion-service/api-demo",
            data={"api_key": "demo-avi-key", "request_json": json.dumps(payload)},
            follow_redirects=False,
        )
        page = client.get(response.headers["location"])
        self.assertIn("Dokumenttien tekniset tiedot", page.text)
        self.assertIn("hakemus.txt", page.text)

    def test_package_api_does_not_fabricate_fields_for_neutral_document(self) -> None:
        response = client.post(
            "/api/v1/application-packages:analyze",
            json={
                "permit_type": PERMIT,
                "request_id": "neutral-document",
                "documents": [{"filename": "neutral.txt", "text": "Asiakirja ilman tunnistettavia lomakekenttia."}],
            },
            headers=KEY,
        )

        self.assertEqual(response.status_code, 200)
        result = client.get(response.json()["result_url"], headers=KEY)
        self.assertEqual(result.json()["result"]["extracted_fields"], [])

    def test_package_api_preserves_document_source_references(self) -> None:
        response = client.post(
            "/api/v1/application-packages:analyze",
            json={
                "permit_type": PERMIT,
                "request_id": "document-source-refs",
                "documents": [{"document_id": "doc-1", "filename": "hakemus.txt", "text": "activity_description: Biokaasulaitoksen toiminta ja tuotanto."}],
            },
            headers=KEY,
        )

        result = client.get(response.json()["result_url"], headers=KEY).json()["result"]
        evidence = [ref for field in result["extracted_fields"] for ref in field.get("evidence_refs", [])]
        self.assertTrue(any(ref.get("source_id") == "doc-1" for ref in evidence))

    def test_package_api_result_includes_criteria_provenance(self) -> None:
        response = client.post(
            "/api/v1/application-packages:analyze",
            json={"permit_type": PERMIT, "source_text": "Biokaasulaitoksen ympäristölupahakemus."},
            headers=KEY,
        )
        result = client.get(response.json()["result_url"], headers=KEY).json()["result"]
        self.assertIn("criteria_set_id", result)
        self.assertIn("criteria_municipality", result)

    def test_structure_extract_returns_lifecycle_envelope(self) -> None:
        response = client.post(
            "/api/v1/structure/extract",
            json={"permit_type": PERMIT, "source_text": "activity_description: Biokaasu."},
            headers=KEY,
        )

        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertIn("result_url", body)
        self.assertIn("result", client.get(body["result_url"], headers=KEY).json())

    def test_structure_extract_rejects_disallowed_municipality(self) -> None:
        response = client.post(
            "/api/v1/structure/extract",
            json={"permit_type": "building_residential", "form_data": {"municipality": "Kerava"}, "source_text": "Biokaasu."},
            headers=VANTAA_KEY,
        )
        self.assertEqual(response.status_code, 403)

    def test_auxiliary_api_routes_reject_disallowed_municipality(self) -> None:
        payload = {"permit_type": "building_residential", "form_data": {"municipality": "Kerava"}}
        for path in ("/api/v1/ai/jobs", "/api/v1/question-plans", "/api/v1/authority:summarize"):
            with self.subTest(path=path):
                response = client.post(path, json=payload, headers=VANTAA_KEY)
                self.assertEqual(response.status_code, 403)

    def test_api_playground_normalizes_minimal_field_request(self) -> None:
        payload = {"tenant_id": "avi_demo", "permit_type": PERMIT, "field_id": "activity_description"}
        response = client.post(
            f"/cases/{case}/lupademo/suggestion-service/api-demo",
            data={"api_key": "demo-avi-key", "request_json": json.dumps(payload)},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)

    def test_package_api_rejects_malformed_document_field_types(self) -> None:
        for document in (
            {"filename": "bad.txt", "text": 123},
            {"filename": {"bad": True}, "text": "Biokaasu"},
        ):
            with self.subTest(document=document):
                response = client.post(
                    "/api/v1/application-packages:analyze",
                    json={"permit_type": PERMIT, "documents": [document]},
                    headers=KEY,
                )
                self.assertEqual(response.status_code, 422)

    def test_package_api_rejects_empty_document(self) -> None:
        response = client.post(
            "/api/v1/application-packages:analyze",
            json={"permit_type": PERMIT, "request_id": "empty-document", "documents": [{"filename": "empty.txt"}]},
            headers=KEY,
        )

        self.assertEqual(response.status_code, 422)

    def test_navigation_is_scoped_to_active_workspace(self) -> None:
        applicant = client.get(f"{base}/wizard/1").text
        authority = client.get("/authority/queue").text
        demo_lab = client.get("/demo-lab").text

        # Navigation sidebar shows workspace links for every page.
        for page in (applicant, authority, demo_lab):
            sidebar = page.split('<aside class="app-sidebar"', 1)[1].split("</aside>", 1)[0]
            self.assertIn('>Hakija<', sidebar)
            self.assertIn('>Viranomainen<', sidebar)
            self.assertIn('>Demo Lab ja alusta<', sidebar)
            self.assertEqual(page.count('<footer class="service-footer">'), 1)

        # Wizard page has the six-step stepper in the main content area (not sidebar).
        for label in ("Perustiedot", "Aineistot", "AI-ehdotukset", "Tarkistus", "Paketointi", "Vastaanotto"):
            self.assertIn(f">{label}</strong>", applicant)
        self.assertIn('aria-label="Hakemuksen vaiheet"', applicant)

        # Authority and Demo Lab keep their workspace-specific navigation.
        authority_header = authority.split("</header>", 1)[0]
        self.assertIn(">Työtila</span>", authority_header)
        self.assertIn(">Viranomainen</strong>", authority_header)
        demo_sidebar = demo_lab.split('<aside class="app-sidebar"', 1)[1].split("</aside>", 1)[0]
        self.assertIn(">Yleiskuva", demo_sidebar)
        self.assertIn(">Demon ohjaus", demo_sidebar)


if __name__ == "__main__":
    unittest.main()
