# Plan: AI tools side panel with step-based progress and copilot chat

## 1. Design principles (from Microsoft Copilot UX guidelines)

The [Copilot MCP Apps UI guidelines](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/plugin-mcp-apps-ui-guidelines)
and [Atlas Design chat pattern](https://design.learn.microsoft.com/patterns/chat.html)
provide the foundation:

| Principle | What it means for us |
|---|---|
| **Conversation-first** | Chat is always visible and primary. The side panel is a workspace, not a replacement for conversation. |
| **Progressive complexity** | Start with glanceable state. Expand only when the user chooses to act. Each AI step is a discrete, user-triggered action. |
| **Preserve human control** | Every AI action is user-initiated. Results require explicit accept/reject. Nothing auto-applies. |
| **Scale density with intent** | Inline status for idle state. Side-by-side panel for active work. The panel always occupies its space — no layout jumping. |
| **Extract capabilities, don't replicate** | Each AI agent is one atomic capability with one button. Not a monolithic "analyze everything" black box. |
| **No full applications in widgets** | The side panel is a focused task workspace — no tabs, no sub-navigation, no global chrome. |

## 2. Target layout

```
┌─ App header (light_base.html, unchanged) ─────────────────────────────┐
│  Lupa-apuri  │  Työtila: Hakija ja LV  │  [workspace picker]          │
├─ Project context bar ─────────────────────────────────────────────────┤
│  Avoin hanke: Rakennuslupa Vantaa  │  Rakennuslupa  │  v1             │
├─ Wizard stepper (when entered from wizard) ───────────────────────────┤
│  ✓ 1. Yhteiset tiedot  →  ✓ 2. Aineisto  →  ● 3. Hakemus  →  4. ... │
├────────────────────────────────────────────┬──────────────────────────┤
│                                            │  AI-työkalut  360px       │
│  Workbench main (flex: 1)                  │                          │
│  ┌──────────────────────────────────┐      │  ┌──────────────────────┐ │
│  │ Readiness dashboard              │      │  │ ○ Dokumenttien lukija│ │
│  │ Score ring + summary             │      │  │   [▶ Aloita]         │ │
│  └──────────────────────────────────┘      │  │                      │ │
│  ┌──────────────────────────────────┐      │  │ ○ Liitteiden luokit. │ │
│  │ Findings list                    │      │  │   [  Odottaa]        │ │
│  │ Blocking / major / minor         │      │  │                      │ │
│  └──────────────────────────────────┘      │  │ ○ Lomakkeen rakent.  │ │
│  ┌──────────────────────────────────┐      │  │   [  Odottaa]        │ │
│  │ Extraction review queue          │      │  │                      │ │
│  │ Field-by-field accept/reject     │      │  │ ... (6 steps total)  │ │
│  └──────────────────────────────────┘      │  ├──────────────────────┤ │
│  ┌──────────────────────────────────┐      │  │ 💬 Keskustele AI:n   │ │
│  │ Attachment scope                 │      │  │   kanssa             │ │
│  │ Official / AI-only / undecided   │      │  │ [copilot chat area]  │ │
│  └──────────────────────────────────┘      │  ├──────────────────────┤ │
│                                            │  │ ❓ Apua               │ │
│                                            │  └──────────────────────┘ │
└────────────────────────────────────────────┴──────────────────────────┘
```

### Layout rules

- **Side panel is always 360px wide** (per `v5/visual_design_tokens.yaml`). Even when idle, the space is reserved — no content shift.
- **Chat is always visible in the panel** (conversation-first principle). The chat input uses the Atlas Design `textarea-form` pattern with send button inside the textarea footer.
- **On narrow viewports (<1024px)**: the side panel collapses to a bottom sheet (per `v5/visual_design_tokens.yaml` `mobile: bottom-sheet`). A floating action button toggles it.
- **Global padding of the panel**: 24px (per Copilot MCP guidelines).

## 3. Side panel anatomy (top to bottom)

### 3a. AI job steps section

Six discrete, user-triggerable steps. Each step is an independent AI job with `depends_on` chain.

```
┌──────────────────────────────┐
│ AI-työkalut                  │
│                              │
│ ✓ 1. Dokumenttien lukija     │
│    3 tiedostoa · 0,8 s       │
│    [Näytä tiedot]            │
│                              │
│ ● 2. Liitteiden luokittelija │
│    ████████░░ 67 % · ~4 s    │
│    [Keskeytä]                │
│                              │
│ ○ 3. Lomakkeen rakenteistaja │
│    [  Odottaa edellistä]     │
│                              │
│ ○ 4. Kriteeritarkistus       │
│    [  Odottaa]               │
│                              │
│ ○ 5. Puutteiden tarkastus    │
│    [  Odottaa]               │
│                              │
│ ○ 6. Parannusehdotukset      │
│    [  Odottaa]               │
└──────────────────────────────┘
```

#### Step states

| State | Visual | Button | Meaning |
|---|---|---|---|
| `pending` | Hollow circle ○, gray text | `Aloita` | Ready, user hasn't triggered yet |
| `blocked` | Hollow circle ○, muted text | `Odottaa edellistä vaihetta` | Previous step must finish first |
| `running` | Filled blue ●, progress bar | `Keskeytä` | AI is processing |
| `waiting_for_user` | Filled amber ●, hand icon | `Tarkista (3)` | AI done, needs human review. Count = pending items. |
| `completed` | Green check ✓ | `Näytä tiedot` | Done, results link to relevant section in main area |
| `failed` | Red ✗, error summary | `Yritä uudelleen` | Error, can retry |

#### Step pipeline definition (in `services/jobs.py`)

```python
PIPELINE_STEPS = [
    {
        "kind": "document_reader",
        "label": "Dokumenttien lukija",
        "description": "Lukee ja tunnistaa ladatut asiakirjat",
        "depends_on": None,
        "eta_formula": lambda docs, chars: min(30, 5 + docs * 2),
        "agents": ["text_extractor"],
    },
    {
        "kind": "attachment_classifier",
        "label": "Liitteiden luokittelija",
        "description": "Päättelee liitteiden käyttötarkoituksen",
        "depends_on": "document_reader",
        "eta_formula": lambda docs, chars: min(20, 3 + docs),
        "agents": ["material_classifier"],
    },
    {
        "kind": "structure_extractor",
        "label": "Lomakkeen rakenteistaja",
        "description": "Poimii hakemuskentät aineistosta",
        "depends_on": "attachment_classifier",
        "eta_formula": lambda docs, chars: min(60, 10 + docs * 3),
        "agents": ["structure_extractor", "field_suggestion_agent"],
    },
    {
        "kind": "criteria_agent",
        "label": "Kriteeritarkistus",
        "description": "Vertaa hakemusta ohjeisiin ja kriteereihin",
        "depends_on": "structure_extractor",
        "eta_formula": lambda docs, chars: min(45, 12 + docs * 2),
        "agents": ["criteria_assessor", "completeness_validator"],
    },
    {
        "kind": "gap_analyzer",
        "label": "Puutteiden ja ristiriitojen tarkastus",
        "description": "Etsii puutteet ja epäjohdonmukaisuudet",
        "depends_on": "criteria_agent",
        "eta_formula": lambda docs, chars: min(45, 12 + docs * 2),
        "agents": ["gap_analyzer", "consistency_validator", "question_generator"],
    },
    {
        "kind": "improvement_agent",
        "label": "Parannusehdotukset",
        "description": "Muodostaa korjaussuunnitelman",
        "depends_on": "gap_analyzer",
        "eta_formula": lambda docs, chars: 12,
        "agents": [],
    },
]
```

#### ETA strategy

Azure OpenAI's Responses API does **not** return an ETA field. The strategy:

1. **Before call**: Use the `eta_formula` — document-count and character-count based. Shown as initial estimate.
2. **During streaming call**: Count received tokens against `max_output_tokens`. `progress_pct = received_tokens / max_output_tokens * 100`. Estimate remaining from elapsed time and current progress. The progress bar and ETA refine in real-time.
3. **After call**: Show actual elapsed time. The estimate is replaced with the real value.

This works identically for mock (instant → 100% immediately) and Azure (progressive). No special-casing.

### 3b. Copilot chat section

Uses the [Atlas Design chat pattern](https://design.learn.microsoft.com/patterns/chat.html) for message styling and the `textarea-form` component for input.

```
┌──────────────────────────────┐
│ 💬 Keskustele AI:n kanssa    │
│                              │
│  ┌─ chat messages ─────────┐ │
│  │                          │ │
│  │ [AI] Miten voin auttaa   │ │
│  │      hakemuksen kanssa?  │ │
│  │                          │ │
│  │      [You] Onko tämä     │ │
│  │            liite pakolli-│ │
│  │            nen?          │ │
│  │                          │ │
│  │ [AI] Kyllä, rakennus-    │ │
│  │      piirustus on        │ │
│  │      pakollinen liite.   │ │
│  │                          │ │
│  └──────────────────────────┘ │
│                              │
│  ┌─ input area ────────────┐ │
│  │ Kysy tai kuvaile...  ▐  │ │
│  │ 0 / 500            [↑] │ │
│  └──────────────────────────┘ │
│                              │
│  Ehdotukset:                 │
│  [Mitä liitteitä puuttuu?]   │
│  [Mikä on hakemuksen tila?]  │
│  [Selitä tämä kriteeri]      │
└──────────────────────────────┘
```

#### Chat UI rules (from Atlas Design)

- **Messages**: AI responses use `message-sm` (full width). User messages use `message-sm message-sender width-auto` (fit to content, right-aligned).
- **Input**: The `textarea-form` pattern with footer containing character count (left) and send button (right).
- **Suggestion pills**: After each AI response, show 2–4 clickable suggestion chips using `tag tag-sm tag-suggestion`.
- **Error state**: Textarea stays outlined in blue (not red), per Copilot standard.
- **Streaming indicator**: Animated caret `|` at end of AI message while tokens arrive.

#### Chat interaction rules (from Copilot MCP guidelines)

- **Chat is primary**: The conversation is the source of intent. The AI steps panel is a workspace — the user can chat while a step is running.
- **No duplicate features**: Don't recreate prompt input, suggestion chips, or reasoning summaries in the main workbench area. Those belong in the chat.
- **Chat actions trigger AI steps**: When the user clicks a suggestion pill like "Mitä liitteitä puuttuu?", it can auto-fill the chat input or trigger a relevant AI step. The chat can also propose actions that the user accepts — e.g. "Haluatko, että käynnistän kriteeritarkistuksen?" → user clicks "Kyllä" → step 4 starts.

### 3c. Help section

```
┌──────────────────────────────┐
│ ❓ Apua                       │
│                              │
│ Voit käynnistää AI-työkalut  │
│ yksi kerrallaan. Jokainen    │
│ vaihe tuottaa tulokset,      │
│ jotka näet työpöydällä.      │
│                              │
│ AI ei tee muutoksia          │
│ automaattisesti. Sinä        │
│ hyväksyt tai hylkäät         │
│ jokaisen ehdotuksen.         │
│                              │
│ [Näytä ohjeet kokonaan]      │
└──────────────────────────────┘
```

## 4. `waiting_for_user` job status

### Current problem

Jobs go `running → completed` directly. There is no distinction between "AI finished" and "human finished reviewing AI output."

### New state machine

```
queued → running → waiting_for_user → completed
                  ↘ failed
                  ↘ cancelled
     waiting_for_user → running (re-analysis triggered after review)
```

### When does a job enter `waiting_for_user`?

A job enters `waiting_for_user` when AI produces results requiring human review:

- `structure_extractor` produces extraction candidates → `waiting_for_user` until all accepted/rejected
- `criteria_agent` produces findings → `waiting_for_user` until all resolved
- `gap_analyzer` produces open questions → `waiting_for_user` until addressed

### UI behavior

- The step shows an amber indicator with count: "Odottaa hyväksyntääsi · 12 poimintaa jäljellä"
- The corresponding main-area section is highlighted
- The "Tarkista" button links directly to the relevant section (e.g. `#extraction-queue`)
- Once all pending items are addressed, the job auto-completes
- The next step in the pipeline unblocks

## 5. Changes needed (ordered by dependency)

### Phase A: Side panel skeleton (CSS + template)

1. Add the 360px right rail to `package_workbench.html` with three stacked sections.
2. CSS: `.workbench-layout` becomes a two-column grid. `.workbench-side` is fixed 360px, sticky, with 24px internal padding.
3. The current `analysis-panel` progress bar moves into the side panel's AI steps section.
4. The current standalone chat link is replaced with the inline copilot chat in the side panel.
5. Narrow viewport: media query collapses side panel to a bottom sheet.

### Phase B: Step pipeline (`services/jobs.py`)

1. Define `PIPELINE_STEPS` list in `jobs.py`.
2. Add `start_pipeline_step(db, case_id, permit_type, step_kind)` — creates a job for one step, respecting `depends_on`.
3. Add `pipeline_status(db, case_id, permit_type)` — returns status of all 6 steps for the template.
4. Add route: `POST /applicant/packages/{id}/steps/{step_kind}/start` — user triggers a step.
5. Add route: `POST /applicant/packages/{id}/steps/{step_kind}/cancel` — user cancels a running step.
6. Wire ETA formulas into `start_ai_job()` so jobs get document-count-based estimates.

### Phase C: `waiting_for_user` status (`services/jobs.py`)

1. Add `waiting_for_user` to the job status enum.
2. Add `complete_ai_job_with_review(db, job_id)` — transitions to `waiting_for_user` instead of `completed`.
3. Add `resolve_waiting_job(db, job_id)` — transitions to `completed` when all pending items are addressed.
4. Update `ai_job_status_payload()` to surface `waiting_for_user` status and pending item counts.
5. The extraction accept/reject handlers trigger `resolve_waiting_job` when the queue is empty.

### Phase D: Copilot chat integration

1. Style chat messages using the Atlas `message-sm` / `message-sender` / `width-auto` pattern.
2. Style chat input using the `textarea-form` pattern with footer (character count + send button).
3. Add suggestion pills after AI responses using `tag tag-sm tag-suggestion`.
4. Wire suggestion pills to either fill the chat input or trigger AI step actions.
5. Streaming indicator: animated caret during token delivery.

### Phase E: Wizard-workbench handoff

1. When entering the workbench from the wizard (step 3 redirect), pass a `from_wizard=1` query parameter.
2. The workbench renders the wizard stepper as a condensed breadcrumb above the project context bar.
3. The stepper is read-only — clicking a step navigates back to the wizard at that step.
4. When entering standalone (direct `/applicant/packages/{id}`), no stepper is shown.

## 6. What does NOT change

- **Wizard is preserved.** Steps 1, 2, 5, 6 remain in `wizard.html`. The wizard is the linear bookend flow; the workbench is the AI-heavy iterative middle.
- **Authority side is untouched.** This plan only affects applicant-facing AI tooling.
- **API v1 routes are untouched.** The step pipeline uses existing job infrastructure under the hood; external API consumers see no change.
- **`light_base.html` shell is untouched.** Only the workbench template changes.
- **No new dependencies.** All styling uses existing CSS custom properties plus the Atlas Design patterns implemented as plain CSS (no framework added).

## 7. Non-goals (YAGNI)

- No WebSocket or real-time push — page refresh and polling are sufficient for this demo.
- No multi-tenant step pipelines — one pipeline per package, one active step at a time.
- No drag-and-drop reordering of steps — the pipeline order is fixed.
- No chat history persistence beyond the current session — the existing `contextual_chat` artifact is sufficient.
- No Fluent 2 React components — we use plain HTML/CSS styled to match the Atlas/Fluent patterns.

