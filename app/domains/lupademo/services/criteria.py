"""Permit criteria CRUD: load, save, version history, rule management.
Depends on domain.py + db_helpers.py only."""

from __future__ import annotations

import copy
import time
from typing import Any

from .core import SPEC_ROOT, application_id_for, artifact_json, event, load_json, save_artifact
from .db_helpers import _dict


CRITERIA_FILES = {
    "building_residential": "vantaa_building_residential_demo_v1.json",
    "environmental_industrial": "national_environmental_industrial_demo_v1.json",
    "event_public": "vantaa_event_public_demo_v1.json",
}


SEVERITY_ORDER = {"recommendation": 0, "minor": 1, "major": 2, "blocking": 3}


def load_criteria(permit_type: str) -> dict[str, Any]:
    filename = CRITERIA_FILES.get(permit_type, CRITERIA_FILES["building_residential"])
    return load_json(SPEC_ROOT / "criteria_sets" / filename, {})


def effective_criteria(db: Any, case_id: str, permit_type: str) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    art = artifact_json(db, case_id, "criteria_set", permit_type, {})
    if art:
        return art
    criteria = load_criteria(permit_type)
    save_artifact(db, case_id, "criteria_set", permit_type, criteria)
    return criteria


def save_criteria_version(db: Any, case_id: str, permit_type: str, criteria: dict[str, Any], editor: str = "admin") -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    current = effective_criteria(db, case_id, permit_type)
    history = artifact_json(db, case_id, "criteria_history", permit_type, {"versions": []})
    history.setdefault("versions", []).append({"ts": time.time(), "editor": editor, "action": "replaced", "criteria": current})
    criteria["version"] = str(criteria.get("version") or f"edited-{int(time.time())}")
    criteria["status"] = str(criteria.get("status") or "active")
    criteria["source_files"] = criteria.get("source_files") or criteria.get("sources") or []
    criteria["updated_at"] = time.time()
    criteria["updated_by"] = editor
    save_artifact(db, case_id, "criteria_history", permit_type, history)
    save_artifact(db, case_id, "criteria_set", permit_type, criteria)
    event(db, case_id, "criteria.updated", "Criteria set updated", {"permit_type": permit_type, "version": criteria.get("version")})
    return criteria


def criteria_history(db: Any, case_id: str, permit_type: str) -> dict[str, Any]:
    return artifact_json(db, case_id, "criteria_history", application_id_for(permit_type), {"versions": []})


def criteria_history_rows(history: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    versions = history.get("versions", []) if isinstance(history, dict) else []
    if not isinstance(versions, list):
        return rows
    for index, item in enumerate(versions):
        criteria = _dict(item).get("criteria", {})
        rows.append(
            {
                "history_index": index,
                "ts": item.get("ts", "") if isinstance(item, dict) else "",
                "editor": item.get("editor", "") if isinstance(item, dict) else "",
                "action": item.get("action", "replaced") if isinstance(item, dict) else "replaced",
                "version": criteria.get("version", "") if isinstance(criteria, dict) else "",
                "status": criteria.get("status", "") if isinstance(criteria, dict) else "",
                "rule_count": len(criteria.get("rules", [])) if isinstance(criteria, dict) and isinstance(criteria.get("rules"), list) else 0,
            }
        )
    return list(reversed(rows))


def criteria_rule_rows(criteria: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    rules = criteria.get("rules", []) if isinstance(criteria, dict) else []
    if not isinstance(rules, list):
        return rows
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            continue
        rows.append(
            {
                "index": index,
                "id": rule.get("id", ""),
                "field_id": rule.get("field_id", ""),
                "attachment_type": rule.get("attachment_type", ""),
                "severity": rule.get("severity", "major"),
                "instruction": rule.get("instruction", ""),
            }
        )
    return rows


def restore_criteria_version(db: Any, case_id: str, permit_type: str, history_index: int, editor: str = "admin") -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    history = criteria_history(db, case_id, permit_type)
    versions = history.get("versions", [])
    if not isinstance(versions, list) or history_index < 0 or history_index >= len(versions):
        raise ValueError("criteria_history_index_not_found")
    selected = versions[history_index].get("criteria", {})
    if not isinstance(selected, dict) or not selected:
        raise ValueError("criteria_history_entry_missing_criteria")
    restored = copy.deepcopy(selected)
    restored["status"] = "active"
    restored["restored_from_version"] = selected.get("version", "")
    restored["restored_at"] = time.time()
    restored["version"] = f"{selected.get('version', 'criteria')}-restored-{int(restored['restored_at'])}"
    saved = save_criteria_version(db, case_id, permit_type, restored, editor=editor)
    event(db, case_id, "criteria.restored", "Criteria version restored", {"permit_type": permit_type, "history_index": history_index, "version": saved.get("version")})
    return saved


def upsert_criteria_rule(db: Any, case_id: str, permit_type: str, rule: dict[str, Any], editor: str = "admin") -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    rule_id = str(rule.get("id") or "").strip()
    if not rule_id:
        raise ValueError("criteria_rule_id_required")
    updated = copy.deepcopy(effective_criteria(db, case_id, permit_type))
    rules = updated.setdefault("rules", [])
    if not isinstance(rules, list):
        rules = []
        updated["rules"] = rules
    clean_rule = {
        "id": rule_id,
        "severity": str(rule.get("severity") or "major"),
        "instruction": str(rule.get("instruction") or "").strip(),
    }
    field_id = str(rule.get("field_id") or "").strip()
    attachment_type = str(rule.get("attachment_type") or "").strip()
    if field_id:
        clean_rule["field_id"] = field_id
    if attachment_type:
        clean_rule["attachment_type"] = attachment_type
    replaced = False
    for index, existing in enumerate(rules):
        if isinstance(existing, dict) and existing.get("id") == rule_id:
            rules[index] = {**existing, **clean_rule}
            replaced = True
            break
    if not replaced:
        rules.append(clean_rule)
    updated["version"] = f"{str(updated.get('version') or 'criteria')}-rule-{int(time.time())}"
    saved = save_criteria_version(db, case_id, permit_type, updated, editor=editor)
    event(db, case_id, "criteria.rule.upserted", "Criteria rule upserted", {"permit_type": permit_type, "rule_id": rule_id, "mode": "updated" if replaced else "added"})
    return saved


def delete_criteria_rule(db: Any, case_id: str, permit_type: str, rule_id: str, editor: str = "admin") -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    rule_id = str(rule_id or "").strip()
    if not rule_id:
        raise ValueError("criteria_rule_id_required")
    updated = copy.deepcopy(effective_criteria(db, case_id, permit_type))
    rules = updated.get("rules", [])
    if not isinstance(rules, list):
        rules = []
    next_rules = [rule for rule in rules if not (isinstance(rule, dict) and rule.get("id") == rule_id)]
    if len(next_rules) == len(rules):
        raise ValueError("criteria_rule_not_found")
    updated["rules"] = next_rules
    updated["version"] = f"{str(updated.get('version') or 'criteria')}-delete-{int(time.time())}"
    saved = save_criteria_version(db, case_id, permit_type, updated, editor=editor)
    event(db, case_id, "criteria.rule.deleted", "Criteria rule deleted", {"permit_type": permit_type, "rule_id": rule_id})
    return saved
