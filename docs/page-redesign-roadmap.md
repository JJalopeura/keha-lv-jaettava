---
title: Lupa-apuri Page Redesign Roadmap
description: Implementation plan for redesigning Lupa-apuri pages within the approved product architecture and design system
ms.date: 2026-07-29
ms.topic: concept
---

## Delivery constraints

The [product architecture](product-architecture.md) and
[master design system](../design-system/lupa-apuri/MASTER.md) are approved and
authoritative. This roadmap applies their decisions. It does not introduce roles,
workspaces, navigation models, or visual systems.

The implementation keeps these boundaries:

* Applicant and LV work remains in the Applicant/LV workspace
* Caseworker work remains in the Caseworker workspace
* Administration, integrations, evaluation, operations, and demo capabilities remain
  in the Demo Lab/Platform workspace
* Presenter remains a guided mode across prepared product states
* Existing routes, form actions, field names, and Jinja variables remain stable until
  a backlog item explicitly calls for a compatible migration
* Information architecture, navigation, flow, and component consistency precede
  visual refinement

Every redesign follows the same page anatomy:

1. Overview establishes context, status, blockers, and the recommended next action.
2. Work area supports the page's one primary job.
3. Details preserve evidence, history, and technical information without competing
   with the current decision.

## Applicant and LV workspace

### `landing.html`

| Review area | Redesign decision |
|---|---|
| Primary user job | Choose whether to continue existing work, start a guided application, or begin from prepared material. |
| Current problems | The marketing-style AI promise outranks current work. Three numbered paths blur starting and continuing. `Jatka LV:ssä` appears without confirmed project context. Presenter proof and AI-process explanation compete with the decision. |
| New page structure | Overview: resumable project or application status and one recommended continuation. Work area: two start choices, guided application and prepared material. Details: concise AI assistance and human-control explanation. |
| Components used | `AppShell`, `PageHeader`, `StatusSummary`, `RecommendedNextAction`. |
| Components removed | Marketing hero treatment, numbered path decoration, and Applicant-visible presenter proof. |
| Information moved elsewhere | The claim `Mitä tämä todistaa` moves to Presenter. Detailed material handling belongs in Materials. |
| Information collapsed behind disclosure | Secondary description of how AI reads and structures material. The statement that the applicant approves changes remains visible. |
| Recommended Finnish labels | `Hakijan etusivu`, `Jatka hakemusta`, `Aloita uusi hakemus`, `Tuo valmis aineisto`, `Näin AI auttaa`. |
| Navigation improvements | Keep `/` as Applicant Home. Show project continuation only when a relevant project exists. Preserve existing start destinations. |
| Implementation recommendation | Recompose existing links and context before adding data. Remove presenter markup. Use current workspace options or minimal existing-project state to render the continuation action conditionally. |

### `applicant_start.html`

| Review area | Redesign decision |
|---|---|
| Primary user job | Create an application context with the minimum project identity and permit choice. |
| Current problems | The page repeats Home and LV shared-data capture. Its numbered form sections create a second workflow before the application stepper. The action outcome is unclear, and demo defaults can look like user input. |
| New page structure | Overview: what will be created and what can be completed later. Work area: project identity followed by permit choice. Details: permit-choice guidance. |
| Components used | `PageHeader`, `FormSection`, standard form actions. |
| Components removed | Local numbered workflow and repeated general AI guidance. |
| Information moved elsewhere | Full shared applicant and location maintenance moves to Project. Application-specific fields remain in Application Details. |
| Information collapsed behind disclosure | Permit-choice help and examples. |
| Recommended Finnish labels | `Uusi hakemus`, `Hankkeen perustiedot`, `Valitse lupatyyppi`, `Luo hakemus`, `Palaa etusivulle`. |
| Navigation improvements | Return to Applicant Home. Successful creation continues to the approved `Perustiedot` step. |
| Implementation recommendation | Keep `/projects/new`, current form names, and `start_at=wizard`. Remove presentation-only defaults unless the user selected a scenario that supplies them. |

### `lv_project.html`

| Review area | Redesign decision |
|---|---|
| Primary user job | Understand permit-portfolio status and take the next project-level action. |
| Current problems | Shared-data editing appears before portfolio status. Permit actions use ambiguous labels. Synchronization exposes direction and field identifiers. Integration events and presenter proof leak into Applicant work. |
| New page structure | Overview: project status, blockers, and recommended action. Work area: permit portfolio, active synchronization proposals, then shared-data editing. Details: completed changes and project history. |
| Components used | `ProjectContextBar`, `StatusSummary`, `RecommendedNextAction`, `FormSection`, compact status list. |
| Components removed | Integration timeline, raw synchronization codes, `Avaa vertailuksi`, and presenter proof. |
| Information moved elsewhere | Integration event IDs and payloads move to Platform Audit. Application-specific editing stays in the application workflow. |
| Information collapsed behind disclosure | Completed synchronization proposals, secondary shared fields, and project history. Active conflicts remain visible. |
| Recommended Finnish labels | `Projektin tilanne`, `Lupahakemukset`, `Jatka hakemusta`, `Aloita hakemus`, `Yhteisten tietojen muutokset`, `Ratkaise ristiriita`, `Projektin tiedot`. |
| Navigation improvements | Use one canonical project URL in navigation while preserving route aliases. Each permit links to its existing application route. |
| Implementation recommendation | Keep `project`, `common`, `sync`, and current POST actions. Translate change direction into decision language and order permits by required attention. |

### `package_new.html`

| Review area | Redesign decision |
|---|---|
| Primary user job | Add source material to a new or existing application. |
| Current problems | A local three-step progress indicator conflicts with the approved workflow. Project creation is mixed into Materials. File and ZIP controls imply separate jobs. Demo defaults and JavaScript-only readiness increase error risk. |
| New page structure | Overview: application context, material readiness, accepted content, and next action. Work area: one upload panel for files or ZIP. Details: text material, privacy, and delivery-purpose guidance. |
| Components used | `PageHeader`, `DocumentPanel`, `RecommendedNextAction`. |
| Components removed | Parallel progress indicator and repeated project-creation framing. |
| Information moved elsewhere | Permit and applicant creation move to the explicit start flow when application context exists. |
| Information collapsed behind disclosure | Text input, accepted formats, privacy, and delivery-purpose explanation. |
| Recommended Finnish labels | `Lisää aineisto`, `Valitse tiedostot tai ZIP-paketti`, `Lisää kuvaus`, `Tarkista aineisto`, `Toimitetaanko viranomaiselle`. |
| Navigation improvements | Return to Applicant Home when starting without context; otherwise return to the current application's Materials view. Continue into Materials after upload. |
| Implementation recommendation | Preserve route and field names. Render selected filenames with DOM nodes and `textContent`, not `innerHTML`. Keep server validation authoritative and show the action blocker in text. |

### `package_workbench.html`

| Review area | Redesign decision |
|---|---|
| Primary user job | Verify that uploaded material is usable and correctly classified. |
| Current problems | The page accepts AI-found field values and duplicates AI Review. Provider, agent, SSE, ETA, and job terminology compete with material readiness. Findings lack direct document recovery actions. |
| New page structure | Overview: material status, most important correction, and next action. Work area: actionable findings and `DocumentList`. Details: analysis progress and evidence. |
| Components used | `StatusSummary`, `RecommendedNextAction`, `DocumentPanel`, `DocumentList`, conditional `TechnicalDetailsDisclosure`. |
| Components removed | Extraction acceptance forms, agent pills, provider naming, and the parallel suggestion workflow. |
| Information moved elsewhere | AI-found field decisions move to `AI-ehdotukset`. Job traces move to Platform AI Operations. |
| Information collapsed behind disclosure | Analysis timing, progress events, and source excerpts. Technical traces are absent in normal Applicant mode. |
| Recommended Finnish labels | `Aineistot`, `Aineistoa tarkistetaan`, `Aineisto valmis`, `Korjattavat aineistot`, `AI:n löytämiä tietoja odottaa tarkistusta`, `Tarkista AI-ehdotukset`. |
| Navigation improvements | Provide one direct action into AI Review with a pending count. Keep `Uusi aineistopaketti` secondary. |
| Implementation recommendation | Keep package analysis and polling behavior. Replace acceptance forms with a count and route link. Preserve package, attachment, finding, and active-job variables. |

### `wizard.html`

| Review area | Redesign decision |
|---|---|
| Primary user job | Complete exactly one application workflow step at a time. |
| Current problems | One template owns shared data, form editing, uploads, two suggestion systems, chat, voice, validation, submission, receipt, presenter proof, and technical logs. Current steps do not match the approved sequence. Technical terminology and unrestricted progression increase cognitive load and failure demand. |
| New page structure | Overview: project context, application status, approved stepper, blocker, and next action. Work area: one canonical activity for the current step. Details: contextual evidence, accepted history, and help. |
| Components used | `ProjectContextBar`, `Stepper`, `StatusSummary`, `RecommendedNextAction`, `FormSection`, `DocumentPanel`, `AISuggestionReviewCard`, `EvidenceDetails`, `ValidationIssueCard`. |
| Components removed | Receipt step, presenter proof, showcase timeline, Applicant-visible job traces, duplicate extraction tables, and navigation that bypasses unresolved required work. |
| Information moved elsewhere | Upload and document management move to Materials. Accept and reject actions move to AI Review. Technical jobs move to Platform AI Operations. Receipt moves to `receipt.html`. |
| Information collapsed behind disclosure | Chat, evidence excerpts, resolved issues, and accepted or rejected suggestion history. Technical logs are not rendered for Applicant users. |
| Recommended Finnish labels | `Perustiedot`, `Aineistot`, `Hakemus`, `AI-ehdotukset`, `Tarkistus`, `Lähetys`, `Lähetyksen este`, `Lähetysvalmius`, `Vahva`, `Tarkistettava`, `Epävarma`. |
| Navigation improvements | Mark the current step with `aria-current="step"`. Use Back and one primary forward action. Deep-link every issue to its field, document, or suggestion. Submission exits the stepper to Receipt. |
| Implementation recommendation | Preserve the route and POST endpoints initially. Split six step bodies into partials, realign their responsibilities, and stop rendering technical members. Redirect successful submission to the existing receipt route. |

### `package.html`

| Review area | Redesign decision |
|---|---|
| Primary user job | Verify exactly what will be submitted and send the application. |
| Current problems | The page duplicates the wizard submission step and exposes package IDs, source codes, confidence numbers, delivery scopes, stale state, and internal blocker language. Failure recovery points elsewhere. |
| New page structure | Overview: recipient, readiness, blocker, and recovery action. Work area: application summary, official attachments, confirmations, and submit. Details: excluded AI-only material and non-blocking observations. |
| Components used | `ProjectContextBar`, `StatusSummary`, `RecommendedNextAction`, read-only `DocumentList`, `ValidationIssueCard`. |
| Components removed | Separate submission concept, system-oriented package summary, and raw package diagnostics. |
| Information moved elsewhere | Canonical submission review moves to the `Lähetys` step. Technical package metadata moves to Platform Audit when needed. |
| Information collapsed behind disclosure | AI-only excluded material, field provenance, and non-blocking recommendations. |
| Recommended Finnish labels | `Tarkista ja lähetä`, `Vastaanottaja`, `Lähetettävät tiedot`, `Viranomaiselle toimitettavat liitteet`, `Aineistot, joita ei lähetetä`, `Lähetys estyy, koska`. |
| Navigation improvements | Make the application `Lähetys` step canonical. Preserve this URL as a compatible redirect or equivalent view during migration. |
| Implementation recommendation | Keep GET and submit POST initially. Map internal values to Finnish labels and provide one recovery link to `Tarkistus` when blocked. Retire duplicate markup after route compatibility is verified. |

### `receipt.html`

| Review area | Redesign decision |
|---|---|
| Primary user job | Confirm successful submission, retain the reference, and understand what happens next. |
| Current problems | Receipt content is duplicated in the wizard. The key reference lacks prominence. Timestamp formatting is technical, and the return action loses project context. |
| New page structure | Overview: completed outcome, receipt reference, and receiving authority. Work area: received time, sent material summary, and next processing state. Details: printable reference information. |
| Components used | Outcome `PageHeader`, completed `StatusSummary`, and `RecommendedNextAction` only when another applicant action exists. |
| Components removed | Editable stepper controls, duplicate wizard receipt, and normal Applicant links into authority work. |
| Information moved elsewhere | Remove receipt representation from the wizard. Authority follow-up remains in the Caseworker workspace. |
| Information collapsed behind disclosure | Optional technical reference values beyond the receipt identifier. |
| Recommended Finnish labels | `Hakemus on lähetetty`, `Vastaanottokuitti`, `Kuittitunnus`, `Vastaanotettu`, `Vastaanottava viranomainen`, `Siirry projektiin`. |
| Navigation improvements | Make the receipt route canonical after submission. Return directly to the current project. |
| Implementation recommendation | Keep route and receipt data. Format the stored time for Finnish presentation and update submission success to redirect here. |

## Caseworker workspace

### `authority_queue.html`

| Review area | Redesign decision |
|---|---|
| Primary user job | Choose the next submitted application by comparing urgency, readiness, deadline, and unresolved work. |
| Current problems | Counts do not recommend an action. Quality and risk lack interpretation. Permit IDs leak into labels. Filters have no reset or return-state persistence. Presenter proof and weak empty states distract from prioritization. |
| New page structure | Overview: queue status, urgent count, overdue work, and recommended next case. Work area: filter toolbar and full-width queue table. Details: short explanation of prioritization terms. |
| Components used | Caseworker `AppShell`, `PageHeader`, `StatusSummary`, `RecommendedNextAction`, operational table. |
| Components removed | Presenter proof, presenter-dependent emphasis, raw permit IDs, and unexplained quality scoring. |
| Information moved elsewhere | Presenter claims move to Presenter. Case evidence stays in case detail. Technical queue diagnostics move to Platform operations when required. |
| Information collapsed behind disclosure | Queue term and prioritization explanations. Urgency, status, deadline, issue count, and applicant identity remain visible. |
| Recommended Finnish labels | `Käsittelijän työjono`, `Käsittelyä odottavat hakemukset`, `Rajaa hakemuksia`, `Poista rajaukset`, `Käsittelyvaihe`, `Hakemuksen valmius`, `Täydennettävää`, `Määräaika`, `Avaa käsittelyyn`. |
| Navigation improvements | Keep `Työjono` active. Preserve filter query parameters in case links and return navigation. Add `Omat tapaukset` only when a real destination exists. |
| Implementation recommendation | First remove presenter markup, add a caption and reset action, map permit labels, explain overdue state, and improve empty-state recovery. Reconcile displayed `processing_stage` with persisted status before adding sorting or assignment. |

### `authority_detail.html`

| Review area | Redesign decision |
|---|---|
| Primary user job | Review one submitted application and decide whether to continue, request information, or prepare decision support. |
| Current problems | No clear status and next action pair exists. AI summary dominates before official evidence. Observations lack evidence excerpts. Raw fields, tools, drafts, and events compete in one long page. Status and draft controls imply persistence that is incomplete or unclear. |
| New page structure | Overview: authority case context, status, urgency, unresolved decision, recommended action, and compact advisory AI summary. Work area: observations and checklist beside official evidence. Details: application fields, drafts, resolved observations, and case history. |
| Components used | Authority-case `ProjectContextBar`, `PageHeader`, `StatusSummary`, `RecommendedNextAction`, read-only `DocumentPanel`, `DocumentList`, `EvidenceDetails`, `ValidationIssueCard`. |
| Components removed | Presenter proof, `Command center`, `Audit trail`, generic tool cards, and tab-like anchors that do not represent routed views. |
| Information moved elsewhere | Integration events move to Platform Audit. Presenter claims move to Presenter. Applicant-only AI context remains outside Caseworker. |
| Information collapsed behind disclosure | Evidence rationale, full application fields, resolved observations, draft history, and caseworker action history. Current status, official evidence, and active observations remain visible. |
| Recommended Finnish labels | `Hakemuksen käsittely`, `Tilanne`, `Suositeltu seuraava vaihe`, `AI-yhteenveto`, `Tarkistettavat asiat`, `Viranomaiselle toimitetut liitteet`, `Lähde ja perustelu`, `Pyydä täydennystä`, `Valmistele käsittelymuistio`, `Käsittelyhistoria`. |
| Navigation improvements | Add `Työjono / hakemus` breadcrumbs, retain filter state, and describe the context as an authority case rather than an open project. |
| Implementation recommendation | Reorder existing data before adding backend features. Remove presenter and event sections, mark the current status correctly, label generated drafts as unsaved previews, format times in Finnish, and expose evidence-unavailable states. Add draft persistence and confirmation only with matching backend contracts. |

## Demo Lab and Platform workspace

### `demo_lab.html`

| Review area | Redesign decision |
|---|---|
| Primary user job | See platform status, choose the capability needing attention, or prepare a demonstration. |
| Current problems | Eight equal cards act as a tool directory. Service health, active scenario, readiness, exceptions, and recommended action are absent. The title understates governance and operations. |
| New page structure | Overview: platform status, active scenario, readiness, and significant exceptions. Work area: one recommended action and grouped platform or demo capabilities. Details: optional technical proof and recent activity. |
| Components used | `PageHeader`, `StatusSummary`, `RecommendedNextAction`, `PresenterProofCard`. |
| Components removed | Equal-weight navigation cards and separate Admin or Integrator framing. |
| Information moved elsewhere | Detailed measures, ingestion, API testing, evaluation, logs, and controls remain in their canonical Platform capabilities. |
| Information collapsed behind disclosure | Technical proof and demo recovery information. Raw payloads stay off the overview. |
| Recommended Finnish labels | `Demo Lab ja alusta`, `Palvelun tila`, `Suositeltu seuraava toimi`, `Aktiivinen skenaario`, `Valmistele esitys`. |
| Navigation improvements | Keep this as `Yleiskuva`. Present approved capability groups rather than a feature-card directory. |
| Implementation recommendation | Keep `/demo-lab` and existing destinations. Recompose current links into capability groups and derive initial status from available snapshots without inventing new operational data. |

### `metrics.html`

| Review area | Redesign decision |
|---|---|
| Primary user job | Determine whether the service improves application quality and processing outcomes. |
| Current problems | Impact, AI operations, evaluation, audit, readiness, attachment diagnostics, presenter metadata, and executable agent auditing are mixed. Measures repeat, and simulated baselines look equivalent to observed results. |
| New page structure | Overview: outcome interpretation and four to six key measures. Work area: quality, first-time-right, processing impact, trends, and comparisons. Details: methodology, simulated assumptions, provenance, and export. |
| Components used | `PageHeader`, `StatusSummary`, `RecommendedNextAction`, `MetricsCard`, `AdminDataTable`, `TechnicalDetailsDisclosure`. |
| Components removed | Presenter section, agent execution, readiness table, benchmark table, event table, extraction inventory, and repeated quality summaries. |
| Information moved elsewhere | Agent activity moves to AI Operations, benchmarks to Evaluation, controls to Readiness, events to Audit, and demo proof to Presenter. |
| Information collapsed behind disclosure | Methodology, simulations, calculation rules, exports, and implementation caveats. |
| Recommended Finnish labels | `Vaikuttavuus`, `Hakemuksen laatu`, `Laatu ennen AI-avustusta`, `Nykyinen laatu`, `Korjatut puutteet`, `Arvioitu käsittelyajan säästö`, `Menetelmä ja oletukset`. |
| Navigation improvements | Use `Vaikuttavuus` as the destination label and link technical evidence to its canonical capabilities. |
| Implementation recommendation | Keep current routes. Remove or collapse misplaced sections first. Split the data snapshot only when a dedicated AI Operations view needs a distinct contract. |

### `criteria.html`

| Review area | Redesign decision |
|---|---|
| Primary user job | Govern, test, version, and restore permit criteria. |
| Current problems | Criteria editing is mixed with source ingestion, vector-store state, benchmark setup, raw IDs, internal severities, raw JSON, and large inline forms. Knowledge-source operations outrank criteria. |
| New page structure | Overview: active versions, publication status, and criteria requiring attention. Work area: criteria table, selected rules, and test action. Details: version history, restoration, and advanced editing. |
| Components used | `PageHeader`, `StatusSummary`, `RecommendedNextAction`, `AdminDataTable`, `FormSection`, `TechnicalDetailsDisclosure`. |
| Components removed | Source-ingestion dashboard, benchmark inventory, cross-capability shortcut buttons, and IDs as primary labels. |
| Information moved elsewhere | Source ingestion and vector-state diagnostics move to Knowledge Sources. Authority examples and agent targets move to Evaluation. |
| Information collapsed behind disclosure | Raw JSON, rule and field IDs, attachment codes, version identifiers, and test payloads. Restore and delete actions require confirmation. |
| Recommended Finnish labels | `Kriteerit`, `Aktiivinen versio`, `Julkaisutila`, `Lupatyyppi`, `Säännöt`, `Versiohistoria`, `Testaa esimerkillä`, `Lähetyksen este`, `Korjattava`, `Huomio`, `Suositus`. |
| Navigation improvements | Keep `Kriteerit` canonical. Expose Knowledge Sources as a sibling capability when a real route or stable section target exists. |
| Implementation recommendation | Preserve CRUD, restore, test, and ingestion actions. Render schema labels while retaining IDs in hidden inputs. Extract ingestion markup before introducing route changes. |

### `audit_trail.html`

| Review area | Redesign decision |
|---|---|
| Primary user job | Investigate who or what acted, when it happened, and what changed. |
| Current problems | Timestamps, action codes, and full payloads are exposed without interpretation. Filters, provenance categories, result count, event hierarchy, and recovery links are absent. |
| New page structure | Overview: selected scope, event count, and notable failures. Work area: filter toolbar and chronological table. Details: expandable payload and identifiers per event. |
| Components used | `PageHeader`, `StatusSummary`, `RecommendedNextAction`, `AdminDataTable`, `TechnicalDetailsDisclosure`. |
| Components removed | Admin-as-role eyebrow, always-visible raw data column, and event codes as primary descriptions. |
| Information moved elsewhere | Interpreted aggregate measures remain in Impact. Full AI run traces belong to AI Operations. |
| Information collapsed behind disclosure | Raw action, payload, artifact IDs, correlation values, and trace references. |
| Recommended Finnish labels | `Tapahtumaloki`, `Viimeisimmät tapahtumat`, `Aika`, `Toimija`, `Tapahtuma`, `Kohde`, `Tulos`, `Tekniset tapahtumatiedot`. |
| Navigation improvements | Make this the canonical `Tapahtumaloki`; remove duplicate event listings from Impact. |
| Implementation recommendation | Keep current route and event limit. Format times, map labels, add native row disclosures, and use query filtering when volume requires server-side support. |

### `benchmark_lab.html`

| Review area | Redesign decision |
|---|---|
| Primary user job | Determine whether AI behavior meets defined quality expectations. |
| Current problems | Corpus coverage is presented as evaluation results. Refresh recomputes a snapshot but sounds like an evaluation run. Runner path, scope, agent IDs, case IDs, and vector policy are prominent. Run time, threshold, regression, and pass or fail are absent. |
| New page structure | Overview: evaluation readiness, corpus coverage, and last available result. Work area: comparison targets and cases requiring review. Details: method, rubric, identifiers, and source policy. |
| Components used | `PageHeader`, `StatusSummary`, `RecommendedNextAction`, `MetricsCard`, `AdminDataTable`, `TechnicalDetailsDisclosure`. |
| Components removed | `Admin / eval`, raw runner path, raw scope, and agent IDs from the default view. |
| Information moved elsewhere | Missing source material links to Knowledge Sources. Operational model failures link to AI Operations. |
| Information collapsed behind disclosure | Rubric, case and agent IDs, runner path, excerpts, and vector-store policy. |
| Recommended Finnish labels | `Arviointi`, `Arvioinnin tila`, `Vertailuaineisto`, `Arviointikohteet`, `Tarkistettavat tapaukset`, `Päivitä arvioinnin yhteenveto`. |
| Navigation improvements | Use `Arviointi` as canonical. Use Knowledge Sources as the recovery destination for absent corpus material. |
| Implementation recommendation | Keep aliases and refresh route. Do not label refresh as a run until one executes. Persist timestamps, thresholds, results, and regressions only in the major evaluation redesign. |

### `production_hardening.html`

| Review area | Redesign decision |
|---|---|
| Primary user job | Identify and resolve open controls required for production readiness. |
| Current problems | Phase numbering and hardening terminology expose implementation planning. Rows lead with internal IDs and evidence but lack plain control names, grouping, and available remediation. Owner and action data do not exist. |
| New page structure | Overview: readiness ratio and highest-priority incomplete control. Work area: controls grouped by capability with status and supported remediation. Details: technical evidence. |
| Components used | `PageHeader`, `StatusSummary`, `RecommendedNextAction`, `AdminDataTable`, `TechnicalDetailsDisclosure`. |
| Components removed | Phase framing, raw IDs as titles, internal status codes, and the duplicate readiness table in Impact. |
| Information moved elsewhere | Readiness remains canonical here. Impact shows only a linked summary. Evaluation policy remains in Evaluation. |
| Information collapsed behind disclosure | Scripts, routes, configuration values, prompt hashes, exact limits, and internal control IDs. |
| Recommended Finnish labels | `Tuotantovalmius`, `Valmiuden tila`, `Valmis`, `Vaatii toimia`, `Hallintakeino`, `Näyttö`, `Avoin toimi`. |
| Navigation improvements | Make `Tuotantovalmius` canonical and link incomplete controls to their owning Platform capability only when a real destination exists. |
| Implementation recommendation | Keep current snapshot. Map known IDs and statuses to Finnish labels. Do not invent owners or actions until those fields are persisted. |

### `suggestion_service.html`

| Review area | Redesign decision |
|---|---|
| Primary user job | Choose and monitor a supported suggestion-service usage mode. |
| Current problems | The page frames the service as a separate product. Integration mode, clerk tool, credentials, tenant administration, and request history share one level. Demo keys are visible. |
| New page structure | Overview: service health, supported modes, and recent result. Work area: API contract testing and clerk-tool entry. Details: activity, tenant configuration, and credentials. |
| Components used | `PageHeader`, `StatusSummary`, `RecommendedNextAction`, `MetricsCard`, `AdminDataTable`, `TechnicalDetailsDisclosure`. |
| Components removed | Separate product identity, Integrator implication, raw credentials, and implementation-led `UI-only` framing. |
| Information moved elsewhere | Full request traces and failed AI runs move to AI Operations. Contract testing remains in Integrations. |
| Information collapsed behind disclosure | Demo keys, tenant and criteria IDs, raw request history, endpoints, and authentication setup. |
| Recommended Finnish labels | `AI-ehdotuspalvelu`, `Integraatiotapa`, `API-sopimuksen testaus`, `Virkailijan ehdotustyökalu`, `Viimeisimmät palvelupyynnöt`, `Tekniset käyttöoikeustiedot`. |
| Navigation improvements | Keep the page under `Integraatiot`; API and clerk tools remain subordinate pages. |
| Implementation recommendation | Keep current overview and child routes. Move keys into a collapsed disclosure immediately and retain existing case-scoped activity data. |

### `api_demo.html`

| Review area | Redesign decision |
|---|---|
| Primary user job | Test the external API contract and interpret the response. |
| Current problems | Request, summary, job state, events, raw JSON, suggestions, document inventory, speculative widget, and endpoint docs compete at once. The same result appears in multiple representations. Labels mix languages. |
| New page structure | Overview: selected client, last request status, and retry or correction action. Work area: request editor beside interpreted response. Details: events, raw request and response, document inventory, endpoints, and headers. |
| Components used | `PageHeader`, `StatusSummary`, `RecommendedNextAction`, `AdminDataTable`, `TechnicalDetailsDisclosure`, `EvidenceDetails`. |
| Components removed | Widget preview, iframe claim, duplicate prominent result panels, and nested decorative result cards. |
| Information moved elsewhere | Persistent run history and failures move to AI Operations. Credentials move to Integrations administration. |
| Information collapsed behind disclosure | Keys, raw JSON, job and event data, endpoints, headers, field IDs, document roles, scopes, and full error response. |
| Recommended Finnish labels | `API-sopimuksen testaus`, `Asiakas`, `Pyyntö`, `Lähetä pyyntö`, `Vastauksen yhteenveto`, `Ajotapahtumat`, `Lopullinen JSON-vastaus`, `Tekniset sopimustiedot`. |
| Navigation improvements | Show `Integraatiot / AI-ehdotuspalvelu / API-sopimuksen testaus` context and keep Integrations active. |
| Implementation recommendation | Preserve GET, POST, and redirect aliases. Recompose current result data before changing API contracts. Use interpreted labels as the primary response and raw JSON as evidence. |

### `ui_only.html`

| Review area | Redesign decision |
|---|---|
| Primary user job | Produce, review, and copy suggestions from supplied material without system integration. |
| Current problems | Tenant and criteria metadata lead the page. Suggestions expose field IDs and numeric confidence without evidence. Checkbox acceptance can imply application mutation. Edit and reject dispositions are absent. |
| New page structure | Overview: input context and explicit non-mutation statement. Work area: source input, suggestion review, and selected output. Details: evidence, selection history, tenant, and criteria metadata. |
| Components used | `PageHeader`, `StatusSummary`, `RecommendedNextAction`, `AISuggestionReviewCard`, `EvidenceDetails`, `TechnicalDetailsDisclosure`. |
| Components removed | `UI-only` as a user-facing name, IDs as headings, numeric confidence, delivery scope, and tenant suffix beside results. |
| Information moved elsewhere | Tenant and request diagnostics move to Integrations administration or AI Operations. Local selection history remains with the tool. |
| Information collapsed behind disclosure | Tenant ID, criteria version, ephemeral scope, character counts, field IDs, and extraction metadata. |
| Recommended Finnish labels | `Virkailijan ehdotustyökalu`, `Lähdeaineisto`, `Lupatyyppi`, `Muodosta ehdotukset`, `Tarkista ehdotukset`, `Kopioi valitut tiedot`, `Vahva`, `Tarkistettava`, `Epävarma`. |
| Navigation improvements | Keep this subordinate to Integrations and provide a direct return to the suggestion-service overview. |
| Implementation recommendation | Preserve generation, copy, and selection routes. First relabel acceptance and map schema labels. Add edit, reject, evidence, and disposition persistence only as a major follow-up. |

### `presenter.html`

| Review area | Redesign decision |
|---|---|
| Primary user job | Run a reliable demonstration from applicant need to outcome. |
| Current problems | The page uses the legacy LV shell, narrates backend stages, lacks scenario readiness, and sends multiple scenes to generic destinations. Its checklist mixes presenter preparation with architecture assertions. |
| New page structure | Overview: active scenario, readiness, duration, and start action. Work area: ordered proof scenes with expected state and valid deep link. Details: speaker notes, optional technical proof, and real recovery actions. |
| Components used | Canonical `AppShell`, `PageHeader`, `StatusSummary`, `RecommendedNextAction`, `PresenterProofCard`. |
| Components removed | Presenter workspace implication, legacy shell, generic agent-stage narration, architecture checklist, and links without prepared context. |
| Information moved elsewhere | Evaluation policy moves to Evaluation, API JSON to optional integration proof, and service readiness to Readiness. |
| Information collapsed behind disclosure | Speaker notes, API proof, seeded-data warnings, provider configuration, and recovery instructions. |
| Recommended Finnish labels | `Esittäjätila`, `Aktiivinen skenaario`, `Demon valmius`, `Aloita esitys`, `Kohtaus`, `Odotettu näyttö`, `Puhujan muistiinpanot`, `Tekninen lisänäyttö`. |
| Navigation improvements | Keep Presenter as a Platform capability and mode. Destination pages retain their own workspace identity. |
| Implementation recommendation | Keep `/demo-lab/presenter`, migrate to the canonical shell, and provide active case and scenario context before generating deep links. |

### `scenarios.html`

| Review area | Redesign decision |
|---|---|
| Primary user job | Select and start a prepared demonstration premise. |
| Current problems | The legacy shell and JSON implementation language dominate. Scenario cards expose internal persona and proof data without readiness, expected outcome, or active-scenario orientation. |
| New page structure | Overview: active scenario and recommended continuation. Work area: scenarios with audience need, starting state, expected proof, and start action. Details: prerequisites and reset consequences. |
| Components used | Canonical `AppShell`, `PageHeader`, `StatusSummary`, `RecommendedNextAction`, `PresenterProofCard`. |
| Components removed | JSON-spec wording, legacy shell, generic scenario cards, and any workspace implication. |
| Information moved elsewhere | Full demonstration sequence moves to Presenter. Applicant and Caseworker actions remain in their destination workspaces. |
| Information collapsed behind disclosure | Seed prerequisites, reset effects, scenario ID, and route detail. |
| Recommended Finnish labels | `Skenaariot`, `Valitse demon lähtötilanne`, `Aktiivinen skenaario`, `Lähtötilanne`, `Mitä skenaario osoittaa`, `Käynnistä skenaario`, `Avaa esittäjätila`. |
| Navigation improvements | Keep `Skenaariot` beside `Esittäjätila`. After selection, make Presenter the recommended next action. |
| Implementation recommendation | Preserve scenario GET and POST routes and hidden IDs. Migrate rendering to the Platform shell and keep the existing scenario-defined destination behavior. |

## Design debt to remove once

| Duplicate concept | Current locations | Canonical destination | Removal rule |
|---|---|---|---|
| Application start and project capture | `landing.html`, `applicant_start.html` | Home chooses; Start creates | Home never repeats the creation form |
| Material upload and management | `package_new.html`, `package_workbench.html`, `wizard.html` | Applicant Materials | Wizard links to Materials rather than embedding another workbench |
| AI suggestion decisions | `package_workbench.html`, `wizard.html` | Application AI Review | No other page accepts or rejects application suggestions |
| Submission review | `package.html`, `wizard.html` | Application `Lähetys` | Compatibility route renders or redirects to the canonical step |
| Receipt | `receipt.html`, `wizard.html` | `receipt.html` | Receipt exists outside the editable stepper |
| Shared project data | `applicant_start.html`, `lv_project.html`, `wizard.html` | Applicant Project | Start captures the minimum; Project maintains shared values |
| Presenter proof | Applicant and Caseworker templates | Platform Presenter | Operational pages contain no presenter-only claims |
| Platform readiness | `metrics.html`, `production_hardening.html` | Production Readiness | Impact shows only an interpreted linked summary |
| Benchmark and evaluation | `metrics.html`, `criteria.html`, `benchmark_lab.html` | Evaluation | Other pages link rather than render benchmark inventories |
| Events and traces | `metrics.html`, `audit_trail.html`, case pages | Audit and AI Operations | User workspaces show interpreted evidence, not integration events |
| Knowledge ingestion | `criteria.html` and metric diagnostics | Knowledge Sources | Criteria consumes approved sources but does not operate ingestion |
| API activity | `metrics.html`, `suggestion_service.html`, `api_demo.html` | Integrations and AI Operations | Contract testing and persistent operations have separate views |

## Complete redesign backlog

| ID | Priority | Scope | Deliverable | Dependency | Completion evidence |
|---|---:|---|---|---|---|
| RD-01 | P0 | Shared | Implement `PageHeader`, `StatusSummary`, and `RecommendedNextAction` partials | Approved shell | Two pages reuse each partial without copied markup |
| RD-02 | P0 | Shared | Add semantic status, action, panel, disclosure, table, and form tokens | None | Existing shell and new partials use semantic variables |
| RD-03 | P0 | Applicant | Remove suggestion acceptance from Materials and link to AI Review | Existing AI Review route | Only AI Review accepts or rejects application suggestions |
| RD-04 | P0 | Applicant | Align the stepper and wizard content to the approved six activities | RD-01 | Each step has one job, status, next action, and current-step semantics |
| RD-05 | P0 | Applicant | Make `receipt.html` the post-submit destination | RD-04 | Submission exits the stepper and opens one canonical receipt |
| RD-06 | P0 | Shared | Remove presenter proof and technical traces from operational pages | None | Applicant and Caseworker renders contain no presenter or trace sections |
| RD-07 | P1 | Applicant | Recompose Home and Start into selection and creation responsibilities | RD-01 | Home contains no duplicate creation form |
| RD-08 | P1 | Applicant | Rebuild Project around portfolio status and next action | RD-01 | Permit attention precedes shared-data editing |
| RD-09 | P1 | Applicant | Consolidate upload and material management | RD-03 | One canonical Materials surface owns documents |
| RD-10 | P1 | Applicant | Retire duplicate submission markup behind a compatible route | RD-04, RD-05 | One submission representation remains |
| RD-11 | P1 | Caseworker | Recompose queue around prioritization and filter recovery | RD-01 | Queue recommends one next case and preserves filters |
| RD-12 | P1 | Caseworker | Recompose case detail around decision, official evidence, and history | RD-01 | Status and next action precede AI summary and details |
| RD-13 | P1 | Platform | Turn Demo Lab into a status-led capability overview | RD-01 | Yleiskuva shows readiness, scenario, exceptions, and next action |
| RD-14 | P1 | Platform | Reduce Impact to outcome measures and methodology | RD-01 | No audit, benchmark, readiness, trace, or presenter sections remain |
| RD-15 | P1 | Platform | Separate criteria governance from source ingestion | RD-01 | Criteria starts with active versions and rules |
| RD-16 | P1 | Platform | Hide keys, payloads, IDs, and traces behind technical disclosures | RD-02 | Normal Platform views expose interpreted labels first |
| RD-17 | P1 | Platform | Migrate Presenter and Scenarios to the canonical shell | RD-01 | Neither template extends the legacy shell |
| RD-18 | P2 | Shared | Implement document list, evidence, validation, metrics, and admin-table partials | RD-01, RD-02 | Each abstraction has at least two real consumers |
| RD-19 | P2 | Platform | Add a canonical Knowledge Sources surface using existing ingestion actions | RD-15, RD-18 | Source readiness no longer lives on Criteria |
| RD-20 | P2 | Platform | Add an AI Operations view for runs, failures, and recovery | RD-14, RD-18 | Persistent traces leave Impact and integration pages |
| RD-21 | P2 | Platform | Add real evaluation run state, thresholds, and regressions | RD-18 | Evaluation distinguishes readiness from measured results |
| RD-22 | P2 | Caseworker | Add draft persistence, status confirmation, and observation notes | RD-12 | Controls describe and persist the same behavior |
| RD-23 | P2 | Platform | Add complete clerk-tool dispositions and evidence | RD-18 | Suggestions support edit, reject, evidence, and history |
| RD-24 | P2 | Shared | Retire compatibility and legacy shell markup after parity | RD-17 and template migration | No active template depends on `lv_base.html` |

## Refactoring priority order

1. Remove duplicate actions and misleading controls that can change user decisions.
2. Establish reusable status and next-action components.
3. Align the Applicant workflow and canonical outcomes.
4. Remove technical and presenter content from operational workspaces.
5. Recompose Caseworker pages around case decisions.
6. Consolidate Platform capabilities and technical disclosures.
7. Introduce new Platform routes only for activities with distinct jobs and real data.
8. Retire compatibility templates only after route and behavior parity.

This order reduces failure demand before improving visual polish. It prevents shared
components from standardizing duplicate or misplaced concepts.

## Component implementation order

| Order | Component | First consumers | Reason for position |
|---:|---|---|---|
| 1 | `PageHeader` | All workspace pages | Establishes job, context, and one primary action |
| 2 | `StatusSummary` | Wizard, Project, Queue, Demo Lab | Makes state and blockers comparable across workspaces |
| 3 | `RecommendedNextAction` | Same pages as status | Completes the status-to-action contract |
| 4 | `TechnicalDetailsDisclosure` | Criteria, API Demo, Audit | Removes technical information from normal scan paths |
| 5 | `Stepper` | Application workflow | Aligns ordered progress and current-step semantics |
| 6 | `FormSection` | Start, Project, Wizard, Criteria | Standardizes field hierarchy and validation placement |
| 7 | `DocumentPanel` and `DocumentList` | Materials, Submission, Case Detail | Consolidates document purpose and state |
| 8 | `AISuggestionReviewCard` and `EvidenceDetails` | AI Review, Clerk Tool, Case Detail | Standardizes human review and provenance |
| 9 | `ValidationIssueCard` | Application Validation, Case Detail | Standardizes blocker, correction, and resolution actions |
| 10 | `MetricsCard` and `AdminDataTable` | Impact, Evaluation, Audit, Readiness | Supports compact Platform operations after content is separated |
| 11 | `PresenterProofCard` | Presenter, Scenarios | Last because it depends on prepared destination states |

## CSS implementation order

| Order | CSS scope | Outcome |
|---:|---|---|
| 1 | Map existing primitives to semantic color, spacing, type, radius, and focus tokens | New components stop introducing local values |
| 2 | Page anatomy for overview, work area, details, and constrained content widths | Pages gain consistent hierarchy before decoration |
| 3 | Status and next-action band with all semantic states | Blockers and recovery remain visually coherent |
| 4 | Header, context, stepper, and sticky action behavior | Navigation and workflow stay stable on desktop and narrow screens |
| 5 | Forms, validation, and document rows | Applicant work becomes scannable and predictable |
| 6 | Evidence, native disclosure, and technical detail styles | Secondary information remains accessible without dominating |
| 7 | Operational and administrative tables | Caseworker and Platform data gains consistent density and focus |
| 8 | Suggestion, metric, validation, and presenter repeated-item treatments | Repeated decision surfaces gain stable dimensions |
| 9 | Responsive stacking, overflow, and fixed-control collision checks | Narrow layouts remain usable without hiding actions |
| 10 | Print and receipt rules | Consequential outcomes remain referenceable |

Do not begin with page-specific color, spacing, shadow, or typography changes. Delete
obsolete selectors as their markup is retired instead of preserving parallel systems.

## Quick wins

| Rank | Change | Impact | Effort |
|---:|---|---|---|
| 1 | Remove Applicant and Caseworker presenter proof | Restores workspace focus | Low |
| 2 | Remove Materials suggestion acceptance and link to AI Review | Eliminates a duplicate decision path | Low |
| 3 | Redirect successful submission to the canonical receipt | Eliminates duplicate outcome state | Low |
| 4 | Use the approved step names and `aria-current="step"` | Clarifies workflow position | Low |
| 5 | Replace technical labels with approved Finnish decision language | Reduces cognitive load across all pages | Low |
| 6 | Collapse keys, JSON, IDs, traces, and payloads with native `details` | Improves Platform scanability | Low |
| 7 | Format dates and times for Finnish users | Improves comprehension and audit use | Low |
| 8 | Add table captions, filter reset, and actionable empty states | Reduces queue and Platform recovery effort | Low |
| 9 | Migrate Presenter and Scenarios to the shared shell | Removes false workspace identity | Low to medium |
| 10 | Replace filename `innerHTML` with safe text rendering | Removes an avoidable input risk | Low |

## Major redesigns

| Rank | Redesign | Impact | Effort | Main risk |
|---:|---|---|---|---|
| 1 | Reassign and split the six wizard activities | Very high | High | Route-compatible state and form behavior |
| 2 | Consolidate Materials across upload, workbench, and wizard | Very high | High | Preserving analysis and deep links |
| 3 | Rebuild Project around portfolio status and synchronization decisions | High | High | Translating conflict states without losing actions |
| 4 | Recompose Case Detail around evidence-backed decisions | High | High | Incomplete persistence for drafts and status actions |
| 5 | Split Impact, AI Operations, Evaluation, Audit, and Readiness content | Very high | High | Current snapshot data mixes capability concerns |
| 6 | Separate Criteria and Knowledge Sources | High | High | Existing actions share one route and template context |
| 7 | Complete clerk-tool review dispositions and evidence | High | High | Requires new persisted review state |
| 8 | Add real evaluation runs and regression history | High | High | Current refresh is not an evaluation execution |

## Prioritized sprint roadmap

### Sprint 1: Quick wins

Goal: remove misleading duplication and technical leakage before introducing shared
presentation abstractions.

* Remove presenter proof from Applicant and Caseworker templates
* Remove integration events and technical job traces from operational workspaces
* Make AI Review the only application suggestion-decision surface
* Apply approved Finnish labels to workflow, statuses, confidence, and blockers
* Make Receipt the post-submit outcome and preserve project context
* Add current-step semantics, table captions, filter reset, actionable empty states,
  Finnish date formatting, and safe filename rendering
* Move keys, payloads, IDs, and JSON into existing native disclosures

Exit criteria: every changed page still uses its existing route and action contract;
the full smoke suite passes; no operational page exposes presenter-only content.

### Sprint 2: Shared components and shells

Goal: provide the shared page anatomy required by every later redesign.

* Implement `PageHeader`, `StatusSummary`, and `RecommendedNextAction`
* Implement semantic status, action, layout, focus, and disclosure tokens
* Implement the approved Stepper and Project Context variants
* Add `TechnicalDetailsDisclosure` and standard table or form foundations
* Migrate Presenter and Scenarios to the canonical Platform shell
* Add focused component render tests and desktop or narrow browser checks

Exit criteria: at least two real pages consume each general component; no component
duplicates page-specific markup; all three workspace identities remain intact.

### Sprint 3: Applicant workspace redesign

Goal: create one continuous value flow from start to receipt.

* Separate Home selection from Start creation
* Rebuild Project around portfolio status and synchronization actions
* Consolidate upload and document management under Materials
* Split wizard step bodies and align them to the approved six activities
* Implement canonical AI Review, Validation, and Submission surfaces
* Retire duplicate package submission markup behind route compatibility
* Complete the canonical Receipt outcome

Exit criteria: every Applicant page has one primary job, status and next action agree,
Materials owns documents, AI Review owns suggestion decisions, and Receipt exists once.

### Sprint 4: Caseworker workspace redesign

Goal: shorten the path from queue prioritization to a documented casework decision.

* Recompose the queue around urgency, readiness, deadlines, and filter recovery
* Preserve queue state through case detail and return navigation
* Recompose case detail around status, next decision, observations, and official evidence
* Apply `EvidenceDetails` and `ValidationIssueCard` to AI-supported observations
* Collapse full application data, resolved observations, drafts, and history
* Add persistence and confirmation only for controls backed by explicit server behavior

Exit criteria: the queue recommends the next case; case detail recommends the next
casework action; AI remains advisory; official evidence is visually distinct.

### Sprint 5: Demo Lab and platform consolidation

Goal: make Platform operations capability-led, actionable, and technically inspectable
without exposing implementation detail by default.

* Turn Demo Lab into a status-led capability overview
* Reduce Impact to outcome measures and methodology
* Separate Criteria from Knowledge Sources
* Establish canonical AI Operations, Evaluation, Audit, and Readiness content
* Recompose Integrations, API testing, and the clerk tool under one capability
* Apply `MetricsCard`, `AdminDataTable`, and technical disclosures consistently
* Complete Presenter scenes only after destination pages have stable prepared states
* Retire legacy shell markup after behavior and route parity

Exit criteria: each Platform capability has one primary job and canonical destination;
technical evidence is available but collapsed; Presenter guides existing product states
without becoming a product or workspace.

## Verification gates

Every sprint closes with these checks:

* Page job, workspace, and canonical destination agree with the approved architecture
* Overview, work area, and details are recognizable without decorative containers
* Status, blocker, and recommended next action describe the same state
* One primary action is visible and recoverable
* Technical detail is absent or collapsed for the active role
* Reused components replace copied markup
* Existing deep links, browser back behavior, forms, and POST actions remain valid
* Keyboard order, focus, headings, labels, contrast, overflow, and fixed controls pass
  desktop and narrow checks
* Smoke tests cover the changed route, canonical ownership, and removed duplication