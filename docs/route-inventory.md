# Route Inventory — KEHA LV Demo

Complete, machine-checkable table of every HTTP route in the app. Routes are
grouped by the `routes/*.py` module that defines them. All routes are mounted
under the single `create_lupademo_router()` in `routes/__init__.py`.

Legend: `{case_id}` and `{application_id}` are path params. `application_id`
is a permit type id (e.g. `building_residential`); handlers normalize it via
`svc.application_id_for(...)`.

## `routes/applicant.py` — applicant wizard, intake, common data, chat

> **Two entry paths (do NOT mix-match).** Routes under `/applicant/packages/*`
> belong to **Path A — standalone package review** (permit and municipality
> first). They do not create a hanke/application until promotion. Routes under
> `/projects/new` → `/cases/{case_id}/lupademo/application/*/wizard/*` belong
> to **Path B — wizard** (form-first). See `AGENTS.md` → "Two entry paths".

| Method | Path | Handler | Template | Path |
|--------|------|---------|----------|------|
| GET | `/login` | `login` | `applicant/login.html` | — |
| POST | `/login` | `login_post` | — (redirect) | — |
| POST | `/logout` | `logout` | — (redirect) | — |
| GET | `/` | `landing` | `applicant/landing.html` | — |
| GET | `/demo/scenarios` | `scenario_list` | `admin/scenarios.html` | — |
| GET | `/demo-lab/presenter` | `presenter_lab` | `admin/presenter.html` | — |
| GET | `/applicant` | `applicant_start` | `applicant/applicant_start.html` | — |
| GET | `/applicant/packages/new` | `applicant_package_new` | `applicant/package_new.html` | **A** |
| POST | `/applicant/packages/new` | `applicant_package_create` | — (redirect) | **A** |
| GET | `/applicant/packages/{package_id}` | `applicant_package_workbench` | `applicant/package_workbench.html` | **A** |
| POST | `/applicant/packages/{package_id}/analyze` | `applicant_package_analyze` | — (redirect) | **A** |
| POST | `/applicant/packages/{package_id}/promote` | `promote_package_review` | — (redirect) | **A** |
| GET | `/projects/new` | `project_new` | `applicant/applicant_start.html` | **B** |
| POST | `/demo/scenarios/start` | `scenario_start` | — (redirect) | — |
| POST | `/demo/reset` | `demo_reset` | — (redirect) | — |
| POST | `/projects/new` | `create_project` | — (redirect) | **B** |
| GET | `/projects/{project_id}` | `project_workspace` | `applicant/lv_project.html` | **B** |
| POST | `/projects/{project_id}/applications` | `create_application` | — (redirect) | **B** |
| GET | `/projects/{project_id}/intake` | `project_intake` | — (redirect) | **B** |
| POST | `/projects/{project_id}/intake` | `project_intake_post` | — (redirect) | **B** |
| GET | `/projects/{project_id}/intake/{run_id}` | `project_intake_result` | — (redirect) | **B** |
| GET | `/projects/{project_id}/applications/{application_id}/studio` | `application_studio` | — (redirect) | **B** |
| GET | `/demo/presenter` | `presenter` | — (redirect) | — |
| GET | `/lv` | `lv` | — (redirect) | — |
| GET | `/lv/projects/{case_id}` | `lv_project` | `applicant/lv_project.html` | — |
| GET | `/lv/projects/new` | `lv_project_new` | — (redirect → `/projects/new`) | — |
| GET | `/customer/profile` | `customer_profile` | `applicant/customer_profile.html` | — |
| POST | `/customer/profile` | `update_customer_profile` | — (redirect) | — |
| POST | `/lv/projects/{case_id}/common-data` | `update_lv` | — (redirect) | — |
| POST | `/lv/projects/{case_id}/sync` | `accept_sync` | — (redirect) | — |
| GET | `/api/lv/projects/{case_id}/common-data` | `common_data` | JSON | — |
| POST | `/api/lv/events` | `lv_events` | JSON | — |
| GET | `/cases/{case_id}/lupademo/start` | `start` | — (redirect) | **B** |
| GET | `/cases/{case_id}/lupademo/application/{application_id}` | `application` | — (redirect) | **B** |
| GET | `/cases/{case_id}/lupademo/application/{application_id}/wizard/{step}` | `application_wizard` | `applicant/wizard.html` | **B** |
| POST | `/cases/{case_id}/lupademo/application/{application_id}/materials/package` | `create_application_material_package` | — (redirect) | **B** |
| POST | `/cases/{case_id}/lupademo/application/{application_id}/save` | `save_application` | — (redirect) | **B** |
| POST | `/cases/{case_id}/lupademo/application/{application_id}/attachments/upload` | `upload_attachment` | — (redirect) | **B** |
| POST | `/cases/{case_id}/lupademo/application/{application_id}/materials/upload-zip` | `upload_material_zip` | — (redirect) | **B** |
| POST | `/cases/{case_id}/lupademo/application/{application_id}/materials/upload-files` | `upload_material_files` | — (redirect) | **B** |
| POST | `/cases/{case_id}/lupademo/application/{application_id}/materials/add-text` | `add_material_text` | — (redirect) | **B** |
| POST | `/cases/{case_id}/lupademo/application/{application_id}/attachments/{attachment_id}/scope` | `change_attachment_scope` | — (redirect) | **B** |
| POST | `/cases/{case_id}/lupademo/application/{application_id}/attachments/{attachment_id}/metadata` | `update_attachment_metadata` | — (redirect) | **B** |
| POST | `/cases/{case_id}/lupademo/application/{application_id}/attachments/{attachment_id}/delete` | `delete_attachment` | — (redirect) | **B** |
| POST | `/cases/{case_id}/lupademo/application/{application_id}/ai/intake-analysis` | `intake_analysis` | — (redirect) | **B** |
| POST | `/cases/{case_id}/lupademo/application/{application_id}/extractions/accept` | `accept_extraction` | — (redirect) | **B** |
| POST | `/cases/{case_id}/lupademo/application/{application_id}/extractions/reject` | `reject_extraction` | — (redirect) | **B** |
| POST | `/cases/{case_id}/lupademo/application/{application_id}/ai/suggest-field` | `suggest_field` | — (redirect) | **B** |
| POST | `/cases/{case_id}/lupademo/application/{application_id}/ai/suggest-form` | `suggest_form` | — (redirect) | **B** |
| POST | `/cases/{case_id}/lupademo/application/{application_id}/suggestions/{suggestion_id}/accept` | `accept_suggestion` | — (redirect) | **B** |
| POST | `/cases/{case_id}/lupademo/application/{application_id}/suggestions/accept-selected` | `accept_selected_suggestions` | — (redirect) | **B** |
| POST | `/cases/{case_id}/lupademo/application/{application_id}/suggestions/accept-all` | `accept_all_suggestions` | — (redirect) | **B** |
| POST | `/cases/{case_id}/lupademo/application/{application_id}/suggestions/{suggestion_id}/reject` | `reject_suggestion` | — (redirect) | **B** |
| POST | `/cases/{case_id}/lupademo/application/{application_id}/chat` | `contextual_chat` | — (redirect) | **B** |
| POST | `/cases/{case_id}/lupademo/application/{application_id}/chat/messages` | `chat_messages` | JSON | **B** |
| POST | `/cases/{case_id}/lupademo/application/{application_id}/chat/stream` | `chat_stream` | SSE | **B** |
| POST | `/cases/{case_id}/lupademo/application/{application_id}/voice/mock` | `voice_mock` | — (redirect) | **B** |
| POST | `/cases/{case_id}/lupademo/application/{application_id}/ai/check` | `run_check` | — (redirect) | **B** |
| GET | `/cases/{case_id}/lupademo/application/{application_id}/package` | `package` | — (redirect) | **B** |
| GET | `/cases/{case_id}/lupademo/application/{application_id}/jobs/{job_id}` | `application_job_status` | JSON | **B** |
| GET | `/cases/{case_id}/lupademo/application/{application_id}/jobs/{job_id}/stream` | `application_job_stream` | SSE | **B** |
| POST | `/cases/{case_id}/lupademo/application/{application_id}/jobs/{job_id}/cancel` | `application_job_cancel` | — (redirect) | **B** |
| POST | `/cases/{case_id}/lupademo/application/{application_id}/submit` | `submit` | — (redirect) | **B** |
| GET | `/cases/{case_id}/lupademo/application/{application_id}/receipt` | `receipt` | `applicant/receipt.html` | **B** |

## `routes/authority.py` — caseworker queue, detail, observations

| Method | Path | Handler | Template |
|--------|------|---------|----------|
| GET | `/cases/{case_id}/lupademo/authority/queue` | `authority_queue` | — (redirect) |
| GET | `/authority/queue` | `authority_queue_global` | `authority/authority_queue.html` |
| GET | `/cases/{case_id}/lupademo/authority/applications/{application_id}` | `authority_detail` | `authority/authority_detail.html` |
| GET | `/authority/applications/{application_id}` | `authority_detail_global` | — (redirect) |
| POST | `/cases/{case_id}/lupademo/authority/applications/{application_id}/observations/{observation_id}/status` | `update_observation_status` | — (redirect) |
| POST | `/cases/{case_id}/lupademo/authority/applications/{application_id}/ai/summary` | `generate_authority_summary` | — (redirect) |
| POST | `/cases/{case_id}/lupademo/authority/applications/{application_id}/ai/request-info-draft` | `generate_additional_info` | — (redirect) |
| POST | `/cases/{case_id}/lupademo/authority/applications/{application_id}/ai/decision-support` | `generate_authority_memo` | — (redirect) |
| POST | `/cases/{case_id}/lupademo/authority/applications/{application_id}/status` | `update_authority_status` | — (redirect) |

## `routes/api_v1.py` — external suggestion-service API (tenant auth)

| Method | Path | Handler | Notes |
|--------|------|---------|-------|
| POST | `/api/v1/ai/jobs` | `api_v1_ai_jobs` | Start a generic AI job |
| GET | `/api/v1/jobs/{job_id}` | `api_v1_job_status` | Poll job status |
| GET | `/api/v1/jobs/{job_id}/stream` | `api_v1_job_stream` | SSE status stream |
| GET | `/api/v1/jobs/{job_id}/events` | `api_v1_job_events` | Agent event list |
| GET | `/api/v1/jobs/{job_id}/result` | `api_v1_job_result` | Completed job result |
| POST | `/api/v1/jobs/{job_id}/cancel` | `api_v1_job_cancel` | Cancel pending/running job |
| POST | `/api/v1/jobs/{job_id}/retry` | `api_v1_job_retry` | Retry failed/cancelled job |
| POST | `/api/v1/field-suggestions` | `api_v1_field_suggestions` | Field completion |
| POST | `/api/v1/applications:precheck` | `api_v1_applications_precheck` | Application check |
| POST | `/api/v1/application-packages:analyze` | `api_v1_application_packages_analyze` | Full package analysis (sync, HTTP 200) |
| POST | `/api/v1/question-plans` | `api_v1_question_plans` | Question plan |
| POST | `/api/v1/authority:summarize` | `api_v1_authority_summarize` | Authority summary |
| POST | `/api/v1/suggestions/field` | `api_suggest_field` | Field suggestion (legacy) |
| POST | `/api/v1/suggestions/form` | `api_suggest_form` | Form suggestion (legacy) |
| POST | `/api/v1/checks/application` | `api_check` | Application check (legacy) |
| POST | `/api/v1/structure/extract` | `api_extract` | Structure extraction (legacy) |
| GET | `/api/v1/criteria/{criteria_set_id}` | `api_criteria` | Criteria retrieval (tenant-scoped) |

## `routes/admin.py` — criteria admin, API playground, source ingest

| Method | Path | Handler | Template |
|--------|------|---------|----------|
| GET | `/cases/{case_id}/lupademo/suggestion-service` | `suggestion_service` | `admin/suggestion_service.html` |
| GET | `/cases/{case_id}/lupademo/suggestion-service/api-demo` | `api_demo` | `admin/api_demo.html` |
| GET | `/integrations/api-playground` | `api_playground` | — (redirect) |
| POST | `/cases/{case_id}/lupademo/suggestion-service/api-demo` | `api_demo_post` | — (redirect) |
| GET | `/cases/{case_id}/lupademo/admin/criteria` | `criteria_admin` | `admin/criteria.html` |
| POST | `/cases/{case_id}/lupademo/admin/criteria` | `criteria_admin_post` | — (redirect) |
| POST | `/cases/{case_id}/lupademo/admin/criteria/rule` | `criteria_rule_post` | — (redirect) |
| POST | `/cases/{case_id}/lupademo/admin/criteria/rule/delete` | `criteria_rule_delete_post` | — (redirect) |
| POST | `/cases/{case_id}/lupademo/admin/criteria/restore` | `criteria_restore_post` | — (redirect) |
| POST | `/cases/{case_id}/lupademo/admin/criteria/test` | `criteria_test_post` | — (redirect) |
| POST | `/cases/{case_id}/lupademo/admin/criteria/city` | `criteria_city_post` | — (redirect) |
| POST | `/cases/{case_id}/lupademo/admin/source-materials/ingest` | `source_materials_ingest` | — (redirect) |

## `routes/demo_lab.py` — benchmark, metrics, audit, UI-only demo

| Method | Path | Handler | Template |
|--------|------|---------|----------|
| GET | `/cases/{case_id}/lupademo/demo-cockpit` | `demo_cockpit` | `admin/demo_cockpit.html` |
| POST | `/cases/{case_id}/lupademo/demo-cockpit/criteria-comparison` | `run_criteria_comparison` | — (redirect) |
| GET | `/cases/{case_id}/lupademo/suggestion-service/ui-only` | `ui_only` | `admin/ui_only.html` |
| GET | `/suggestion-service/ui-only` | `ui_only_global` | — (redirect) |
| POST | `/cases/{case_id}/lupademo/suggestion-service/ui-only` | `ui_only_post` | — (redirect) |
| POST | `/cases/{case_id}/lupademo/suggestion-service/ui-only/accept` | `ui_only_accept` | — (redirect) |
| GET | `/cases/{case_id}/lupademo/admin/benchmark-lab` | `benchmark_lab` | `admin/benchmark_lab.html` |
| GET | `/cases/{case_id}/lupademo/admin/benchmarks` | `benchmark_lab_alias` | `admin/benchmark_lab.html` |
| GET | `/admin/benchmarks` | `benchmark_lab_global` | `admin/benchmark_lab.html` |
| GET | `/cases/{case_id}/lupademo/admin/audit-trail` | `audit_trail` | `admin/audit_trail.html` |
| GET | `/admin/audit-trail` | `audit_trail_global` | `admin/audit_trail.html` |
| POST | `/cases/{case_id}/lupademo/admin/benchmark-lab/run` | `benchmark_lab_run` | — (redirect) |
| GET | `/cases/{case_id}/lupademo/admin/production-hardening` | `production_hardening` | `admin/production_hardening.html` |
| GET | `/cases/{case_id}/lupademo/metrics` | `metrics` | `admin/metrics.html` |
| GET | `/cases/{case_id}/lupademo/metrics/report.json` | `metrics_report_json` | JSON |
| GET | `/cases/{case_id}/lupademo/metrics/report.html` | `metrics_report_html` | `admin/metrics_report.html` |
| POST | `/cases/{case_id}/lupademo/admin/agent-chain/audit-run` | `agent_chain_audit_run` | — (redirect) |
| GET | `/metrics` | `metrics_global` | `admin/metrics.html` |
| GET | `/metrics/report.html` | `metrics_report_global` | `admin/metrics_report.html` |
| GET | `/metrics/report.json` | `metrics_report_json_global` | JSON |
| GET | `/admin/criteria` | `criteria_global` | `admin/criteria.html` |
| GET | `/admin/suggestion-service` | `suggestion_service_global` | `admin/suggestion_service.html` |
| GET | `/admin/production-hardening` | `production_hardening_global` | `admin/production_hardening.html` |
| GET | `/demo-lab` | `demo_lab_index` | `admin/demo_lab.html` |

## Global redirects (no `case_id` in URL)

Several modules expose "global" variants that resolve the active case via
`scenarios.active_case_id(db()) or svc.ensure_demo_case(db())` and either
redirect to the case-scoped route or render the same template. These are the
entry points reachable from the top-level nav (`/authority/queue`, `/metrics`,
`/admin/criteria`, `/admin/suggestion-service`, `/demo-lab`).

## Keeping this file accurate

This table is **enforced by a guard test** — `tests/test_route_inventory.py`
walks `app.routes` and asserts every business route is documented here (and
that nothing documented here is stale). When you add or remove a route, update
this table; the test will fail if it drifts. The route count per module is also
summarized in `ARCHITECTURE.md`'s folder map.
