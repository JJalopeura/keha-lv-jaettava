# New Agent Onboarding — KEHA LV

A short ramp-up for an AI agent (or human) starting work in this repo. Read
these in order; each builds on the last. This is the "fast path" — for the full
module map and dependency graph, see `ARCHITECTURE.md`.

## 1. Read these three files first

| File | What it gives you |
|------|-------------------|
| `AGENTS.md` | The mental model: what the project is, scope boundaries, key concepts, how the DB/AI backend work, common edit patterns, coupling hotspots. **Start here.** |
| `ARCHITECTURE.md` | The module map, dependency graph (Mermaid), data flow, and the wizard/workbench route tables. |
| `docs/route-inventory.md` | Every HTTP route (method, path, handler, module, template), enforced by a guard test. |

## 2. Run these two commands

```bash
# Start the app (live Azure AI Foundry, port 8003)
./run.sh

# Run the full test suite (deterministic fake LLM, temp DB)
PYTHONWARNINGS=ignore .venv/bin/python -m unittest discover tests

# Optional: lint (focused config — see pyproject.toml)
ruff check app tests
```

## 3. The mental model in 30 seconds

- **FastAPI app** → thin **routes** (`routes/*.py`) → **services** (`services/*.py`)
  → **SQLite artifacts** (JSON docs keyed `{case_id}:{type}:{name}`).
- Everything is a **case** (an "LV project"). A case holds **applications** (one
  per permit type). Application state lives in `permit_application:<permit_type>`.
- **Two entry paths** to fill an application — the **package workbench**
  (`/applicant/packages/*`, material-first) and the **wizard**
  (`/projects/new` → wizard, form-first). They share state but are separate
  journeys. **Do not mix-match them.** See `AGENTS.md` → "Two entry paths".
- **Azure AI Foundry only** — every AI call is a Foundry Responses API request
  (`core` = JSON work, `chat` = prose). No mock or offline provider exists; a
  missing endpoint/deployment/key fails loudly.
- **`svc.*` pattern** — all services re-exported through `services/__init__.py`
  (with an explicit `__all__`). Routes use `import services as svc; svc.xxx()`.

## 4. Where to look for a given concern

| Concern | Module |
|---------|--------|
| What does an Issue/Suggestion look like? | `services/domain.py` |
| How do I read/write artifacts? | `services/db_helpers.py` |
| How do I create a case/application? | `services/core.py` |
| How do I manage permit criteria? | `services/criteria.py` |
| How does the AI job lifecycle work? | `services/jobs.py` |
| Attachments / ingestion / extraction | `services/intake.py` |
| Suggestions / checks / packaging / authority / chat / metrics / API | `services/application.py` |
| HTTP route handlers | `routes/{applicant,authority,api_v1,admin,demo_lab}.py` |
| AI prompt templates | `specs/prompts/*.prompt.md` |
| Permit form schemas | `specs/form_schemas/*.schema.json` |
| Permit criteria sets | `specs/criteria_sets/*.json` |

## 5. Files to avoid reading in full

- `services/application.py` (~2700 lines) and `services/intake.py` (~1500 lines)
  are large. Use `read_symbol` / `read_enclosing` to target specific functions
  rather than reading them top to bottom.

## 6. Before you change code

- **New route** → add to the matching `routes/*.py` module, then add a row to
  `docs/route-inventory.md` (the guard test will fail otherwise).
- **New business logic** → add to the matching `services/*.py` module; import
  only *downward* (application → intake → jobs → core → domain).
- **New artifact type** → use `svc.save_artifact()` / `svc.artifact_json()`.
- **New form field** → add to `PERMIT_SCHEMAS` in `services/core.py`.
- **New scenario** → add to `specs/scenarios/demo_scenarios.json`.
- **Coupling hotspots** — `save_artifact()`, `artifact_json()`, `event()` are the
  most-connected functions. Change their signatures carefully.
