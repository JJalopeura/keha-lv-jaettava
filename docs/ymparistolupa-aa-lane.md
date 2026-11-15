# Ympäristölupa lane — AA e-service rework (2026-12)

The environmental permit form used to be eight technical tabs (päästöt, jätteet, BAT,
tarkkailu …). It now mirrors the Asiointipalvelu (AA) e-service, and the criteria set
holds exactly one rule per AA section. This note records the mapping, the evidence-path
change that the 2026-11-04 Ren-Gas package forced, and the measured end-to-end results.

## Form: five AA sections, ten prose fields

`app/domains/lupademo/specs/form_schemas/ymparistolupa_teollisuus.schema.json`
(`schema_id: environmental_industrial_v2`)

| AA tab | Field id | Label | Required |
|---|---|---|---|
| Tiivistelmä | `permit_basis` | Hankkeen yleiskuvaus | yes (LV common data) |
| Taustatiedot | `site_location` | Sijainti | yes |
| Taustatiedot | `zoning_context` | Kaavoitus | yes |
| Toiminta | `activity_description` | Toiminnan yleiskuvaus | yes |
| Toiminta | `production_capacity` | Tuotanto ja tuotteet | yes |
| Toiminta | `byproducts` | Sivutuotteet | no (conditional) |
| Toiminta | `process_description` | Prosessit | yes |
| Riskit | `risk_preparedness` | Riskit – yleiskuvaus | yes |
| Ympäristö, päästöt ja vaikutukset | `land_use_environment` | Lähiympäristö ja maankäyttö | yes |
| Ympäristö, päästöt ja vaikutukset | `land_use_impacts` | Vaikutukset maankäyttöön ja kiinteistöjen käyttöön | yes |

Each field carries the AA guidance text as `help`, rendered under the label in the
wizard. `applicant_name`, `business_id`, `activity_name` remain the applicant header
fields (AA has them outside the ten sections).

`permit_basis` kept its id on purpose: it is also an LV common-data field, so the "Uusi
hanke" description and the Kevytasiointi form still carry over.

## Criteria: ten rules, guidance as the instruction

`app/domains/lupademo/specs/criteria_sets/national_environmental_industrial_demo_v1.json`
(`version: 2026-12-demo-2`)

One rule per section; rule `id` equals the field id, `title` equals the section label,
and `instruction` is the AA guidance text verbatim. Severities stay `minor` /
`recommendation` (the minor-only policy is unchanged), so criteria findings never block a
submission. The three attachments the guidance names are the only attachment
requirements:

- `permit_basis` → `general_map` (lähestymis-/yleiskartta 1:100 000–1:200 000)
- `zoning_context` → `zoning_extract` (kaavaote)
- `risk_preparedness` → `risk_preparedness_plan` (ennaltavarautumissuunnitelma)

`byproducts` has a `condition` on `process_description` containing "sivutuote" /
"jätelaki 5": when the applicant is not asking for a by-product classification the rule
is reported green ("ei sovellu") instead of as a gap.

New attachment types: `general_map`, `zoning_report` (kaavaselostus), `process_diagram`
(prosessi-/kytkentä-/virtauskaavio). Classifier needles and requirement aliases were
added for all three; `kaavaselostus` deliberately does **not** satisfy `zoning_extract`.

## The evidence path had to change

The 2026-11-04 package's application is 65 pages. Two caps made its later chapters
invisible:

1. `extract_text_from_path()` read `pdf.pages[:20]` and capped at 24 000 chars — about
   six pages of a 2.4k-chars-per-page document. Everything stored for sections
   Toiminta/Riskit/Vaikutukset was the cover and the table of contents.
2. Retrieval gave every document an even share of the budget and took `text[:budget]`
   — again the head.

Measured on the package: 11 PDFs, 1.03 M chars, 14 s to extract everything (the 207-page
YVA dominates at 8 s). So the caps were raised (`MAX_PDF_PAGES = 400`,
`MAX_EXCERPT_CHARS = 300 000`, `max_chars=INGEST_MAX_CHARS` on zip/file ingest) and three
things followed:

- `_sampled_excerpt()` samples start/middle/end instead of the head (`retrieval_context`).
- `evidence_chunks()` + `_select_evidence()` give each criteria batch the passages its own
  rules talk about, scored by rule-word hits and attachment-role match, inside the same
  9 000-char budget (`CRITERIA_CONTEXT_BUDGET_CHARS`).
- `_material_source_text()` gives the structure extractor the application document first,
  sampled across seven windows; the zip is sorted with `Liite 4 a …` before the
  application, so a plain join would have sent the YVA statement instead.

## Check policy touched

A missing **required form field** is blocking again (`deterministic_issues`). Criteria are
advisory by policy, but the submit gate must still mean something: without this, an empty
form would be submittable because no criterion can produce a blocking finding. Prepared
applications fill every required field, so the minor-only gate still yields zero
blocking/major for them.

Two copy/fidelity fixes around the same area:

- a missing attachment now names the attachment ("Lähestymis- / yleiskartta puuttuu")
  rather than the section that is already filled ("Hankkeen yleiskuvaus puuttuu");
- an agent that answers with a criterion **title** instead of its id is mapped back
  instead of dropped, and a non-applicable conditional rule is forced green.

## Measured end-to-end (2026-11-15, local, real Foundry)

`scripts/e2e_aa_package.py` — wizard and Kevytasiointi, same package, temp DB:

- ingest: 79–95 s for 11 files, all 11 stored (the two AVI decisions, Liite 14 a/b, are
  precedent decisions the applicant attached — they were previously skipped by a
  `päätös` filename heuristic and that loss is fixed)
- classification: the application PDF is the only `environmental_application`; YVA bundle
  → `yva_statement`, UUDELY → `judgement_freshness_assessment`, BAT → `bat_assessment`,
  savukaasumalli → `air_quality_model`, hulevesiarvio → `baseline_report`
- intake analysis: 10 of the 13 form fields extracted by AI (only `permit_basis` and
  `byproducts` left to the applicant)
- check: 102 s · 0 blocking · 1 major · 9 minor · 0 red · 5 green / 5–7 yellow ·
  `incomplete_agents: []`, no fallback
- gate open, submit succeeds, receipt with 9 official attachments
- both paths submitted: wizard `LV-20261115-7B61E2`, Kevytasiointi `LV-20261115-50F8EE`
- whole run 350 s

Run-to-run variance is real: the same case re-checked took 89 s once and 406 s another
time, and the agent findings differ between runs. That is Foundry latency and LLM
variance, not the app; the deterministic criteria results stayed stable.

## What the check no longer covers, on purpose

The YVA statement, BAT assessment, savukaasumallinnus, melumallinnus, perustilaselvitys,
kemikaaliluettelo and hulevesisuunnitelma are still *classified*, stored, shown in the
material list and delivered to the authority when the applicant adds them — they are just
no longer separate criteria rules, so their absence opens no finding. That was the
"strictly the ten sections" decision. Nothing in the UI or `AGENTS.md` claims those checks
run; if that coverage is wanted back, the fix is to add rules with the matching
`attachment_type` (the types and labels already exist) rather than to change the form.

## Fixture policy: one package, the current one

Only `Ren-Gas pelkkä hakemus 20261104.zip` is used for this lane. The earlier, larger
Ren-Gas package (46 MB, with melumallinnus, perustilaselvitys and the neighbourhood maps)
is retired: it solves gaps the demo package deliberately does not contain, so a check
against it is not comparable. `tests/test_criteria_minor_only.py` and both e2e scripts use
the current package only.

Because the current package is thinner, a *correct* check shows **more** suggestions than
the old complete package did — but they stay advisory: only an empty required form field
blocks, criteria are minor-only, and the majors are "merkittävä" review points, not send
blockers (measured: 0 blocking / 1 major / 7 minor on the 11-file run, gate open).

## Step 1 inherits step 3

`permit_basis` (Hankkeen yleiskuvaus) is both the application field and an LV common-data
field. When the applicant fills it on the application form — by hand or by accepting an AI
extraction — an **empty** LV common value is filled from the application
(`_seed_empty_common_fields` in `services/core.py`), so step 1 shows the same text instead
of "-". A non-empty LV common value is never overwritten; divergence stays a visible sync
proposal.

## No document is mandatory

`required_attachments` is empty for this lane. The applicant's upload *is* the package;
the check does not invent a mandatory document list on top of it. The only blocking class
left is an empty required **form** field. The three AA attachments (lähestymiskartta,
kaavaote, ennaltavarautumissuunnitelma) stay as `minor` criteria requirements, so their
absence is a Vähäinen finding — never Merkittävä or Estävä.

That also means the wizard's step-2 "Puuttuvat viranomaisliitteet" banner is intentionally
absent for this permit type; the materials step lists what was uploaded and nothing more.

## Known limits

- The package has no yleiskartta, kaavaote or prosessikaavio, so those requirements
  legitimately show as missing. The two AVI decision PDFs (Liite 14 a/b) are skipped by
  `suggested_scope_for_attachment` because their filenames contain "päätös": they are
  treated as authority documents, not applicant material.
- The wizard step-4 criteria grid only renders non-green criteria that have **no** linked
  issue; with reconciliation (C1) synthesizing a minor issue per yellow criterion, the
  grid is usually empty and the issues list carries the same information.
- `attachment_requirement_covered` still matches requirement terms anywhere in the
  filename/summary/excerpt, so a negated sentence can satisfy a requirement. Not hit by
  this package, still a known weakness.

## Hardening after the independent review (2026-11-15)

An external review of the rework (gpt-6.1-terra, full diff) found five real issues; all
are fixed and pinned by tests:

1. **Authority-only material could reach the model.** `evidence_chunks()` and
   `retrieval_context()` now filter to `official_submission` / `ai_context_only`
   (`EVIDENCE_ALLOWED_SCOPES`). A conformance report is not applicant evidence.
2. **A batch could spend its whole budget on one topic.** `_select_evidence()` reserves
   one passage per rule first, then fills the rest by score, so the section with the
   richest vocabulary in the package cannot starve the others.
3. **Short documents were dropped** (120-char floor) even when they held the only note on
   a topic; the floor is now 40 chars.
4. **`None` counted as filled.** `deterministic_issues()` used
   `str(form_data.get(field_id, ""))`, which turns `None` into `"None"`; it now uses
   `str(value or "")`.
5. **A conditional rule could be forced green while the condition was true elsewhere.**
   The `byproducts` condition now also checks the rule's own field value, not only the
   prose field it points at. Unknown criterion ids are additionally mapped through rule
   *titles* and field *labels*, and the caseworker checklist item that only reads the
   summary was renamed to "Hankkeen yleiskuvaus" (it never checked the legal basis).

Two operational additions:

- `app/infra/error_log.py` appends unhandled request tracebacks to `/home/data/errors.log`
  (the deployment has no Log Analytics, and the platform log shows only container
  lifecycle, so an intermittent 500 otherwise leaves nothing behind).
- the check report's `degraded` block now lists `truncated_documents`: files whose stored
  text stops before the document ends are a stated coverage gap, not a silent one.

## Live deployment measurements (2026-11-15, App Service B1)

`scripts/e2e_live.py` — the same package through the deployed site:

- zip ingest round-trip: 213–235 s (local: 79 s; B1 is 1 vCPU and PDF text extraction is
  CPU-bound), intake analysis 24–29 s. The zip ingest does not chain the analysis on the
  wizard path (the applicant clicks "Aineiston analyysi"); on the Kevytasiointi path the
  ingest job does chain it, so `POST /analyze` answers 409 `analysis_already_running`.
- wizard path on the hardened build: 11 AI extractions offered, check 88 s, 0 blocking,
  gate open, submitted (`LV-20261115-611DCF`); Kevytasiointi path submitted
  (`LV-20261115-961475`) with the same package
- the first live attempt hit one HTTP 500 on `GET .../wizard/2` while the ingest job was
  running; a local 60-poll reproduction never failed and the second live run polled the
  same page aggressively for 4 minutes with zero errors. The error-log middleware now
  exists so a recurrence leaves a traceback instead of a guess.
- all 11 files of the package are ingested; `internal_authority_only` now means authority
  *correspondence* (kuulutus, lausuntopyyntö, vireilletulo), not an applicant's attachment.
