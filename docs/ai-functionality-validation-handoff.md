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
forbidden corpus scopes. Execution still uses one active demo case, so response
annotation is tenant-aware but persisted artifacts and jobs are not isolated by
tenant.

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

## Verified correctness gaps

These are observed implementation facts. Confirm each with a focused regression
test before changing behavior.

3. Job streaming emits one current server-sent event rather than maintaining a
   live stream.
4. Cancellation prevents a late completion from overwriting the cancelled
   state, but it does not interrupt active provider or background work.
6. Generic timer jobs can reach completed state through elapsed-time simulation
   without executing feature logic.
7. API job results now use the canonical `api_job_result:<job_id>` reference.
8. Direct wizard intake auto-finalization does not carry provider settings into
   the finalizer, so Azure validators can be skipped on that path.
9. Contextual chat calls `client.responses` directly and bypasses the structured
   AI logging, validation, and fallback path.
10. Chat-proposed field changes are deterministic and can be semantically
    disconnected from the generated answer.
11. Agent output is keyed by agent name and overwritten. Historical audit rows
    can therefore inherit fallback or retrieval metadata from a later run.
12. Some generated evidence is synthetic or filename-level and can appear more
    specific than the source support warrants.
13. UI-only extraction inserts a candidate when extraction returns nothing,
    potentially presenting arbitrary input as an extracted field.
14. Deterministic issue checks use static criteria while prompts and reported
    versions can use effective edited criteria.
15. Applicant upload logic infers Azure enablement from `llm_provider != mock`,
    which can disagree with factory selection under forced mock, demo fallback,
    or a missing key.
16. Mock vector IDs are random even when model output is deterministic. Avoid
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

## Recommended implementation order

### Phase 1: Lock down contracts with tests

Add focused tests before behavior changes:

1. Provider selection matrix for `core` and `chat`
2. Azure payload, URL, API-version, header, timeout, error, empty-body, and
   output-text parsing tests using `httpx.MockTransport` or an injected transport
3. Malformed JSON, missing output, provider timeout, and log-recording tests for
   `call_llm_json()`
4. Retrieval priority, deduplication, maximum count, and forbidden-scope tests
5. Job transition, result lookup, cancellation, retry, timeout, and idempotency
   tests
6. Tenant A cannot read or mutate Tenant B jobs, results, logs, or artifacts
7. Applicant chat context excludes benchmark and authority-example material
8. Authority attachment-scope policy tests

Do not require live Azure credentials for the normal suite. Test Azure protocol
behavior through a fake transport. Keep an optional, explicitly credential-gated
smoke test for the deployed Azure endpoint.

Exit criteria:

* Both `core` and `chat` provider-selection branches are covered
* Azure fake-transport tests cover success, timeout, HTTP error, missing output,
   and malformed JSON
* Captured provider payloads prove retrieval priority and forbidden-scope
   exclusion
* Job tests expose the current result, cancellation, retry, and tenant ownership
   defects before Phase 2 changes them

### Phase 2: Repair execution semantics

Choose one honest job model and apply it consistently:

* Truly synchronous endpoints should return the result directly with a completed
  status and must not advertise cancellation or live streaming
* Truly asynchronous endpoints should queue work, return before execution,
  persist a stable operation descriptor, support real result lookup, and make
  cancellation cooperative

Recommended minimum repairs:

1. Persist tenant, case, operation name, normalized input, and result reference
   on every API job
2. Scope every lookup, result, cancel, and retry operation to the authenticated
   tenant
3. Make retry dispatch the original operation with the stored normalized input
4. Standardize one result artifact convention
5. Prevent late workers from completing a cancelled job
6. Stop elapsed-time simulation from implying provider progress
7. Pass settings explicitly through every provider-capable finalizer

Exit criteria:

* Every public job operation has one tenant owner and one stable result reference
* Cancellation prevents a late worker from publishing success
* Retry executes the stored operation rather than creating timer-only progress
* HTTP status, stored status, stream status, and result availability agree

### Phase 3: Normalize provider behavior

1. Route chat through a logged service helper with explicit text-output
   validation and fallback behavior
2. Separate `provider_used`, `fallback_used`, and `execution_mode` in persisted
   metadata
3. Store immutable run-specific agent output instead of overwriting by agent
   name
4. Redact or minimize logged prompt and response content
5. Distinguish provider evidence, deterministic evidence, filename hints, and
   unsupported fallback in the schema
6. Align edited criteria with deterministic gate logic or explicitly report
   which criteria source each check used

Exit criteria:

* Every AI-labeled result records `execution_mode`, `provider_used`, and
   `fallback_used` truthfully
* Chat uses the same observable logging and failure contract as structured calls
* Agent output metadata is immutable per run
* Applicant-facing output distinguishes sourced evidence from filename hints and
   unsupported fallback

### Phase 4: Validate feature correctness

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

### Phase 5: Optional live Azure smoke test

Run only after fake-transport tests pass and only with non-sensitive fixtures.
Verify:

* Core and chat use their intended host, model, key, and timeout
* Responses API JSON mode is accepted by the configured deployment
* File upload and vector-store ingestion complete within the expected timeout
* File search uses only the approved stores
* Logs do not expose credentials or prohibited source content
* User-visible fallback and error states remain truthful

Record the deployment, model, API version, timestamp, and fixture ID. Do not
record API keys or raw confidential prompts.

Exit criteria:

* Fake-transport and full mock suites pass before credentials are used
* One synthetic fixture completes through the configured Azure deployment
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

## First continuation task

Begin with provider and job contract tests. They are narrow, do not require UI
changes, and can falsify the highest-risk assumptions before implementation.
After each repair, run the focused test and then the full established suite.

Do not start by tuning prompts. Prompt quality cannot compensate for shared
tenant state, inaccessible results, misleading job semantics, missing failure
handling, or retrieval-scope defects.