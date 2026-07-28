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

 1. Make `/applicant/packages/{package_id}` the only screen an applicant
    sees after upload. Move wizard step 3 (suggestions) and step 4 (checks)
    content into the workbench's existing `extraction-section` /
    `task-section` (already 90% there) instead of linking out to
    `/wizard/2` / `/wizard/4`.
 2. Wizard keeps only steps 1 (LV common data) and 5/6 (package preview,
    receipt) — or retire it fully in favor of
    `/cases/{case_id}/lupademo/applicant/...` routes if step 1 data entry
    moves into the workbench header too (decide after Phase 3.10 shows how
    much wizard code is actually still load-bearing).
 3. Collapse landing page CTAs to the four the spec names:
    "Tarkista valmis hakemuspaketti", "Laadi hakemus AI:n kanssa", "Jatka
    LV:ssä", "Näytä AI-ehdotuspalvelu" (`page_blueprints.yaml`
    `front_door`). Everything else (presenter, benchmark lab, production
    hardening, raw ui_only) moves under one `/demo-lab` nav item.
 4. Verification: AT01 (front door CTA above fold), AT11 (no
    benchmark-name leakage outside `/demo-lab`) as scripted checks.

### Phase 4 — Authority side

 1. `authority_detail.html` already has AI summary/draft — align its data
    source to the same typed `Finding`/readiness objects from Phase 1 so
    applicant and authority views show literally the same evidence records,
    not two independently-shaped summaries.

### Phase 5 — Cleanup pass

 1. Delete now-dead templates/routes once their content is folded into the
    workbench (check with `readSeek_refs`/route usage before deleting).
 2. Run `/ponytail-review` on the diffs from every phase above (required by
    project rules) and this repo's `ruff`/lens diagnostics before declaring
    each phase done.
 3. Update `AGENTS.md`'s architecture section to reflect the new
    `services/`/`routes/` package layout once split.

## 4. Explicit non-goals (YAGNI)

- No new framework/ORM/state-management library — FastAPI + SQLite +
  Jinja stays; the mess is organization, not tooling.
- No auth/production hardening beyond what's already stubbed
  (`production_hardening.html`) — out of scope per `AGENTS.md`.
- Not rewriting the mock LLM or Azure client — they're not where the
  usability complaint lives.

## 5. Suggested order of execution

Phase 1 (typed contracts) first — it's the direct fix for "AI results not
usable" and is a prerequisite for Phase 3 (can't consolidate UI on data
you can't trust the shape of). Phase 2 (file split) can happen in parallel
by a second pass since it's purely mechanical. Phase 3 (UI consolidation)
last, once data is typed and code is in the right files.
