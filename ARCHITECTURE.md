# Architecture — KEHA LV Demo

Single source of truth for how modules connect. Read this first.

## Folder map

```
keha-lv-jaettava/
  app/
    main.py                         FastAPI entry point, mounts router, /healthz
    infra/                          Infrastructure (no business logic)
      config.py                       Settings from env vars
      db.py                           SQLite: cases, files, artifacts, settings
      llm_factory.py                  create_llm_client() → mock | Azure
      llm_utils.py                    call_llm_json(), rate limiting
      mock_llm.py                     Rule-based mock LLM (default, offline)
      azure_client.py                 Azure OpenAI client
    domains/lupademo/
      routes/                        HTTP handlers (thin — delegate to services)
        __init__.py                    create_lupademo_router()
        applicant.py                   42 handlers — wizard, intake, forms, chat
        authority.py                   9 handlers — queue, detail, observations
        api_v1.py                      7 handlers — external suggestion-service API
        admin.py                      22 handlers — criteria admin, playground, jobs
        demo_lab.py                   14 handlers — benchmark, metrics, audit
        common.py                     Shared route helpers
        template_filters.py           Jinja2 filters
      services/                      Business logic (no HTTP)
        domain.py                     Dataclasses, schemas, path constants, prompts (~350L)
        db_helpers.py                 artifact_json, save_artifact, event (~80L)
        core.py                       Case/app lifecycle, common-data sync (~250L)
        criteria.py                   Permit criteria CRUD, history, rules (~180L)
        jobs.py                       AI job lifecycle (~350L)
        intake.py                     Attachments, ingestion, extraction, agents (~1500L)
        application.py                Suggestions, checks, packaging, authority, chat,
                                      metrics, external API (~2700L)
        __init__.py                   Re-exports — `import services as svc` works
      templates/                     22 Jinja2 HTML templates
      static/                        CSS + fonts
      specs/                         Runtime specs (JSON — loaded by app)
        form_schemas/                  3 permit form schemas
        criteria_sets/                 3 permit criteria sets
        prompts/                      11 AI agent prompt templates (.md)
        scenarios/                     Demo scenarios + material stories
      scenarios.py                   Demo scenario seeding (code fallback)
      security.py                    API key auth for suggestion-service
      tenants.json                   Demo API tenants + keys
  docs/                             Design docs (NOT loaded at runtime)
    product-architecture.md        Product design — user journeys, principles
    archive/                       Historical / design-phase artifacts
      v5-specs/                      Early design sketches (domain, roles, UI, workflow, contracts)
  tests/                            Smoke tests (mock LLM, temp DB)
  testiaineisto/                    Test materials (sample zip uploads, gitignored)
  graphify-out/                     Dependency graph analysis artifacts (gitignored)
```

## Dependency graph

```mermaid
graph TD
    subgraph "Routes (HTTP)"
        applicant["routes/applicant.py"]
        authority["routes/authority.py"]
        api_v1["routes/api_v1.py"]
        admin["routes/admin.py"]
        demo_lab["routes/demo_lab.py"]
    end

    subgraph "Services (business logic)"
        application["application.py<br/>suggestions, checks, packaging,<br/>authority, chat, metrics, API"]
        criteria["criteria.py<br/>permit criteria CRUD"]
        intake["intake.py<br/>attachments, ingestion,<br/>extraction, agent runner"]
        jobs["jobs.py<br/>AI job lifecycle"]
        core_svc["core.py<br/>case/app lifecycle,<br/>common-data sync"]
        domain["domain.py<br/>dataclasses, schemas,<br/>path constants"]
        db_helpers["db_helpers.py<br/>artifact_json, save_artifact,<br/>event, type helpers"]
    end

    subgraph "Infrastructure"
        config["infra/config.py"]
        db["infra/db.py"]
        llm["infra/llm_factory.py<br/>infra/llm_utils.py"]
        mock["infra/mock_llm.py"]
        azure["infra/azure_client.py"]
    end

    applicant --> application
    authority --> application
    api_v1 --> application
    admin --> application
    demo_lab --> application

    application --> intake
    application --> criteria
    application --> jobs
    intake --> jobs
    jobs --> core_svc
    criteria --> core_svc
    core_svc --> domain
    core_svc --> db_helpers

    application --> llm
    intake --> llm
```

**Key rule:** dependencies flow downward. No circular imports.

## Data flow

```
Browser → FastAPI route → svc.function() → DB artifact (SQLite JSON doc)
                                              ↓
                                         AI agent (mock or Azure)
                                              ↓
                                         svc.save_artifact() → DB
```

All state lives in SQLite `artifacts` table as JSON documents keyed `{case_id}:{type}:{name}`.

## Wizard step → API calls

The applicant wizard (`routes/applicant.py`) is a 6-step flow. Each step renders
via `GET /cases/{case_id}/lupademo/application/{application_id}/wizard/{step}`
and triggers the POST endpoints below. `{application_id}` is the permit type
(e.g. `environmental_industrial`). All AI work runs as background jobs polled
through `/api/v1/jobs/{job_id}`.

### Step 1 — Perustiedot (LV-yhteiset tiedot)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/cases/{case_id}/lupademo/application/{application_id}/save` | Save common/applicant data; `redirect_to` → wizard/2 |

### Step 2 — Aineistot (upload + analyze)

| Method | Path | Purpose | AI job |
|--------|------|---------|--------|
| `POST` | `.../materials/upload-files` | Multi-file / zip / text upload | `material_ingest` |
| `POST` | `.../materials/upload-zip` | Zip-only upload | `material_ingest` |
| `POST` | `.../materials/add-text` | Add text material (AI-only) | — |
| `POST` | `.../attachments/{id}/scope` | Change delivery scope | — |
| `POST` | `.../attachments/{id}/metadata` | Update primary type / scope | — |
| `POST` | `.../attachments/{id}/delete` | Delete attachment | — |
| `POST` | `.../ai/intake-analysis` | **"Analysoi aineisto AI:lla"** — light analysis, extracts fields into review queue, no gap/criteria check | `intake_analysis` (`full_check=False`) |
| `POST` | `.../save` | Save the form fields | — |

`material_ingest` builds the case vector store (`ingest_file_row_to_case_vector_store`).
The light `intake_analysis` writes `field_extraction_queue` (feeds step 3's
"Tarkista AI-ehdotukset") and `intake_analysis` artifact. It does **not** make
suggestions or run criteria.

### Step 3 — AI-ehdotukset (review suggestions)

| Method | Path | Purpose | AI job |
|--------|------|---------|--------|
| `POST` | `.../ai/suggest-form` | **"AI-suositukset liitteistä"** — batch suggestions for all `ai_suggest` fields | `form_suggestions` |
| `POST` | `.../ai/suggest-field` | Per-field suggestion (dropdown) | `field_suggestion` |
| `POST` | `.../extractions/accept` | Accept an extraction-queue item (from step 2) | — |
| `POST` | `.../suggestions/{id}/accept` | Accept one AI suggestion | — |
| `POST` | `.../suggestions/accept-selected` | Accept selected / all suggestions | — |
| `POST` | `.../suggestions/{id}/reject` | Reject one suggestion | — |
| `POST` | `.../chat` | Contextual chat (form post) | — |
| `POST` | `.../chat/messages` | Contextual chat (JSON) | — |
| `POST` | `.../chat/stream` | Contextual chat (SSE stream) | — |
| `POST` | `.../voice/mock` | Voice assistant (mock transcript) | — |

`form_suggestions` runs `create_field_suggestion` per field **within one job**
(no per-field sub-jobs) and marks each field agent complete as it finishes.

### Step 4 — Tarkistus (AI + rule check)

| Method | Path | Purpose | AI job |
|--------|------|---------|--------|
| `POST` | `.../ai/check` | **"Aja tarkistus"** — full check: deterministic + completeness/consistency agents + criteria assessor + precheck report | `application_check` |

Writes `check_report`, `criteria_assessment`, `applicant_precheck_report`,
`precheck_gate`. Blocking issues block submission.

### Step 5 — Paketointi (submission package)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `.../wizard/5` | Renders package; composes `submission_package` on demand if attachments exist |
| `POST` | `.../submit` | Submit — runs `ensure_fresh_precheck_or_raise`; redirects to receipt or back to step 4 on blocking issues |

### Step 6 — Vastaanotto (receipt)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `.../wizard/6` | Renders receipt view |
| `GET` | `.../receipt` | Receipt page |

### Job polling (used by the AI banner)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/v1/jobs/{job_id}` | Poll job status (progress, current agent) |
| `GET` | `/api/v1/jobs/{job_id}/stream` | SSE status stream |
| `GET` | `/api/v1/jobs/{job_id}/events` | Agent event list |

The banner (`templates/shared/_ai_job_bar.html`) polls every 4s and auto-reloads
with a `?t=` cache-buster when all jobs reach a terminal state.

## Package workbench → API calls

The package workbench (`/applicant/packages/*`) is a **separate path** from the
wizard. It starts from raw material (no pre-seeded case) and runs the **full**
analysis pipeline in sequence. Routes live in `routes/applicant.py`.

### Create + ingest

| Method | Path | Purpose | AI job |
|--------|------|---------|--------|
| `GET` | `/applicant/packages/new` | New-package form | — |
| `POST` | `/applicant/packages/new` | Create case + pending package, start ingest | `material_ingest` |
| `GET` | `/applicant/packages/{package_id}` | Render workbench (polls active job) | — |

`POST /applicant/packages/new` calls `scenarios.create_generic_project(...)`
(creates a fresh case), then `start_material_ingest_job` +
`execute_material_ingest_job` in the background. This builds the case vector
store and marks the package `ready`.

### Analyze (full pipeline)

| Method | Path | Purpose | AI job |
|--------|------|---------|--------|
| `POST` | `/applicant/packages/{package_id}/analyze` | **"Analysoi hakemuspaketti AI:lla"** — full analysis | `intake_analysis` (`full_check=True`) |

Guards: 409 if package not `ready` or an `intake_analysis` job is already
running. Runs `start_intake_analysis_job` (default `full_check=True`) +
`execute_intake_analysis_job` in the background.

The full path (`_build_intake_analysis_result` with `full_check=True`) runs the
**entire** pipeline in one job:
1. `material_classifier` — classify attachments
2. `structure_extractor` — extract fields into `field_extraction_queue`
3. `gap_analyzer` — `package_analysis_from_current_state` (completeness,
   consistency agents, criteria, findings, readiness score)
4. `question_generator` — build questions

Writes `intake_analysis`, `field_extraction_queue`, `package_analysis_result`,
`structure_extraction_result`. It does **not** run the step-4 `application_check`
— that's a separate job the user triggers from the wizard.

### Handoff to wizard

The workbench links into the wizard for review/fix:
- "Katso kaikki tarkistukset" → `wizard/4`
- "Korjaa" / "Muokkaa aineistoa" → `wizard/2`
- Accept extraction → `.../extractions/accept` (shared with wizard step 3)

### Job polling

Same endpoints as the wizard banner: `GET /api/v1/jobs/{job_id}` (+ `/stream`,
`/events`). The workbench's `package_context` tracks the active
`material_ingest` / `intake_analysis` job and shows it in the progress bar.

> **Note:** the workbench runs ingest → full analysis as two separate jobs
> (each triggered by its own button). It does **not** auto-chain them — the
> user clicks "Analysoi" after the package is `ready`.

## Where to find things

| Question | Module |
|----------|--------|
| What does an Issue/Suggestion look like? | `services/domain.py` |
| How do I read/write artifacts? | `services/db_helpers.py` |
| How do I create a case or application? | `services/core.py` |
| How do I manage permit criteria? | `services/criteria.py` |
| How does the AI job lifecycle work? | `services/jobs.py` |
| How are attachments uploaded/extracted? | `services/intake.py` |
| How does the application check run? | `services/application.py` → `run_application_check()` |
| How does submission packaging work? | `services/application.py` → `compose_submission_package()` |
| Where are the HTTP route handlers? | `routes/{applicant,authority,api_v1,admin,demo_lab}.py` |
| Where are the AI prompt templates? | `specs/prompts/*.prompt.md` |
| Where are the permit form schemas? | `specs/form_schemas/*.schema.json` |
| Where are the permit criteria sets? | `specs/criteria_sets/*.json` |
| How is the DB configured? | `infra/config.py` + `infra/db.py` |
| How do I swap mock → Azure? | Set `KEHA_LV_LLM_PROVIDER=azure` in `.env` |

## Key design decisions

1. **Artifacts as JSON in SQLite.** Every domain object is a JSON doc, not a relational table. Simple, flexible, no migrations.
2. **Mock LLM by default.** Runs offline with deterministic Finnish responses. No API keys needed.
3. **`svc.*` pattern.** All services re-exported through `__init__.py`. Routes never import from individual service modules — they use `import services as svc; svc.xxx()`.
4. **Routes are thin.** Route handlers validate input, call a service function, return a template/redirect. No business logic in routes.
5. **`app/infra/` not `app/core/`.** Named "infra" to avoid collision with `services/core.py`. Infrastructure code has zero business logic.
6. **Specs live in `app/`.** Runtime specs (schemas, criteria, prompts) live next to the code that loads them. Design archives live in `docs/specs/`.
