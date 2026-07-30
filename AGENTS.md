---
title: KEHA LV AI Demo Agent Guide
description: Architecture, conventions, and validated workflows for repository agents
---

## What this project is

A standalone, shareable demo of an AI-assisted permit-application platform ("Lupahakemus", LV) for Finnish municipal and regional authorities (KEHA / ELY centers, municipalities, AVI).

It demonstrates: AI filling application forms from unstructured materials, AI checking completeness against permit criteria, separating official attachments from AI-only context, submission packaging, authority review workflows, and an external AI suggestion-service API with tenant isolation.

Runs fully offline with a mock LLM (rule-based, instant) by default. Swap to Azure OpenAI via `.env`.

## Scope boundaries

**In scope:** the demo webapp, mock LLM, scenario scripts, API playground, criteria admin, authority views, benchmark lab.
**Out of scope:** production auth, real user data, real integration with authority systems, real legal decisions. This is a demo.

## Architecture

```
app/
  main.py                    FastAPI app, mounts router + static, global error handler, /healthz
  core/
    config.py                Settings dataclass + _bool_env() helper; force_mock_llm
                             computed once at startup (no scattered env-var checks)
    db.py                    SQLite store: cases, files, artifacts (JSON docs), app_settings, ai_logs
    llm_factory.py           create_llm_client() → mock or Azure client
    llm_utils.py             call_llm_json() helper, prompt templating
    mock_llm.py              Rule-based mock that responds instantly without network
  domains/lupademo/
    routes/                  HTTP routes package (Phase 2 split, see /workspace/plan.md):
      __init__.py               thin create_lupademo_router that mounts 5 sub-routers
      template_filters.py        shared Jinja2Templates instance + finnish_datetime filter
                                + FINDING_CATEGORY_LABELS global (imported by all 5 routers)
      applicant.py              42 handlers — wizard, intake, application studio, common
                                data, packaging, chat/voice (~360 lines)
      authority.py              9 handlers — queue, detail, observations, summary/memo
      api_v1.py                  7 handlers — external suggestion-service endpoints
      admin.py                   22 handlers — criteria admin, API demo/playground,
                                internal AI job API
      demo_lab.py               14 handlers — benchmark, metrics, audit, UI-only demo
    services/                Business logic package (Phase 2 split, see /workspace/plan.md):
      core.py                  dataclasses/contracts, form schemas & criteria, artifact/db
                                primitives, prompt rendering, common-data sync. No intra-package deps.
      jobs.py                   generic AI job lifecycle (start/advance/complete/cancel/retry).
                                Depends on core only.
      intake.py                 attachments, material packages, ingestion, extraction,
                                classification, retrieval/vector-store context. Depends on core+jobs.
      application.py            suggestions, criteria checks, package analysis, submission
                                packaging, authority views, chat/voice, metrics/benchmark,
                                external suggestion-service API. Depends on core+jobs+intake.
      __init__.py               re-exports everything so `import services as svc; svc.xxx()` is unchanged.

                                Dependency order (graphify-verified):
                                application → intake → jobs → core
                                Core imports nothing intra-package. No cycles exist.
    scenarios.py             Demo scenario definitions + case creation (1 minimal fallback;
                             4 canonical scenarios in specs/scenarios/demo_scenarios.json)
    security.py              API key / tenant auth for suggestion-service endpoints
    static/style.css          Single CSS file
    templates/*.html          21 Jinja2 templates — all extend light_base.html directly
    specs/                     JSON specs: form schemas, permit criteria, scenarios, roles, eval definitions
docs/specs/                   Additional reference specs (not loaded at runtime)
data/                         Created at first run: SQLite DB + uploads/ (gitignored)
testiaineisto/                Test packages (sample zip uploads)
tests/                        Smoke tests (mock LLM, temp DB)
rules/                        ast-grep rules for pi-lens diagnostics
```

## Key concepts

### Cases

A case (`case_id`, UUID) is the top-level container — an "LV project." Everything lives under a case.

### Permit types

Three demo permit types (`PERMIT_SCHEMAS`), each with a JSON form schema + criteria set:

- `building_residential` — Rakennuslupa (building permit, residential)
- `environmental_industrial` — Ympäristölupa (environmental permit, industrial)
- `event_public` — Tapahtumalupa (public event permit)

### Applications

A case can hold multiple applications (one per permit type). Each is an artifact keyed `permit_application:<permit_type>` with `form_data`, `field_sources`, `status`.

### Wizard (6 steps)

1. **LV-yhteiset tiedot** — Common applicant data (name, business ID, address, etc.)
2. **Aineisto** — Upload materials (zip, files, text), view attachments, run AI intake analysis
3. **Hakemus** — AI-suggested form fields, accept/reject/edit, contextual chat, voice assistant
4. **Tarkistus** — AI permit check (blocking/major/minor issues), submit gate
5. **Paketointi** — Submission package preview (official attachments vs AI context)
6. **Vastaanotto** — Receipt / confirmation

### Artifacts

JSON documents stored in SQLite as `{case_id}:{type}:{name}`. The central persistence pattern. Key artifact types:

- `lv_project:main` — Project metadata, scenario, statuses
- `lv_common_data:main` — Shared applicant data (`COMMON_FIELDS`)
- `permit_application:{permit_type}` — Application state
- `ai_suggestions:{permit_type}` — Pending AI suggestions
- `check_report:{permit_type}` — Last check results
- `submission_package:{permit_type}` — Composed package
- `field_extraction_queue:{permit_type}` — Extracted fields awaiting review
- `lupademo_events:main` — Audit trail

### Common data (LV-yhteiset tiedot)

Fields shared across permits (`applicant_name`, `business_id`, `municipality`, `address`, etc.). Synced bidirectionally between LV common data and individual application forms. Changes to common data create sync proposals (`detect_common_data_changes`).

### Attachments

Uploaded files stored on disk (`data/uploads/`) + metadata in SQLite `files` table. Each attachment has `delivery_scope`:

- `official_submission` — Included in the submission package
- `ai_context_only` — Only for AI analysis, not sent to authority
- `ephemeral` — UI-only uploads, not persisted

### AI features (mock or Azure OpenAI)

- **Field suggestions** — AI fills individual form fields from materials
- **Intake analysis** — Classifies documents, extracts structure, identifies gaps
- **Application check** — Runs criteria rules against completed applications
- **Contextual chat** — Chat with AI about the application
- **Authority summary** — Summarizes application for caseworker
- **Additional info draft** — Drafts a request for more info from applicant
- **Decision support** — Drafts a decision memo
- **Voice assistant** — Mock voice transcript → AI assistant response

### Suggestion service (external-facing API)

`/api/v1/*` endpoints with tenant API key auth (`X-Lupademo-Api-Key`). Supports:

- `POST /api/v1/field-suggestions` — Field completion
- `POST /api/v1/applications:precheck` — Application check
- `POST /api/v1/application-packages:analyze` — Full package analysis (synchronous, returns HTTP 200 with a completed job and result reference)
- `POST /api/v1/authority:summarize` — Authority summary
- `GET /api/v1/criteria/{criteria_set_id}` — Criteria retrieval (tenant-scoped)

API keys and tenants defined in `app/domains/lupademo/tenants.json` (demo keys).

### Scenarios

Pre-built demo paths in `scenarios.py` (fallback) and `specs/scenarios/demo_scenarios.json`:

1. `building_vantaa_residential` — Building permit, Vantaa
2. `environmental_industrial_generic` — Environmental permit, industrial
3. `event_public_vantaa` — Public event permit
4. `national_suggestion_service` — API integration demo

### Authority workflow

Separate UI at `/cases/{case_id}/lupademo/authority/`:

- Queue view with filters
- Detail view with AI summary, additional info draft, decision draft
- Caseworker observations (issues to track)
- Status updates

## How the DB works

- Single SQLite file at `data/keha_lv.db`
- Tables: `cases`, `files`, `artifacts`, `app_settings`, `llm_overrides`, `ai_logs`
- `artifacts` is the main document store: `id = case_id:type:name`, JSON in `data_json`
- `app_settings` is a key-value store (used for active case/scenario tracking)
- No migrations — schema created on first `DB.__init__`

## How the mock LLM works

Three env vars control it, all read once at startup into `Settings`:

| Var | Default | Effect |
|-----|---------|--------|
| `KEHA_LV_LLM_PROVIDER` | `mock` | `"mock"` or `"azure"` |
| `KEHA_LV_FORCE_MOCK_LLM` | off | Overrides to mock even if provider=azure |
| `KEHA_LV_DEMO_MODE` | `true` | If on + provider=azure but no key → falls back to mock |

Decision chain in `create_llm_client()`:
1. If `provider == "mock"` → `MockLLMClient`
2. If `force_mock_llm` → `MockLLMClient`
3. If `demo_mode` AND no API key → `MockLLMClient`
4. Otherwise → `AzureOpenAIClient`

`MockLLMClient` responds instantly with pre-crafted Finnish responses.
No network calls, no API keys needed. Demo scenarios are pre-seeded.

## How to run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./run.sh
# → http://127.0.0.1:8001
```

## How to test

```bash
PYTHONWARNINGS=ignore KEHA_LV_FORCE_MOCK_LLM=true .venv/bin/python -m unittest discover tests
```

Tests use the mock LLM and a temporary database. No Azure credentials or `data/`
state are needed. `tests/test_smoke.py` forces mock mode during module import, so
environment overrides do not turn the normal suite into live Azure tests.

For an optional live Azure check, source the ignored `.env`, explicitly set
`KEHA_LV_LLM_PROVIDER=azure` and `KEHA_LV_FORCE_MOCK_LLM=false`, and use temporary
`KEHA_LV_DB_PATH` and `KEHA_LV_UPLOADS_DIR` values. Verify persisted provenance
with `provider == "azure"` and `fallback_used is False`; plausible output alone
can hide a deterministic fallback. Never print credentials, endpoint values,
prompts, or raw responses. See
`docs/ai-functionality-validation-handoff.md` for the procedure, completed
contracts, current limitations, and sanitized validation results.

## Templates

Jinja2 templates in `app/domains/lupademo/templates/`. Single base template:

- `light_base.html` — All 21 templates extend it directly (shell chrome, navigation, footer)

When adding a new template, extend `light_base.html`.
The shared `templates` Jinja2Templates instance lives in `routes/template_filters.py`
and is imported by all 5 route modules — do not create a second instance.

## Files to never read in full unless essential

- `app/domains/lupademo/services/application.py` — ~3000 lines; use `read_symbol` or `read_enclosing` to target specific functions
- `app/domains/lupademo/services/intake.py` — ~1700 lines; same approach
- `app/domains/lupademo/routes/applicant.py` — ~382 lines; use `read_enclosing` for individual route handlers
- `app/core/llm_utils.py` — imported by services, use `read_symbol` for `call_llm_json`

## Common patterns when editing

- New route → add to the matching `routes/*.py` module (applicant/authority/api_v1/admin/demo_lab — see architecture above) inside the `create_{name}_router()` function; no prefix needed, routes share the same URL space
- New business logic → add to the matching `services/*.py` module (core/jobs/intake/application — see architecture above); only import *downward* (application may import intake/jobs/core, intake may import jobs/core, jobs may import core, core imports nothing intra-package)
- New artifact type → use `svc.save_artifact()` / `svc.artifact_json()` pattern
- New form field → add to schema in `services/core.py` `PERMIT_SCHEMAS` (the big dicts)
- New scenario → add to `specs/scenarios/demo_scenarios.json` (canonical source); `scenarios.py` has a minimal 1-entry fallback for when the JSON file is missing
- Access DB → use the `db()` getter injected into router closures
- Access settings → use the `settings()` getter injected into router closures

### Coupling hotspots (change carefully)

These three functions are the most-connected nodes in the codebase (graphify-verified).
Changes to their signatures or behavior ripple everywhere:

- `save_artifact()` — every write path flows through it (56 callers, graphify-verified)
- `artifact_json()` — every read path flows through it (47 callers, graphify-verified)
- `event()` — all audit-trail entries flow through it (44 callers, graphify-verified)
- `application_id_for()` — central permit-type resolver (45 callers)

Before changing any of these, check `svc.__init__.py` for their re-exports — that's the
single public API surface they expose.
