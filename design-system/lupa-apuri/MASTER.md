---
title: Lupa-apuri Master Design System
description: Canonical visual, interaction, accessibility, and component rules for the Lupa-apuri permit product
ms.date: 2026-07-29
ms.topic: reference
---

## Scope

This file is the visual and interaction source of truth for Lupa-apuri. The
[product architecture](../../docs/product-architecture.md) owns roles, journeys,
workspaces, navigation, and page responsibilities. This file owns how those
decisions appear and behave.

All templates inherit these rules. A page-specific exception requires a documented
reason under [Page-category overrides](#page-category-overrides). Local preference
is not a reason.

## Design philosophy

Lupa-apuri is a calm, credible desktop workbench for consequential public-sector
work. It must help users scan, compare, decide, and recover without mistaking AI
output for official truth.

The visual language is:

* Quiet rather than theatrical
* Dense enough for repeated professional work
* Plain-spoken rather than implementation-driven
* High contrast without becoming visually harsh
* Structured by hierarchy, not by decorative cards
* Explicit about status, ownership, evidence, and next action
* Restrained in motion and free of decorative gradients or illustrations

The product has three shell identities: Applicant/LV-facing, Caseworker, and
Demo Lab/Platform. Integrations, administration, evaluation, and demo controls
are capability groups inside Demo Lab/Platform. Presenter is a mode that adds
curated guidance to existing pages without changing their workspace identity.

The product does not use marketing heroes, oversized display typography, nested
cards, purple AI styling, glass effects, decorative orbs, or ornamental animation.

## Semantic color system

Use semantic variables in templates and components. Do not use primitive colors
directly outside token definitions.

### Foundations

| Token | Value | Purpose |
|---|---:|---|
| `--color-canvas` | `#f7f9fb` | Product background |
| `--color-surface` | `#ffffff` | Primary work surface |
| `--color-surface-subtle` | `#f1f5f9` | Secondary bands and grouped controls |
| `--color-surface-strong` | `#e2e8f0` | Selected or emphasized neutral surface |
| `--color-text` | `#172033` | Primary text |
| `--color-text-muted` | `#526173` | Secondary text |
| `--color-text-subtle` | `#64748b` | Metadata that remains readable |
| `--color-border` | `#cbd5e1` | Default boundary |
| `--color-border-subtle` | `#e2e8f0` | Internal separators |
| `--color-border-strong` | `#94a3b8` | Emphasized boundary |
| `--color-brand` | `#123a64` | Product identity and primary navigation |
| `--color-brand-hover` | `#0b2d50` | Brand interaction hover |
| `--color-action` | `#075f96` | Primary actions and links |
| `--color-action-hover` | `#064e7b` | Primary action hover |
| `--color-focus` | `#0b79bf` | Keyboard focus ring |
| `--color-on-strong` | `#ffffff` | Text on brand and action colors |

### Status colors

Status color never carries meaning alone. Pair it with a label and, for important
states, an icon.

| State | Foreground | Surface | Border | Use |
|---|---:|---:|---:|---|
| Neutral | `#475569` | `#f1f5f9` | `#cbd5e1` | Not started or informational |
| In progress | `#075f96` | `#eaf5fb` | `#7cc4e8` | Active work or processing |
| Ready | `#166534` | `#edf8f0` | `#86c995` | Ready for the next decision |
| Attention | `#8a4b08` | `#fff7e6` | `#e9b866` | Review needed but not blocked |
| Blocked | `#a61b1b` | `#fff1f1` | `#e89a9a` | Action cannot proceed |
| Completed | `#285a46` | `#eef7f3` | `#92c9b1` | Finished outcome |
| AI pending | `#075f96` | `#edf6fb` | `#8cc8e5` | AI suggestion awaits a person |
| AI accepted | `#166534` | `#edf8f0` | `#86c995` | Person accepted a suggestion |
| AI rejected | `#475569` | `#f1f5f9` | `#cbd5e1` | Person rejected a suggestion |

Destructive actions use `#b42318` with white text. Destructive styling is reserved
for deleting, revoking, or irreversibly discarding data. Do not use red for normal
validation guidance.

## Typography

Use locally available or bundled fonts so the offline demo does not depend on a
network request.

Preferred families:

* Headings and navigation: `Lexend`, with `Avenir Next`, `Segoe UI`, and sans-serif fallbacks
* Body and controls: `Source Sans 3`, with `Avenir Next`, `Segoe UI`, and sans-serif fallbacks
* Technical details: `SFMono-Regular`, `Consolas`, `Liberation Mono`, and monospace fallbacks

Lexend differentiates product hierarchy without becoming editorial. Source Sans 3
supports dense forms and tables. If neither font is bundled, use the fallbacks and
preserve the same size, weight, and line-height tokens.

### Type scale

| Token | Size | Line height | Weight | Use |
|---|---:|---:|---:|---|
| `--text-page-title` | `1.75rem` | `2.2rem` | `600` | One page title |
| `--text-section-title` | `1.25rem` | `1.65rem` | `600` | Major work-area heading |
| `--text-subsection-title` | `1rem` | `1.4rem` | `600` | Panel and form-section heading |
| `--text-body` | `1rem` | `1.5rem` | `400` | Normal content |
| `--text-body-compact` | `0.9375rem` | `1.35rem` | `400` | Dense tables and workbench metadata |
| `--text-label` | `0.875rem` | `1.2rem` | `600` | Form and control labels |
| `--text-meta` | `0.8125rem` | `1.15rem` | `400` | Timestamps and secondary metadata |
| `--text-code` | `0.8125rem` | `1.25rem` | `400` | Technical details only |

Rules:

* Use one page title per page
* Keep application and dashboard headings compact
* Do not scale type with viewport width
* Use normal letter spacing (`0`)
* Keep paragraphs near 70 characters where layout permits
* Use sentence case for Finnish and English headings, labels, and buttons
* Use tabular numerals for metrics and aligned numeric columns

## Spacing and dimensions

The base spacing unit is 4px.

| Token | Value | Typical use |
|---|---:|---|
| `--space-1` | `0.25rem` | Icon and label micro-gap |
| `--space-2` | `0.5rem` | Compact control gap |
| `--space-3` | `0.75rem` | Field internals and table cells |
| `--space-4` | `1rem` | Standard component padding |
| `--space-5` | `1.25rem` | Panel padding |
| `--space-6` | `1.5rem` | Section padding |
| `--space-8` | `2rem` | Major section separation |
| `--space-10` | `2.5rem` | Page-level separation |
| `--space-12` | `3rem` | Rare workspace-home separation |

Stable shell dimensions:

| Element | Dimension |
|---|---:|
| Global header | `3.5rem` high |
| Desktop sidebar | `15rem` wide |
| Collapsed sidebar | `4rem` wide, only when implemented accessibly |
| Project context bar | Minimum `3rem` high |
| Standard control | `2.5rem` high |
| Compact table row | Minimum `2.75rem` high |
| Standard table row | Minimum `3.25rem` high |
| Icon button | `2.5rem` square |
| Main content | `max-width: 90rem` |
| Reading/form column | `max-width: 52rem` |

## Shape, borders, and elevation

| Token | Value | Use |
|---|---:|---|
| `--radius-control` | `4px` | Inputs, buttons, status tags |
| `--radius-panel` | `6px` | Standalone panels and repeated cards |
| `--radius-dialog` | `8px` | Dialogs only |
| `--border-default` | `1px solid var(--color-border)` | Standard boundary |
| `--shadow-raised` | `0 1px 3px rgb(15 23 42 / 0.12)` | Menus and sticky regions |
| `--shadow-dialog` | `0 12px 30px rgb(15 23 42 / 0.18)` | Dialogs only |

Page sections are unframed or separated by borders. Use panels only when an element
needs a distinct interaction or repeated-item boundary. Do not place a card inside
another card. Avoid shadows on normal form sections and data tables.

## Page layout

The default desktop page anatomy is:

```text
Global header
Workspace navigation | Project context bar
                     | Page header
                     | Status and recommended action
                     | Primary work area
                     | Details and history
```

Layout rules:

* Constrain text and forms, not operational tables
* Use full available width for queues and administration tables
* Use a two-column workbench only when the secondary column supports the same job
* Keep the primary work area at least twice as wide as a secondary evidence panel
* Stack columns below 1024px without hiding content or actions
* Preserve a hint of surrounding context when a sticky action region is present
* Do not use floating page-section cards
* Do not repeat the page title inside the work area

## Responsive behavior

Desktop is the primary operating mode. Narrow viewports remain functional.

* At 1280px and wider, show the full workspace navigation and workbench layout
* From 1024px to 1279px, reduce page gutters and allow selected two-column layouts
  to stack
* Below 1024px, replace persistent workspace navigation with an accessible drawer
* Below 768px, make data tables horizontally scrollable with a visible table caption,
  or convert only when row meaning remains intact
* Keep primary actions reachable without covering content
* Ensure long Finnish labels wrap instead of shrinking text
* Use appropriate `inputmode` and native input types on touch devices

## Interaction and motion

Motion communicates state; it does not decorate the interface.

* Use `120ms` to `180ms` transitions for hover, focus, disclosure, and selection
* Animate opacity or small position changes only
* Do not animate layout dimensions on dense forms and tables
* Show progress for operations longer than 300ms
* Preserve the triggering control and announce operation results
* Respect `prefers-reduced-motion: reduce`
* Do not add scroll reveals, parallax, looping animation, or generic page-load sequences

## Accessibility contract

Every component and page must meet these rules:

* Meet WCAG 2.2 AA contrast for text, controls, states, and focus indicators
* Provide a skip link to the main content
* Keep keyboard order equal to visual order
* Use semantic landmarks and sequential heading levels
* Associate every input with a persistent visible label
* Use `fieldset` and `legend` for meaningful control groups
* Provide visible `:focus-visible` indicators of at least 2px
* Announce asynchronous success, error, and progress through an appropriate live region
* Move focus to the first invalid field only after submission, not while typing
* Identify errors in text and connect them with `aria-describedby`
* Mark the current workspace destination, step, and page with semantic current-state attributes
* Keep native disclosure, button, link, table, and form semantics when possible
* Ensure icon-only buttons have an accessible name and tooltip
* Never use placeholder text as the only label or instruction

## Iconography

Use Lucide icons when the frontend adopts an icon library. Until then, prefer text
and familiar Unicode-free CSS or existing accessible icon assets rather than adding
a dependency for a few symbols.

Rules:

* Use icons to identify actions, statuses, and object types
* Pair unfamiliar icons with text or a tooltip
* Use a consistent 18px icon size in standard controls
* Do not use emojis as product icons
* Do not use manually drawn decorative SVG illustrations

## Controls

### Buttons

Button hierarchy:

| Variant | Use | Limit |
|---|---|---|
| Primary | The page's recommended action | One visible primary action per page state |
| Secondary | Alternative or supporting command | Use beside or below the primary action |
| Tertiary | Low-emphasis inline command | Use for noncritical actions |
| Icon | Familiar tool action | Requires accessible name and tooltip |
| Destructive | Irreversible action | Separate from normal actions and confirm |

All buttons use stable dimensions so loading labels and icons do not shift the layout.
During submission, keep the label visible where possible, show progress, and prevent
duplicate activation.

Do not use button styling for navigation that should behave as a link. Do not use a
rounded text pill when a familiar icon communicates the tool action.

### Form controls

* Use a standard height of 40px for single-line controls
* Use 12px horizontal control padding
* Align labels above controls in forms with mixed input lengths
* Keep help text below the label and errors below the control
* Mark required fields with text or a symbol explained once per form
* Use segmented controls for a small set of mutually exclusive modes
* Use checkboxes or switches only for binary settings
* Use select menus for bounded option sets; use autocomplete only for long lists
* Keep readonly values visually distinct from disabled controls
* Do not disable a control when a readonly explanation is more informative

### Tables

Operational tables are compact, scannable, and action-oriented.

* Use sticky headers only when the table scrolls inside a defined region
* Left-align text and status; right-align comparable numbers
* Keep row actions in the last column
* Show primary identity in the first column
* Include a visible caption or nearby table heading
* Use borders and subtle row hover, not zebra stripes by default
* Persist filters when returning from a detail page where current routes permit it
* Place raw payloads in expandable rows or technical disclosures
* Never rely on truncated text without exposing the full value

### Disclosures

Use native `details` and `summary` when they meet the interaction need.

* Summaries state what is hidden, not "Show more"
* Keep decision-critical content outside disclosures
* Use collapsed defaults for history, evidence excerpts, raw JSON, and diagnostics
* Preserve disclosure state during in-page operations where practical
* Do not nest disclosures more than one level deep

## Component specifications

The component names and responsibilities come from the product architecture. This
section defines their common visual and state behavior.

### AppShell and WorkspaceHeader

* Use the brand color in the global header and neutral surfaces in workspace navigation
* Keep product identity and the current workspace name visible in the full desktop shell
* Separate authorized workspace choices from current-workspace capability links
* Use a 3px active indicator plus stronger text for the current destination
* Keep global header tools aligned to a stable 40px control grid
* Avoid drop shadows between normal shell regions; use borders
* Use only three workspace names: `Hakija ja LV`, `Käsittelijä`, and `Demo Lab ja alusta`
* Do not label an integration tool, admin section, or presenter mode as a workspace

### ProjectContextBar

* Use a subtle surface and bottom border
* Give the project or case name the strongest emphasis
* Use compact metadata separated by layout, not punctuation-heavy strings
* Show status with label and icon when consequential
* Wrap on narrow viewports without covering the page title

### PageHeader

* Keep breadcrumbs above the title
* Align one primary action to the title area on wide screens
* Stack the action below the title on narrow screens
* Keep supporting text to two lines where possible
* Use an overflow menu only when at least three secondary commands exist

### Stepper

* Give each step a stable number marker and short label
* Mark the current step with `aria-current="step"`
* Use completed, current, available, and unavailable states
* Do not mark incomplete future steps as errors
* Wrap to a compact list below 768px rather than shrinking labels

### StatusSummary and RecommendedNextAction

Render these as adjacent unframed regions or one split status band, not nested cards.

* Status occupies the larger region and states condition plus reason
* Recommended action uses one concise command and direct destination
* Blocked state lists the most important blocker first
* Waiting state names the responsible party and last meaningful update
* Completed state does not manufacture another action

### FormSection

* Use an unframed section with a heading, description, and optional completion marker
* Separate sections with 1px borders and 32px vertical spacing
* Use two columns only for short, related fields
* Return validation feedback at field and section level
* Keep collapsed sections accessible and summarize incomplete or error counts

### DocumentPanel and DocumentList

* Put upload controls above the document list
* Distinguish official attachments and AI-only background material through a visible
  purpose label, not internal scope values
* Keep filename, type, purpose, state, and action aligned across rows
* Use a stable icon column for object type
* Show analysis progress inline without replacing the document identity

### AISuggestionReviewCard

The suggestion card is a decision surface, so it may use a panel boundary. Do not
place it inside another decorative card.

* Lead with the human-readable target field
* Compare current and proposed values in stable columns when both exist
* Display AI origin visibly but without purple or novelty styling
* Use plain confidence labels such as `Vahva`, `Tarkistettava`, and `Epävarma`
* Keep source and rationale one disclosure away
* Keep accept, edit, and reject actions together in that order
* Following acceptance, show the accepted value and allow normal form editing
* Following rejection, retain a compact audit record without keeping the card prominent

### EvidenceDetails

* Use a neutral bordered disclosure
* Identify source before excerpt
* Highlight only the relevant excerpt text using semantic `mark` styling
* State when evidence is absent or unavailable
* Never present an AI rationale as source evidence

### ValidationIssueCard

* Use a 4px left status border plus a status label
* State the issue in one sentence and the resolution as an action
* Link directly to the responsible field, document, or suggestion
* Keep resolved issues collapsed below active issues
* Order active issues by blocked, correction, then recommendation

### MetricsCard

Metrics cards are allowed only as repeated measures in Demo Lab/Platform.

* Use a maximum 6px radius and no shadow
* Lead with the metric label, then value, then comparison
* Include an interpretation or threshold, not a value alone
* Use color only for threshold meaning
* Keep card dimensions stable across a metric row

### AdminDataTable

* Use a full-width table on an unframed surface
* Keep filters in a separate toolbar directly above the table
* Use textual status labels with semantic colors
* Expose row details through navigation or one expandable technical row
* Keep pagination position and result count together
* Use a compact density by default; offer comfortable density only when persistent

### TechnicalDetailsDisclosure

* Use a subdued neutral surface and monospace content
* Label the disclosed content precisely, such as `Request JSON` or `AI run trace`
* Provide copy or download only when the operation is supported
* Wrap long text but preserve structured indentation
* Restrict use to Demo Lab/Platform and optional presenter proof

### PresenterProofCard

* Use a repeated panel with claim, scene goal, expected state, and action
* Add a visible readiness warning when required context is absent
* Keep speaker notes visually secondary
* Distinguish normal story actions from optional technical proof
* Include a recovery action only when a real route or reset operation exists

## Content and terminology

User-facing copy is concise, specific, and action-oriented.

* Use verbs for commands: `Tarkista ehdotukset`, `Korjaa puuttuva tieto`, `Lähetä hakemus`
* Use nouns for destinations: `Hakemukset`, `Aineistot`, `Tarkistus`
* State blockers as conditions and recovery: `Lähetys estyy, koska... Korjaa...`
* Identify AI output explicitly: `AI-ehdotus`, `AI:n löytämä tieto`, `AI-yhteenveto`
* Identify authority-provided and applicant-provided information when provenance matters
* Use Finnish date and time formatting in normal workspaces
* Keep IDs, API terms, agent names, and model names inside technical details
* Avoid exclamation marks, promotional claims, and conversational filler

## Empty, loading, error, and success states

### Empty states

Every empty state explains:

1. What is absent
2. Why it matters
3. The next available action

Use an icon only when it improves recognition. Do not use illustrations. A true zero
state may include a primary creation action; a filtered zero state offers filter reset.

### Loading states

* Show inline progress after 300ms
* Use a skeleton only when the final layout is stable and predictable
* Keep the page title, context, and cancel action available
* State what is being processed in user language
* Do not show fake percent completion

### Error states

* Keep user input intact
* State what failed and what the user can do
* Place recoverable errors near the responsible operation
* Provide a retry only when retry is safe
* Put trace and correlation identifiers in technical details
* Use a full-page error only when the page cannot render meaningfully

### Success states

* Confirm the completed action and its consequence
* Put transient confirmations in a live region near the action
* Use a persistent outcome page for submission and other consequential operations
* Do not rely on a toast for state the user may need to reference later

## AI states

AI is a provenance and review state, not a decorative theme.

| State | Required behavior |
|---|---|
| Processing | Name the user-facing task and preserve existing data |
| Suggested | Show target, value, confidence language, source, and human actions |
| Needs review | Explain uncertainty or contradiction and prioritize the item |
| Accepted | Record human acceptance and treat the value as normally editable |
| Edited | Distinguish the human-edited value from the original suggestion in history |
| Rejected | Record rejection without repeatedly prompting for the same unchanged suggestion |
| Failed | Preserve work, explain recovery, and hide implementation errors by default |
| Unavailable | Let the user continue manually when the workflow permits |

Rules:

* AI never changes submitted or authority-controlled data without an explicit human action
* Confidence never replaces evidence
* AI failure never masquerades as successful completion
* Authority AI output is clearly advisory and requires caseworker disposition
* Applicant-facing AI details use sources and rationale, not model or prompt metadata

## Validation states

| State | Meaning | Visual treatment | Action |
|---|---|---|---|
| Blocker | Submission cannot proceed | Blocked status system | Direct fix required |
| Correction | Important inconsistency or omission | Attention status system | Recommended fix |
| Recommendation | Quality improvement | Neutral or in-progress system | Optional improvement |
| Resolved | Previously active issue is fixed | Completed status system | No primary action |

Validation appears at three coordinated levels:

1. Page status gives counts and the most important blocker
2. Section status identifies the affected area
3. Field or document feedback explains the exact correction

Do not repeat full issue prose at all three levels. Use summary, label, and detail.

## Page-category overrides

These are the only standing exceptions to the common system.

### Applicant application

* The workflow stepper may remain sticky below the project context bar
* A sticky footer may hold Back and the one primary forward action
* The assistant may use a fixed disclosure only on desktop and must never cover the
  primary action or trap keyboard focus

### Authority casework

* A two-column layout may keep application evidence beside the active casework task
* The official attachment list is visually distinguished from AI observations
* Status-changing actions require explicit confirmation and remain visible in history

### Demo Lab and Platform

* Tables may use compact density and horizontal scrolling
* Raw JSON, traces, identifiers, and benchmark detail are permitted only inside
  `TechnicalDetailsDisclosure`
* Capability navigation may use grouped, collapsible sections
* Integrations, criteria, metrics, AI operations, evaluation, audit, readiness,
  scenarios, and presenter controls remain capabilities within this workspace

### Presenter mode

* Presenter controls may remain sticky to support live narration
* Prepared proof cards may deep-link across workspaces
* Technical evidence may be exposed intentionally, but remains optional to the main story
* Demo warnings may identify missing seeded data or service configuration
* Presenter mode may emphasize a recommended route, but it does not replace the
  destination workspace name or navigation

### Receipt and printable report

* Receipt removes editable workflow controls
* Printable reports may omit workspace navigation while retaining product identity,
  generated date, scope, and page title
* Print styles remove shadows, interactive controls, and nonessential disclosures

## Implementation rules

* Build the system with semantic CSS custom properties in the existing stylesheet
* Use Jinja partials for components with repeated structure
* Prefer native HTML behavior before JavaScript
* Add an abstraction only after two real consumers need the same pattern
* Keep existing routes while shells and templates migrate
* Do not add a CSS framework or component dependency for this redesign
* Do not copy full component markup into page templates after a shared partial exists
* Do not add page-specific colors, radii, shadows, or font sizes without updating this file

## Delivery checklist

Before a page is complete, verify:

* The page belongs to one of the three workspaces and has one primary job
* Status, blockers, and the recommended next action agree
* One primary action is visible for the current state
* Technical detail is absent or collapsed for the role
* AI output exposes provenance and human control
* Labels, headings, landmarks, focus, and keyboard order are correct
* Text and controls meet WCAG 2.2 AA contrast
* Finnish labels wrap without overlap at desktop and narrow widths
* Loading, empty, error, and success states preserve context
* Tables and fixed controls do not cause incoherent overlap
* The page uses semantic tokens and shared components
* The browser back action and deep link remain predictable

> [!IMPORTANT]
> A design-system generator suggested a marketing gateway, massive typography, and
> scroll-reveal animation. Those recommendations are not part of this system. Lupa-apuri
> is an operational workbench, so it retains only the generator's useful guidance on
> accessible contrast, readable type, dense layout, visible focus, and status feedback.