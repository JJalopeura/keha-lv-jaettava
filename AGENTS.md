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

> **New here?** Read [`docs/agent-onboarding.md`](docs/agent-onboarding.md) first — a short ramp-up (files to read, commands to run, mental model, where to look).
>
> **See `ARCHITECTURE.md` for the full module map, dependency graph (Mermaid), and data flow diagram.** This section summarizes key navigation points.

```
app/
  main.py                    FastAPI app, mounts router + static, /healthz
  infra/                     Infrastructure (no business logic):
    config.py                  Settings dataclass from env vars
    db.py                      SQLite store
    llm_factory.py             create_llm_client() → mock or Azure
    llm_utils.py               call_llm_json(), rate limiting
    mock_llm.py                Rule-based mock (default, offline)
    azure_client.py            Azure OpenAI client
  domains/lupademo/
    routes/                  HTTP handlers (thin — delegate to services):
      __init__.py               thin create_lupademo_router that mounts 5 sub-routers
      applicant.py              68 handlers — wizard, intake, application studio, common
                                data, packaging, chat/voice (~1171 lines)
      authority.py              9 handlers — queue, detail, observations, summary/memo
      api_v1.py                  17 handlers — external suggestion-service endpoints
      admin.py                   12 handlers — criteria admin, API demo/playground
      demo_lab.py               24 handlers — benchmark, metrics, audit, UI-only demo
    services/                Business logic package:
      domain.py                 Dataclasses, type contracts, schemas, path constants,
                                prompt loading. No intra-package deps. (~350 lines)
                                → "what does an Issue look like?"
      db_helpers.py             Persistence primitives: artifact_json, save_artifact,
                                event, _dict/_list/_plain, source_ref. (~80 lines)
                                → "how do I read/write artifacts?"
      core.py                   Application orchestration: case setup, application
                                lifecycle, common-data sync. Re-exports from domain
                                and db_helpers. (~250 lines)
                                → "how do I create a case/app?"
      criteria.py               Permit criteria CRUD: load, save, version history,
                                rule management, severity ordering. (~180 lines)
                                → "how do I manage permit criteria/rules?"
      jobs.py                   Generic AI job lifecycle (start/advance/complete/
                                cancel/retry). Depends on core only. (~350 lines)
      intake.py                 Attachments, material packages, ingestion, extraction,
                                classification, retrieval, agent runner.
                                Depends on core+jobs. (~1500 lines)
      application.py            Suggestions, checks, packaging, authority views,
                                chat/voice, metrics, external API. The largest module.
                                Depends on core+jobs+intake+criteria. (~2700 lines)
      __init__.py               Re-exports everything. `import services as svc` works.

                                Dependency order (graphify-verified):
                                application → intake → jobs → core → domain + db_helpers
                                criteria → core → domain + db_helpers
                                No cycles exist.
    scenarios.py             Demo scenario definitions + case creation (4 built-in scenarios, fallback in code)
    security.py              API key / tenant auth for suggestion-service endpoints
    static/style.css          Single CSS file
    templates/*.html          31 Jinja2 templates (wizard, authority, admin, API demo, etc.)
    specs/                     JSON specs: form schemas, permit criteria, scenarios, roles, eval definitions
docs/archive/v5-specs/        Archived v5 reference specs (not loaded at runtime)
data/                         Created at first run: SQLite DB + uploads/ (gitignored)
testiaineisto/                Test packages (sample zip uploads)
tests/                        Smoke tests (mock LLM, temp DB)
rules/                        ast-grep rules for pi-lens diagnostics
```

## Key concepts

### Cases

A customer owns many cases (`case_id`, UUID), which are the durable LV projects or
"hankkeet." Every customer-facing case has a `customer_id` owner.

### Customers

The mock applicant login selects one of three demo customers: P2X, Ren-Gas Oy,
or Sharpcell. The signed browser session contains the selected `customer_id`.
There is no customer switcher. Logout clears the session, and logging in again
restores the selected customer's persisted hankkeet, applications, and packages.

Customer profile data is reusable input for new hankkeet. Each hanke keeps its
own `lv_common_data` snapshot so later profile edits do not rewrite old work.

### Permit types

Three demo permit types (`PERMIT_SCHEMAS`), each with a JSON form schema + criteria set:

- `building_residential` — Rakennuslupa (building permit, residential)
- `environmental_industrial` — Ympäristölupa (environmental permit, industrial)
- `event_public` — Tapahtumalupa (public event permit)

### Applications

A hanke can hold multiple applications (one per permit type). Each is an artifact
keyed `permit_application:<permit_type>` with `form_data`, `field_sources`, and
`status`. Material packages and attachments belong to the selected application.

### Wizard (6 steps)

1. **LV-yhteiset tiedot** — Common applicant data (name, business ID, address, etc.)
2. **Aineisto** — Upload materials (zip, files, text), view attachments, run AI intake analysis
3. **Hakemus** — AI-suggested form fields, accept/reject/edit, contextual chat, voice assistant
4. **Tarkistus** — AI permit check (blocking/major/minor issues), submit gate
5. **Paketointi** — Submission package preview (official attachments vs AI context)
6. **Vastaanotto** — Receipt / confirmation

### Two entry paths (do NOT mix-match)

There are **two distinct ways to work with an application**. The kevytasiointi
journey creates a real hanke and application immediately and links back to the
service view; the form-first wizard journey also creates the hanke and
application first. Keep them independent — do not merge, alias, or cross-wire
them.

| | **A. Kevytasiointi** (material-first) | **B. Wizard** (form-first) |
|---|---|---|
| Entry URL | `/applicant/packages/new` | `/projects/new` (with `start_at=wizard`) |
| Template | `applicant/package_new.html` → `applicant/kevytasiointi.html` | `applicant/applicant_start.html` → `applicant/wizard.html` |
| POST handler | `applicant_package_create` | `create_project` |
| Starts with | Permit type + municipality + raw material | New hanke basics + permit type |
| First step | AI ingest & analysis | Wizard step 1 (common data) |
| Landing | `/applicant/packages/{package_id}` | `/cases/{case_id}/lupademo/application/{application_id}/wizard/1` |
| Nav label | `Kevytasiointi` | `Uusi hanke` |

- **Path A** (`/applicant/packages/*`) is the **Kevytasiointi service view** —
  choose a permit type and municipality, upload raw material, and fill the
  application with AI prose suggestions in a persistent side panel (per-field
  copy buttons, no criteria gate, no review phase). Submitting the LV-side form
  creates a REAL hanke (`created_from="kevytasiointi"`) right away — there is no
  promote step. All links from dashboards and hanke pages for these hankes point
  directly at `/applicant/packages/{package_id}`, never the wizard. Routes live
  in `routes/applicant.py` under the `applicant_package_*` /
  `kevytasiointi_*` handlers.
- **Path B** (`/projects/new` → wizard) is the **6-step wizard** — fill the form
  first, add materials in step 2. Routes live in `routes/applicant.py` under the
  `create_project` / `application_wizard` handlers.

The two entry flows must never be conflated. When adding a feature, decide which
path it belongs to and keep it there.

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
- Tables: `customers`, `cases`, `files`, `artifacts`, `app_settings`, `llm_overrides`, `ai_logs`
- `package_reviews` stores standalone package-review sessions (legacy review
  rows; kevytasiointi no longer creates them).
- `cases.customer_id` is the customer-workspace ownership key. API `tenant_id`
  remains a separate external suggestion-service concept.
- `artifacts` is the main document store: `id = case_id:type:name`, JSON in `data_json`
- `app_settings` remains for demo/API scenario state; applicant customer identity
  lives in the signed browser session, not in a global setting.
- No migrations — schema created on first `DB.__init__`

## How the mock LLM works

When `KEHA_LV_LLM_PROVIDER=mock` (default), `mock_llm.py` responds instantly:

- For field suggestions: returns plausible Finnish values from prompt context
- No network calls, no API keys needed
- Demo scenarios are pre-seeded so AI features work out of the box

## How to run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./run.sh
# → http://127.0.0.1:8002
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

Jinja2 templates in `app/domains/lupademo/templates/`. Base template:

- `shared/light_base.html` — Root layout (full HTML doc, header, nav, footer); used by all leaf templates

Template inheritance: all leaf templates extend `shared/light_base.html` directly.
When adding a new template, extend `shared/light_base.html`.

## Files to never read in full unless essential

- `app/domains/lupademo/services/application.py` — ~3000 lines; use `read_symbol` or `read_enclosing` to target specific functions
- `app/domains/lupademo/services/intake.py` — ~1700 lines; same approach
- `app/domains/lupademo/routes/applicant.py` — ~1171 lines; use `read_enclosing` for individual route handlers
- `app/core/llm_utils.py` — imported by services, use `read_symbol` for `call_llm_json`

## Common patterns when editing

- New route → add to the matching `routes/*.py` module (applicant/authority/api_v1/admin/demo_lab — see architecture above) inside the `create_{name}_router()` function; no prefix needed, routes share the same URL space
- New business logic → add to the matching `services/*.py` module (core/jobs/intake/application — see architecture above); only import *downward* (application may import intake/jobs/core, intake may import jobs/core, jobs may import core, core imports nothing intra-package)
- New artifact type → use `svc.save_artifact()` / `svc.artifact_json()` pattern
- New form field → add to schema in `services/core.py` `PERMIT_SCHEMAS` (the big dicts)
- New scenario → add to `specs/scenarios/demo_scenarios.json` (runtime) or `scenarios.py` (fallback)
- Access DB → use the `db()` getter injected into router closures
- Access settings → use the `settings()` getter injected into router closures

### Coupling hotspots (change carefully)

These three functions are the most-connected nodes in the codebase (graphify-verified).
Changes to their signatures or behavior ripple everywhere:

- `save_artifact()` — every write path flows through it (56 callers)
- `artifact_json()` — every read path flows through it (48 callers)
- `event()` — all audit-trail entries flow through it (45 callers)

Before changing any of these, check `svc.__init__.py` for their re-exports — that's the
single public API surface they expose.
