---
title: KEHA LV Domain Context
description: Canonical domain vocabulary for the applicant workspace UX
ms.date: 2026-08-14
ms.topic: reference
---

## Core Terms

* Account is the applicant organization or customer workspace. It is not a login identity.
* Case is the durable hanke owned by an Account and used to group related permit work.
* Application is a permit-specific application that belongs to one Case.

The primary relationship is Account -> Case -> Application.

## Applicant Vocabulary

The structural terms remain Account, Case, and Application. Applicant-facing Finnish
copy uses `Omat asiat` for the Account workspace, `Hanke` for a Case, and `Hakemus`
for an Application.

## Check verdict vocabulary

* Issue is one finding from the application check, produced by deterministic rules or a validator agent.
* Severity of an Issue is `blocking` (cannot submit), `major` (likely correction request), or `minor`.
* Type of an Issue is `missing`, `incomplete`, `inconsistent`, or `risk` — what the defect is, independent of severity.
* EvidenceRef anchors an Issue to a source: `source_id` plus either a verbatim `quote` or the fired rule in `locator`.
* criteria_id links an Issue to a specific permit criterion; `None` means the finding is not tied to one.

* Verdict is the composed result of the application check, stored as `check_report:{permit_type}`: issues + criteria_results + score + readiness + counts + degraded.
* Score and readiness are derived in code from the verdict — never emitted by an LLM.
* degraded names the check agents that fell back (`fallback_used_agents`) and carries reconciliation notes; a degraded verdict warns the applicant instead of silently claiming a clean result.

* Submission gate is the applicant-facing outcome that determines whether the application may be sent. It is separate from improvement insights.
* Improvement insight is a source-backed, non-blocking observation about how a ready application could be made clearer or stronger.
* Official submission evidence can cover more than one document role. AI-only context is not evidence for the authority package.
