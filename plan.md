# Plan: making the LV AI-demo production-ish

## 1. Current state (what I found)

- **Code is concentrated in two giant files**: `routes.py` (1264 lines, 122
  functions, one big `create_lupademo_router()`) and `services.py` (5524
  lines, 182 functions/classes, no submodules). Everything — intake,
  suggestions, checks, packaging, authority, API, metrics, benchmark lab —
  lives in the same two files. This is the main reason the codebase "feels
  like a mess": there is no seam between subsystems.
- **AI output shapes are inconsistent**: `PackageAnalysisResult`,
  `Issue`/`Finding`, extraction items, and job payloads are dataclasses in
  some places and raw dicts elsewhere (`_dict`/`_list`/`_plain` helpers exist
  specifically to paper over this). Templates reach into
  `field_extraction_queue.get('items', [])`, `item.suggested_value`,
  `finding.evidence_refs` with mixed dict/attr access — a template typo or a
  missing key fails silently (Jinja swallows it) instead of erroring, which
  is exactly why "AI analyze results are not easily usable."
- **There are too many parallel UI entry points** for what is conceptually
  one flow: `landing.html`, `applicant_start.html`, the 6-step
  `wizard.html` (735 lines), the newer `package_new.html` /
  `package_workbench.html` (a *second*, more modern flow that already
  covers readiness/findings/extraction review), `lv_project.html`,
  `authority_queue/detail.html`, `api_demo.html`, `ui_only.html`,
  `presenter.html`, `benchmark_lab.html`, `production_hardening.html`,
  `metrics.html`. Several of these overlap in purpose (there are two ways to
  upload material, two ways to see AI results — wizard step 3/4 vs. the
  workbench — and the workbench even links back into the old wizard for
  "see all checks").
- **There is already a target design nobody finished building**:
  `docs/specs/v5/*.yaml` defines a clean target IA (`target_routes.yaml`,
  `page_blueprints.yaml`), a typed AI job contract (`ai_job_contract.yaml`),
  a typed package/document/finding contract
  (`application_package_contract.yaml`), copy text, telemetry events, and
  acceptance tests (`acceptance_tests.yaml`, AT01–AT12). The
  `/applicant/packages/new` + `/applicant/packages/{id}` workbench in
  `routes.py`/`services.py` is a **partial, uncoordinated implementation of
  that spec** — it exists alongside the old wizard rather than replacing it.
  This spec is the fastest path to "clear usability, clear functions that
  return good data": finish it instead of inventing a new IA.
- **No module boundaries = no enforceable contracts.** Because
  `run_json_agent`, `normalize_agent_payload`, and every feature function
  live in one file with `Any`-typed `db`/`settings` params, nothing stops a
  route from reading a field that doesn't exist on the result it just got.

## 2. Decision

Adopt `docs/specs/v5` as the target IA (it already matches what "good" looks
like) and use it as the spec to finish, not a new redesign to invent. Two
work streams, doable independently but most valuable together:

- **A. Data layer**: make every AI-producing function return one typed,
  validated shape per concept (Job, Package, DocumentAsset, Finding,
  ExtractionCandidate), matching `ai_job_contract.yaml` /
  `application_package_contract.yaml`. Templates/API consume the typed
  shape only — no raw dict poking.
- **B. UI layer**: promote the applicant workbench
  (`/applicant/packages/{id}`) to be the single applicant-facing screen;
  retire the old 6-step wizard's *AI-results* steps (3/4) into it; move
  demo-only surfaces (`presenter`, `benchmark_lab`,
  `production_hardening`, `ui_only`, raw `api_demo`) under `/demo-lab` as
  the spec already dictates, out of primary nav.

## 3. Phased steps

### Phase 0 — Safety net (before touching anything)

1. Run `.venv/bin/python -m unittest discover tests` to record the current
   passing baseline.
2. Add one characterization test per major flow that doesn't have one yet:
   package upload → analyze → readiness score present; suggestion
   accept/reject; submission gate. (Smallest thing that fails if refactor
   breaks behavior — no new framework, extend `tests/test_smoke.py`.)

### Phase 1 — Typed AI result contracts (services.py)

1. Turn `PackageAnalysisResult`, `PackageFinding`, `ExtractedField`/
   extraction-queue items, and the AI job dict into real dataclasses with
   `to_dict()`/`from_dict()`, matching the field lists already specified in
   `docs/specs/v5/application_package_contract.yaml` and
   `ai_job_contract.yaml` (fields like `severity`, `category`,
   `evidence_refs`, `status`, `eta_seconds_remaining` are already named
   there — just enforce them).
2. Delete the `_dict`/`_list`/`_plain`/`_as_dict` shim functions once
   callers pass typed objects instead of "maybe a dict, maybe a dataclass."
3. Every template that reads AI output (`package_workbench.html`,
   `wizard.html` steps 3/4, `authority_detail.html`) switches to the typed
   field names only. A missing/renamed field now breaks loudly (Jinja
   `StrictUndefined` — turn this on in `main.py`'s Jinja env) instead of
   rendering blank.
4. Verification: AT05/AT06 from `acceptance_tests.yaml` (readiness score +
   findings + extraction queue render; extraction never mutates form before
   accept) — write as unit tests against the dataclasses, not just visual.

### Phase 2 — Split the two mega-files

1. **DONE.** Split `services.py` into `app/domains/lupademo/services/` based on
   the actual call-dependency graph (analyzed mechanically, not guessed):
   `core.py` (dataclasses/contracts, form schemas & criteria, artifact/db
   primitives, prompt rendering, common-data sync — no intra-package deps),
   `jobs.py` (generic AI job lifecycle, depends on core only), `intake.py`
   (attachments/material packages/ingestion/extraction/retrieval, depends on
   core+jobs), `application.py` (suggestions, checks, package analysis,
   submission packaging, authority, chat/voice, metrics/benchmark, external
   API — depends on core+jobs+intake). One deliberate job-runner ->
   feature-finalizer callback uses a local/deferred import
   (`_advance_ai_job_in_store` in `jobs.py`) rather than forcing an
   artificial extra module. `services/__init__.py` re-exports everything so
   `routes.py`/`scenarios.py`'s `services as svc; svc.xxx()` call sites are
   unchanged. The originally-planned finer 7-way split (suggestions/checks/
   packaging/authority as separate files) was not used — the actual
   dependency graph showed them tightly mutually coupled (suggestions calls
   checks, checks calls intake helpers, authority calls both), so forcing
   them apart would have meant fragile cross-imports for no real clarity
   gain; they're grouped in `application.py` instead.
2. **DONE.** Split `routes.py` into `app/domains/lupademo/routes/` package:
   `applicant.py` (42 handlers — wizard, intake, application studio, common
   data, packaging, chat/voice), `authority.py` (9 handlers — queue,
   detail, observations, summary/memo), `api_v1.py` (7 handlers — external
   suggestion-service endpoints), `admin.py` (22 handlers — criteria admin,
   API demo/playground, internal AI job API), `demo_lab.py` (14 handlers —
   benchmark, metrics, audit, UI-only demo). Each module exports
   `create_{name}_router(db_getter, settings_getter) -> APIRouter`.
   `routes/__init__.py` contains the thin `create_lupademo_router` that
   mounts all 5 sub-routers. The `app/main.py` import is backward-compatible
   (`routes` now resolves to `routes/__init__.py`). Context helpers
   (`context`, `_context_app`) live in `applicant.py`; each sub-router has
   its own `db()`/`settings()` closures. Largest route file: applicant.py
   at 382 lines.
3. Verification: `lsp_diagnostics`/`lens_diagnostics` clean (all blocking
   errors are sandbox `reportMissingImports` noise — pyrightconfig.json in
   repo root), stubbed import test confirms all 5 sub-routers build, user
   smoke tests pass. File sizes: largest route file applicant.py at 382
   lines ✓, largest service file application.py at 3002 lines (still a
   monolithic domain bucket — candidate for a surgical split later only
   when navigation pain materializes).

### Phase 3 — One applicant flow

**Status: DONE (pragmatic stopping point)**

 1. DONE. Landing page rewrote to match `docs/specs/v5/copy_texts_fi.yaml` CTAs.
 2. DONE. Wizard steps 3 (suggestions) and 4 (checks) redirect to
    `/applicant/packages/{package_id}` workbench, falling back to step 2
    when no `current_material_package`.
 3. DONE. `/demo-lab` route added (redirects to benchmark-lab). Forbidden
    names audit (P2X/Ren-Gas/SharpCell) confirmed only in seed/test data.
 4. DONE. `api_demo.html` + `ui_only.html` StrictUndefined `.get()` fixes.

**Deferred in this phase:**

- Full wizard retirement — `wizard.html` (735 lines) still serves steps
  1 (LV common data), 2 (upload), 5 (package preview), 6 (receipt).
  Moving step 1 to a `/applicant/common-data` page, step 2 to
  `/applicant/packages/{id}/upload`, and steps 5/6 into workbench
  finalize buttons would let us delete the wizard entirely.
- Copilot sidebar — currently link to `/chat`; spec calls for inline
  sidebar in workbench.
- AI jobs tracker widget and help panel in side rail.

### Phase 4 — Authority side

**Status: DONE**

 1. DONE. `authority_detail.html` StrictUndefined `.get()` fixes.
 2. DONE. Authority page already has all 5 v5 blueprint cards (AI
    summary, document inventory, observations, additional-info draft,
    decision memo draft). Data source aligned to typed
    `PackageFinding`/readiness from Phase 1.

### Phase 5 — Cleanup pass

**Status: DONE**

 1. Dead template audit: all 24 templates in use (20 directly referenced
    from routes, 3 base templates via `{% extends %}`, 1
    (`authority_detail.html`) served from authority route). Nothing to
    delete.
 2. `/ponytail-review` ran — one finding: stray whitespace in
    `demo_lab.py:29`.
 3. `AGENTS.md` architecture section updated to reflect `services/`
    and `routes/` package layout.

## 4. Explicit non-goals (YAGNI)

- No new framework/ORM/state-management library — FastAPI + SQLite +
  Jinja stays; the mess is organization, not tooling.
- No auth/production hardening beyond what's already stubbed
  (`production_hardening.html`) — out of scope per `AGENTS.md`.
- Not rewriting the mock LLM or Azure client — they're not where the
  usability complaint lives.

## 5. Status summary (2024)

All phases done to pragmatic stopping points:

- **Phase 1**: Typed contracts extended, `StrictUndefined` everywhere, all
  crashes surfaced and fixed.
- **Phase 2**: `services/` split into 4 modules, `routes/` split into 5
  sub-routers. No broken imports, 17 smoke tests pass.
- **Phase 3**: Landing page, wizard redirects, demo-lab route, template
  fixes done. Full wizard retirement + copilot sidebar deferred.
- **Phase 4**: Authority page aligned, StrictUndefined fixes done.
- **Phase 5**: Dead template audit (nothing to delete), plan finalization.

**Open bug**: Analysis page doesn't auto-advance from progress bar to
results on page reload (analysis completes, data saved correctly).
Frontend polling/timing issue — not blocking for demo use (manual
refresh works).
