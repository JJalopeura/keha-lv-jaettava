---
title: AI Functionality Validation Handoff
description: Source map, correctness risks, and validation order for Lupa-apuri AI functionality
ms.date: 2026-07-29
ms.topic: reference
---

## Purpose

Use this document to continue AI implementation and validation without repeating
the repository discovery. The current user interface redesign is complete and is
the regression baseline. Preserve routes, forms, redirects, persistence, API
shapes, and Applicant-facing disclosures unless a verified AI defect requires a
change.

The existing test suite passes in forced mock mode. That proves selected demo
contracts, but it does not prove Azure request correctness, provider failure
handling, tenant isolation, cancellation, retry, or retrieval boundaries.

## Product truth boundaries

Keep these distinctions explicit in code, tests, logs, and UI copy:

* Azure-backed output is provider-generated
* Mock output is deterministic demo output
* Deterministic checks and question plans are not model calls
* Simulated progress is not observed provider progress
* A benchmark snapshot refresh is not an evaluation run
* Applicant readiness is not authority approval
* Authority drafts are editable support, not decisions
* Mock voice is not a live voice service
* AI-only context is not automatically an official submission attachment
* A fallback without source support must not claim sourced evidence

## Runtime baseline

* Python environment: `.venv`
* Test command: `.venv/bin/python -m unittest discover tests`
* Current suite: 56 tests after provider, retrieval, job, provenance, and
   semantic regressions were added
* Tests set `KEHA_LV_FORCE_MOCK_LLM=true`
* Default provider: `mock`
* Azure provider configuration is split between `core` and `chat`
* Default Azure API version: `2025-04-01-preview`
* Main configuration owner: `app/core/config.py`
* Provider selection owner: `app/core/llm_factory.py`
* Structured call and AI log owner: `app/core/llm_utils.py`

## Execution model

All structured provider calls should follow this path:

```text
route or service
  -> run_json_agent
  -> call_llm_json
  -> create_llm_client
  -> MockLLMClient or AzureOpenAIClient
  -> schema normalization and validation
  -> artifact, AI log, and event persistence
```

The actual application also contains deterministic and hybrid paths. Do not
assume an `ai` route, agent label, or AI job proves that a provider was called.

### Provider selection

`create_llm_client()` returns the mock client when any of these is true:

* The configured provider is `mock`
* `KEHA_LV_FORCE_MOCK_LLM` is enabled
* Demo mode is enabled and the key for the requested purpose is absent

Otherwise, only `azure` is supported. Azure without the appropriate key raises
a runtime error. Test the demo-mode fallback explicitly because it occurs before
the unsupported-provider and missing-key errors.

### Structured calls

`call_llm_json()`:

* Requests a JSON object from the Responses API
* Applies global fast-mode and per-agent overrides
* Rate-limits calls with a process-wide lock and sleep
* Persists request, response, duration, model, case, task, agent, and error data
* Raises when output text is absent or malformed JSON
* Does not repair malformed JSON
* Swallows failures from AI log persistence

Review log retention and redaction before using real personal or confidential
application data. Prompts and complete responses are currently eligible for
persistence.

### Retrieval

`vector_store_ids()` composes case, offerings, and company stores, deduplicates
them, and currently returns at most two IDs. Confirm that truncation preserves
the intended source priority for every feature.

Applicant retrieval must exclude `example_case_benchmark` and
`authority_example` content. Intake code enforces privileged benchmark access,
and the external API rejects those requested scopes. Add tests that prove the
restriction at the final provider payload, not only at request validation.

## Feature inventory

### Applicant and intake

| Feature | Main owners | Execution type | Main persistence | Existing validation |
|---|---|---|---|---|
| Attachment classification and indexing | `routes/applicant.py`, `services/intake.py` | Local extraction plus heuristic or provider classification | Attachments, material intake, vector metadata, agent output, jobs | Classification, extraction, upload security, and job-start smoke tests |
| Environmental structure extraction | `services/intake.py::structure_environmental_application_from_attachments` | Deterministic in mock mode, Azure enrichment when real LLM is enabled | Structure result and extraction review queue | Review UI only; extracted values and provenance need tests |
| Intake and package analysis | `services/application.py::execute_intake_analysis_job` | Deterministic baseline plus optional Azure validators | Intake analysis, package analysis, extraction queue, jobs | Handoff and visibility smoke tests only |
| Single field suggestion | `services/application.py::create_field_suggestion` | Provider or mock with deterministic fallback | Suggestions, agent output, nested job | Prompt, schema, evidence, and honest fallback tests |
| Whole-form suggestions | `services/application.py::execute_form_suggestions_job` | Repeats field suggestion flow | Outer job plus per-field jobs and suggestions | Outer job start only |
| Suggestion acceptance and rejection | `services/application.py` | Deterministic state transition | Application and suggestions | UI coverage; state and provenance need tests |
| Contextual chat | `services/application.py::contextual_chat_answer` | Direct chat-provider or mock call; proposed edits are deterministic | Contextual chat and optional suggestion | Rendering only |
| Voice assistant | `services/application.py::voice_mock_turn` | Explicit mock only | Voice chat and event | No focused behavior test |
| Application check and submit gate | `services/application.py::run_application_check` | Deterministic checks plus optional provider agents | Check report, criteria assessment, precheck report, gate, jobs | Happy paths and API smoke coverage |

### Authority

| Feature | Main owner | Execution type | Existing validation |
|---|---|---|---|
| Caseworker summary | `services/application.py::authority_summary` | Provider or mock with deterministic fallback | Markdown fallback and page rendering |
| Additional-information draft | `services/application.py::additional_info_draft` | Provider or mock with deterministic fallback | No focused test |
| Decision-support memo | `services/application.py::authority_memo` | Provider or mock with deterministic fallback | Page rendering only |
| Observation disposition | `services/application.py` | Human-controlled deterministic transition | UI ordering only |

The memo deliberately limits attachment context to official submissions. The
summary currently receives all attachments. Decide and document the intended
authority policy before changing either path, then test the selected policy.

### External API

Main route owner: `app/domains/lupademo/routes/api_v1.py`.

* `POST /api/v1/field-suggestions`
* `POST /api/v1/applications:precheck`
* `POST /api/v1/application-packages:analyze`
* `POST /api/v1/question-plans`
* `POST /api/v1/authority:summarize`
* `POST /api/v1/suggestions/field`
* `POST /api/v1/suggestions/form`
* `POST /api/v1/checks/application`
* `POST /api/v1/structure/extract`
* `GET /api/v1/criteria/{criteria_set_id}`
* Job status, event, stream, result, cancellation, and retry endpoints under
  `/api/ai/jobs` and `/api/v1/jobs`

Authentication validates API keys, tenant IDs, permit types, criteria sets, and
forbidden corpus scopes. Each API tenant uses a stable, separately seeded case.
Jobs persist their tenant owner, and authenticated status, result, cancellation,
and retry operations enforce that ownership.

### Demo and evaluation tools

* The API playground runs field suggestion or structure extraction and stores a
  demo result
* The UI-only extractor can create and accept extracted candidates
* `benchmark_lab_snapshot()` builds deterministic metadata; it does not run a
  model evaluation
* `metrics_snapshot()` aggregates existing artifacts, events, and logs
* The full agent-chain audit mutates demo applications and invokes multiple AI
  paths; run it only in disposable test data
* Criteria testing writes a criteria test result but does not prove production
  legal correctness

## Remaining limitations

These are observed implementation facts that remain after the hardening work.
Confirm each with a focused regression test before changing behavior.

1. Job streaming emits one current server-sent event rather than maintaining a
   live stream.
2. Cancellation prevents a late completion from overwriting the cancelled
   state, but it does not interrupt active provider or background work.
3. Generic timer jobs can reach completed state through elapsed-time simulation
   without executing feature logic.
4. Direct wizard intake auto-finalization does not carry provider settings into
   the finalizer, so Azure validators can be skipped on that path.
5. Contextual chat calls `client.responses` directly and bypasses the structured
   AI logging, validation, and fallback path.
6. Chat-proposed field changes are deterministic and can be semantically
    disconnected from the generated answer.
7. Agent output is keyed by agent name and overwritten. Historical audit rows
    can therefore inherit fallback or retrieval metadata from a later run.
8. Some generated evidence is synthetic or filename-level and can appear more
    specific than the source support warrants.
9. UI-only extraction inserts a candidate when extraction returns nothing,
    potentially presenting arbitrary input as an extracted field.
10. Deterministic issue checks use static criteria while prompts and reported
    versions can use effective edited criteria.
11. Applicant upload logic infers Azure enablement from `llm_provider != mock`,
    which can disagree with factory selection under forced mock, demo fallback,
    or a missing key.
12. Mock vector IDs are random even when model output is deterministic. Avoid
    asserting stable IDs unless the contract is intentionally changed.

## Validation evidence from 2026-07-29

The current focused baseline passes 19 tests covering the agent pipeline, public
API response contracts, and request-level tenant validation. The full suite must
still be run after each implementation phase.

The local database was inspected through aggregate-only queries. No stored
prompt, response, filename, or application content was read. It contains 998 AI
log rows across seven agents, including 107 rows with an error. Attachment
classification accounts for 87 errors, and all eight recorded completeness
validator calls contain an error. Seven benchmark snapshots exist, but no saved
full agent-chain audit runs exist. These counts prioritize failure-path tests;
they do not measure semantic quality.

Prompt resolution for `caseworker_summary_agent`,
`additional_info_request_agent`, and `authority_memo_draft_agent` was repaired.
Each ID now selects its authored prompt file, and a table-driven regression test
guards the mapping.

Provider contracts now cover core and chat selection, Azure request construction,
HTTP failure parsing, malformed or missing structured output, timeout handling,
and persisted call errors. Applicant retrieval is limited to two deduplicated
stores in case-first order. Tests also prove that benchmark and authority scopes
are rejected and that the final provider call receives only the bounded IDs.

API package analysis now reports synchronous completion with HTTP 200. Each API
tenant receives a stable, separately seeded case for jobs, artifacts,
assessments, and AI logs. Public job status, result, cancellation, and retry
operations enforce persisted tenant ownership whenever an API key is supplied.
Terminal cancellation is rejected, cancelled jobs cannot be resurrected by late
completion, and retry returns `job_retry_not_supported` instead of creating a
timer-only job that does not rerun the original operation.

Agent output artifacts now record provider, execution mode, selected vector
stores, fallback state, and normalized errors. Table-driven semantic checks cover
supported, evidenced suggestions and honest unsupported fallbacks for building,
environmental, and public-event permits.

The Demo Lab active case is now persisted explicitly. Creating tenant-specific
API cases no longer changes Demo Lab links through database row ordering. The
audit trail uses the shared Finnish date-time filter in its own Jinja environment,
and its focused route regression passes without exposing prompt or response
payloads.

## Completed implementation contracts

The 2026-07-29 hardening work established these contracts:

* Structured core and chat calls select the intended provider configuration
* Azure request construction and structured-output parsing have fake-transport
   success and failure coverage
* Retrieval is case-first, deduplicated, limited to two stores, and filtered by
   requester scope before the provider boundary
* API jobs and artifacts are isolated by persisted tenant ownership
* Synchronous package analysis returns HTTP 200 and does not claim queued work
* Job results use `api_job_result:<job_id>` consistently
* Only pending or running jobs can be cancelled, and late completion cannot
   resurrect a cancelled job
* Retry reports `job_retry_not_supported` because the original operation is not
   reconstructible
* Agent output records provider, execution mode, selected stores, fallback state,
   fallback reason, and normalized errors
* Suggestion semantics are covered for building, environmental, and public-event
   permit types
* Audit views expose lifecycle metadata without rendering stored request or
   response payloads

## Reproducible validation

Run the deterministic regression suite with forced mock mode:

```bash
PYTHONWARNINGS=ignore KEHA_LV_FORCE_MOCK_LLM=true \
   .venv/bin/python -m unittest discover tests
```

The current baseline is 56 passing tests. `tests/test_smoke.py` sets
`KEHA_LV_FORCE_MOCK_LLM=true` during module import, so unsetting or overriding the
variable outside the test process does not turn this suite into a live Azure
test. Keep the normal suite deterministic and use a separate credential-gated
probe for live integration validation.

### Live Azure validation

Live integration checks use the ignored repository `.env`, explicitly set
`KEHA_LV_LLM_PROVIDER=azure` and `KEHA_LV_FORCE_MOCK_LLM=false`, and point
`KEHA_LV_DB_PATH` and `KEHA_LV_UPLOADS_DIR` at a temporary directory. Remove the
temporary directory after the run. Never run these checks against the project
database or real applicant material.

The probe must exercise production boundaries instead of calling Azure directly:

1. Call `create_llm_client()` and `call_llm_json()` for both `core` and `chat`
2. Call `create_field_suggestion()` once for each supported permit type
3. Assert valid structured output and a nonempty suggestion
4. Assert persisted `provider == "azure"`
5. Assert persisted `fallback_used is False`
6. Report only sanitized status, duration, and contract assertions

The provenance assertions are mandatory. Plausible output alone is insufficient
because deterministic fallback can hide a failed provider call.

On 2026-07-29, the configured Azure endpoints passed these checks:

| Check | Result | Duration |
|---|---|---:|
| Core structured JSON | Passed | 1.650 s |
| Chat structured JSON | Passed | 1.445 s |
| Building permit field suggestion | Passed, no fallback | 5.246 s |
| Environmental permit field suggestion | Passed, no fallback | 3.886 s |
| Public-event permit field suggestion | Passed, no fallback | 6.554 s |

The application-level probe used temporary database and upload paths and removed
them afterward. No repository data files were modified. Credentials, endpoint
values, prompts, and raw responses were not printed or recorded in this report.

The configured core and chat timeouts are 60 seconds. The observed calls
completed in 1.445 to 6.554 seconds, with no timeout or context-exhaustion
evidence. Do not increase timeout or context limits preemptively. Revisit them
only when token usage, truncation, timeout counts, or latency percentiles show a
specific constraint.

## Recommended follow-up order

The provider, retrieval, tenant, lifecycle, provenance, semantic suggestion, and
representative live Azure contracts described above are complete. Continue with
the remaining limitations in this order.

### Phase 1: Normalize remaining provider behavior

1. Route chat through a logged service helper with explicit text-output
   validation and fallback behavior
2. Store immutable run-specific agent output instead of overwriting by agent
   name
3. Redact or minimize logged prompt and response content
4. Distinguish provider evidence, deterministic evidence, filename hints, and
   unsupported fallback in the schema
5. Align edited criteria with deterministic gate logic or explicitly report
   which criteria source each check used

Exit criteria:

* Chat uses the same observable logging and failure contract as structured calls
* Agent output metadata is immutable per run
* Applicant-facing output distinguishes sourced evidence from filename hints and
   unsupported fallback

### Phase 2: Expand feature correctness

For each feature, cover this matrix:

| Dimension | Cases |
|---|---|
| Input | Valid, empty, partial, oversized, unsupported type, adversarial text |
| Provider | Mock, Azure fake success, timeout, HTTP error, malformed JSON, missing output |
| Retrieval | No store, case store, company store, duplicate IDs, forbidden scope |
| Result | Valid, partial, unknown fields, invalid confidence, missing evidence |
| Persistence | Correct case and tenant, immutable run metadata, no duplicate acceptance |
| Job | Queued, running, completed, failed, cancelled, retry, stale worker |
| UI | Loading, empty, success, fallback, failure, stale result, mobile layout |
| Audit | Accurate actor/action/time/result without fabricated fields |

Use representative fixtures for all three permit types. Assertions should check
semantic invariants rather than exact provider prose.

Exit criteria:

* Each permit type has one non-sensitive fixture with suggestion, check,
  extraction, and authority-draft assertions
* Each feature verifies one provider failure and one unsupported-output path
* Accepted suggestions retain enough run and evidence metadata for audit
* UI checks cover success, fallback, failure, stale results, and mobile layout

### Phase 3: Expand live Azure coverage

The completed smoke test covers structured core and chat calls plus field
suggestions for all permit types. Future credential-gated checks can verify:

* Core and chat use their intended host, model, key, and timeout
* File upload and vector-store ingestion complete within the expected timeout
* File search uses only the approved stores
* Logs do not expose credentials or prohibited source content
* User-visible fallback and error states remain truthful

Record the deployment, model, API version, timestamp, and fixture ID. Do not
record API keys or raw confidential prompts.

Exit criteria:

* Fake-transport and full mock suites pass before credentials are used
* One synthetic attachment fixture completes through the configured Azure
   deployment
* Sanitized evidence records the deployment, model, API version, timestamp,
  fixture ID, retrieval stores, and terminal status

## Feature acceptance criteria

An AI feature is complete only when all applicable statements are true:

* The trigger reaches the intended route and service
* Authentication and tenant/case scope are enforced before reading state
* Input defaults preserve intentional empty values
* Execution mode is accurately recorded as Azure, mock, deterministic, or hybrid
* Retrieval receives only allowed and intended stores
* Provider output is parsed and validated against the feature schema
* Unsupported output degrades to an explicit fallback or visible failure
* Persistence is scoped, idempotent, and traceable to one run
* Audit and AI logs describe observed behavior without invented evidence
* Result lookup returns the artifact advertised by job status
* Cancellation and retry do what their labels claim
* Applicant UI hides unnecessary infrastructure detail but discloses fallback
  and uncertainty when relevant
* Authority drafts remain visibly editable and non-decisional
* Automated tests cover success and at least one failure path

## Source map

Start future work at these ownership boundaries:

* `app/core/config.py`: environment and model configuration
* `app/core/llm_factory.py`: provider selection
* `app/core/azure_client.py`: Azure HTTP, files, vector stores, and Responses API
* `app/core/mock_llm.py`: deterministic mock contracts
* `app/core/llm_utils.py`: structured calls, overrides, rate limiting, and AI logs
* `app/domains/lupademo/security.py`: API key and request-scope validation
* `app/domains/lupademo/services/jobs.py`: job persistence and transitions
* `app/domains/lupademo/services/intake.py`: upload, extraction, retrieval, and
  JSON agents
* `app/domains/lupademo/services/application.py`: suggestions, checks, chat,
  voice, authority drafts, metrics, and external service logic
* `app/domains/lupademo/routes/api_v1.py`: public AI and job contracts
* `app/domains/lupademo/routes/applicant.py`: Applicant triggers
* `app/domains/lupademo/routes/authority.py`: authority triggers
* `app/domains/lupademo/routes/demo_lab.py`: benchmark and audit tools
* `app/domains/lupademo/specs/prompts/`: versioned prompt text
* `tests/test_smoke.py`: current mock-mode regression suite

## Next continuation task

Begin with the direct wizard intake finalizer. Add a focused regression proving
that provider settings reach the finalizer, then make the smallest correction
needed. Run that test and the full established suite after the repair.

Do not start by tuning prompts. Prompt quality cannot compensate for shared
logging gaps, mutable run metadata, synthetic evidence, or criteria-source
mismatches.
