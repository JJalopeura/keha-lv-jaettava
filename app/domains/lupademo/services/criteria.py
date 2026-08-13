"""Permit criteria CRUD: load, save, version history, rule management.
Depends on domain.py + db_helpers.py only."""

from __future__ import annotations

import copy
import time
from typing import Any

from .core import SPEC_ROOT, application_id_for, artifact_json, event, load_json, save_artifact
from .db_helpers import _dict
from .domain import PERMIT_SCHEMAS


CRITERIA_FILES = {
    "building_residential": "vantaa_building_residential_demo_v1.json",
    "environmental_industrial": "national_environmental_industrial_demo_v1.json",
    "event_public": "vantaa_event_public_demo_v1.json",
}

# City-specific criteria variants per permit type. The default (empty string)
# is the fallback used when no city-specific set matches the inputted
# municipality. Keys are normalized (lowercased, trimmed) municipality names.
CRITERIA_BY_MUNICIPALITY: dict[str, dict[str, str]] = {
    "building_residential": {
        "vantaa": "vantaa_building_residential_demo_v1.json",
        "kerava": "kerava_building_residential_demo_v1.json",
    },
    "event_public": {
        "vantaa": "vantaa_event_public_demo_v1.json",
    },
}


SEVERITY_ORDER = {"recommendation": 0, "minor": 1, "major": 2, "blocking": 3}


def _normalize_municipality(value: Any) -> str:
    return str(value or "").strip().lower()


def list_municipalities(db: Any = None, case_id: str = "") -> list[str]:
    """Return the distinct municipalities that have city-specific criteria,
    in a stable order. Includes cities registered in CRITERIA_BY_MUNICIPALITY
    plus any city-scoped criteria sets already created in the DB (via the
    admin "add city" flow)."""
    seen: dict[str, str] = {}
    for city_map in CRITERIA_BY_MUNICIPALITY.values():
        for city, filename in city_map.items():
            criteria = load_json(SPEC_ROOT / "criteria_sets" / filename, {})
            label = str(criteria.get("municipality") or "").strip() or city.title()
            seen.setdefault(city, label)
    if db is not None and case_id:
        for item in db.list_artifacts(case_id):
            if item.get("type") != "criteria_set":
                continue
            name = str(item.get("name") or "")
            if ":" in name:
                city = name.split(":", 1)[1]
                if city:
                    seen.setdefault(city, city.title())
    return [seen[city] for city in seen]


def create_city_criteria(db: Any, case_id: str, municipality: str, editor: str = "admin") -> dict[str, Any]:
    """Create a city-scoped criteria set for every permit type, seeded from
    the default criteria. Registers the city so it appears in the admin
    dropdown. Returns the first created set."""
    city = str(municipality or "").strip()
    if not city:
        raise ValueError("municipality_required")
    result: dict[str, Any] = {}
    for permit_type in PERMIT_SCHEMAS:
        name = _criteria_name(permit_type, city)
        existing = artifact_json(db, case_id, "criteria_set", name, {})
        if existing and _normalize_municipality(existing.get("municipality")) == _normalize_municipality(city):
            continue
        criteria = copy.deepcopy(load_criteria(permit_type, city))
        if _normalize_municipality(criteria.get("municipality")) != _normalize_municipality(city):
            criteria["criteria_set_id"] = f"{_normalize_municipality(city)}_{permit_type}_custom"
        criteria["municipality"] = city
        criteria["version"] = f"{city.lower()}-{int(time.time())}"
        criteria["status"] = "active"
        criteria["updated_at"] = time.time()
        criteria["updated_by"] = editor
        save_artifact(db, case_id, "criteria_set", name, criteria)
        event(db, case_id, "criteria.city.created", "City criteria created", {"permit_type": permit_type, "municipality": city})
        if not result:
            result = criteria
    return result


def _criteria_name(permit_type: str, municipality: Any = None) -> str:
    """Storage name for a criteria set. City-scoped when a municipality is
    given, so each city keeps its own active set and version history."""
    city = _normalize_municipality(municipality)
    return f"{permit_type}:{city}" if city else permit_type


def load_criteria(permit_type: str, municipality: Any = None) -> dict[str, Any]:
    """Load the criteria file for a permit type, preferring a city-specific
    variant when the inputted municipality matches one. Falls back to the
    default criteria file when no city match is found."""
    permit_type = application_id_for(permit_type)
    filename = CRITERIA_FILES.get(permit_type, CRITERIA_FILES["building_residential"])
    city = _normalize_municipality(municipality)
    if city:
        city_file = CRITERIA_BY_MUNICIPALITY.get(permit_type, {}).get(city)
        if city_file:
            filename = city_file
    return load_json(SPEC_ROOT / "criteria_sets" / filename, {})


def effective_criteria(db: Any, case_id: str, permit_type: str, municipality: Any = None) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    name = _criteria_name(permit_type, municipality)
    art = artifact_json(db, case_id, "criteria_set", name, {})
    requested_city = _normalize_municipality(municipality)
    if art and (not requested_city or _normalize_municipality(art.get("municipality")) == requested_city):
        return art
    criteria = load_criteria(permit_type, municipality)
    criteria_city = _normalize_municipality(criteria.get("municipality"))
    if requested_city and criteria_city != requested_city:
        return criteria
    if not requested_city or criteria_city == requested_city:
        save_artifact(db, case_id, "criteria_set", name, criteria)
    return criteria


def save_criteria_version(db: Any, case_id: str, permit_type: str, criteria: dict[str, Any], editor: str = "admin", municipality: Any = None) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    name = _criteria_name(permit_type, municipality)
    current = effective_criteria(db, case_id, permit_type, municipality)
    history = artifact_json(db, case_id, "criteria_history", name, {"versions": []})
    history.setdefault("versions", []).append({"ts": time.time(), "editor": editor, "action": "replaced", "criteria": current})
    submitted_version = str(criteria.get("version") or "")
    current_version = str(current.get("version") or "")
    criteria["version"] = (
        f"{current_version or 'criteria'}-edited-{time.time_ns()}"
        if not submitted_version or submitted_version == current_version
        else submitted_version
    )
    criteria["status"] = str(criteria.get("status") or "active")
    criteria["source_files"] = criteria.get("source_files") or criteria.get("sources") or []
    criteria["updated_at"] = time.time()
    criteria["updated_by"] = editor
    save_artifact(db, case_id, "criteria_history", name, history)
    save_artifact(db, case_id, "criteria_set", name, criteria)
    event(db, case_id, "criteria.updated", "Criteria set updated", {"permit_type": permit_type, "municipality": _normalize_municipality(municipality), "version": criteria.get("version")})
    return criteria


def criteria_history(db: Any, case_id: str, permit_type: str, municipality: Any = None) -> dict[str, Any]:
    name = _criteria_name(application_id_for(permit_type), municipality)
    return artifact_json(db, case_id, "criteria_history", name, {"versions": []})


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


def restore_criteria_version(db: Any, case_id: str, permit_type: str, history_index: int, editor: str = "admin", municipality: Any = None) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    history = criteria_history(db, case_id, permit_type, municipality)
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
    saved = save_criteria_version(db, case_id, permit_type, restored, editor=editor, municipality=municipality)
    event(db, case_id, "criteria.restored", "Criteria version restored", {"permit_type": permit_type, "municipality": _normalize_municipality(municipality), "history_index": history_index, "version": saved.get("version")})
    return saved


def upsert_criteria_rule(db: Any, case_id: str, permit_type: str, rule: dict[str, Any], editor: str = "admin", municipality: Any = None) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    rule_id = str(rule.get("id") or "").strip()
    if not rule_id:
        raise ValueError("criteria_rule_id_required")
    updated = copy.deepcopy(effective_criteria(db, case_id, permit_type, municipality))
    rules = updated.setdefault("rules", [])
    if not isinstance(rules, list):
        rules = []
        updated["rules"] = rules
    clean_rule = {
        "id": rule_id,
        "severity": str(rule.get("severity") or "major"),
        "instruction": str(rule.get("instruction") or "").strip(),
        "field_id": str(rule.get("field_id") or "").strip(),
        "attachment_type": str(rule.get("attachment_type") or "").strip(),
    }
    replaced = False
    for index, existing in enumerate(rules):
        if isinstance(existing, dict) and existing.get("id") == rule_id:
            merged = {**existing, **clean_rule}
            for key in ("field_id", "attachment_type"):
                if not merged.get(key):
                    merged.pop(key, None)
            rules[index] = merged
            replaced = True
            break
    if not replaced:
        rules.append(clean_rule)
    updated["version"] = f"{str(updated.get('version') or 'criteria')}-rule-{int(time.time())}"
    saved = save_criteria_version(db, case_id, permit_type, updated, editor=editor, municipality=municipality)
    event(db, case_id, "criteria.rule.upserted", "Criteria rule upserted", {"permit_type": permit_type, "municipality": _normalize_municipality(municipality), "rule_id": rule_id, "mode": "updated" if replaced else "added"})
    return saved


def delete_criteria_rule(db: Any, case_id: str, permit_type: str, rule_id: str, editor: str = "admin", municipality: Any = None) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    rule_id = str(rule_id or "").strip()
    if not rule_id:
        raise ValueError("criteria_rule_id_required")
    updated = copy.deepcopy(effective_criteria(db, case_id, permit_type, municipality))
    rules = updated.get("rules", [])
    if not isinstance(rules, list):
        rules = []
    next_rules = [rule for rule in rules if not (isinstance(rule, dict) and rule.get("id") == rule_id)]
    if len(next_rules) == len(rules):
        raise ValueError("criteria_rule_not_found")
    updated["rules"] = next_rules
    updated["version"] = f"{str(updated.get('version') or 'criteria')}-delete-{int(time.time())}"
    saved = save_criteria_version(db, case_id, permit_type, updated, editor=editor, municipality=municipality)
    event(db, case_id, "criteria.rule.deleted", "Criteria rule deleted", {"permit_type": permit_type, "municipality": _normalize_municipality(municipality), "rule_id": rule_id})
    return saved
