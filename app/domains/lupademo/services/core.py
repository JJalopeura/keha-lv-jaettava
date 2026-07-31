"""Application orchestration: case setup, application lifecycle, common-data sync.
Depends on domain.py + db_helpers.py. Re-exports both for backward compatibility."""

import time
from typing import Any

from .db_helpers import *  # noqa: F401,F403
from .domain import *  # noqa: F401,F403

# Ensure underscore-prefixed helpers survive the re-export chain.
from .db_helpers import _dict, _extraction_queue_item, _is_presenter_case, _list, _plain, _real_llm_enabled, _real_llm_enabled_for_case, _real_llm_enabled_from_env

from .db_helpers import artifact_json, event, save_artifact
from .domain import COMMON_FIELDS, DEFAULT_COMMON_DATA, PERMIT_SCHEMAS, load_form_schema


def ensure_demo_case(db: Any) -> str:
    cases = db.list_cases()
    if cases:
        return cases[0]["id"]
    case_id = db.create_case("Lupademo: Koivutien pientalohanke", language="fi")
    db.set_case_status(case_id, "application_draft")
    ensure_seed_state(db, case_id)
    db.set_app_setting("lupademo_active_case_id", case_id)
    return case_id


def ensure_seed_state(db: Any, case_id: str) -> None:
    if not db.get_artifact(case_id, "lv_common_data", "main"):
        save_artifact(db, case_id, "lv_common_data", "main", {"data": DEFAULT_COMMON_DATA.copy(), "version": 1})
    if not db.get_artifact(case_id, "common_data_sync_state", "main"):
        save_artifact(db, case_id, "common_data_sync_state", "main", {"base": DEFAULT_COMMON_DATA.copy(), "changes": []})
    project = artifact_json(db, case_id, "lv_project", "main", {})
    if not project:
        save_artifact(
            db,
            case_id,
            "lv_project",
            "main",
            {
                "project_id": "lv-demo-001",
                "name": DEFAULT_COMMON_DATA["project_name"],
                "statuses": {
                    "building_residential": "draft",
                    "environmental_industrial": "not_started",
                    "event_public": "not_started",
                },
            },
        )
    else:
        statuses = project.setdefault("statuses", {})
        changed = False
        for permit_type in PERMIT_SCHEMAS:
            if permit_type not in statuses:
                statuses[permit_type] = "not_started"
                changed = True
        if changed:
            save_artifact(db, case_id, "lv_project", "main", project)
    for permit_type in PERMIT_SCHEMAS:
        ensure_application(db, case_id, permit_type)


def application_id_for(permit_type_or_id: str) -> str:
    if permit_type_or_id in PERMIT_SCHEMAS:
        return permit_type_or_id
    if permit_type_or_id in {"building", "rakennuslupa"}:
        return "building_residential"
    if permit_type_or_id in {"environmental", "ymparistolupa"}:
        return "environmental_industrial"
    if permit_type_or_id in {"event", "event_public", "tapahtumalupa"}:
        return "event_public"
    return permit_type_or_id


def ensure_application(db: Any, case_id: str, permit_type: str) -> dict[str, Any]:
    permit_type = application_id_for(permit_type)
    schema = load_form_schema(permit_type)
    app = artifact_json(db, case_id, "permit_application", permit_type, {})
    if app:
        return app
    common = artifact_json(db, case_id, "lv_common_data", "main", {"data": DEFAULT_COMMON_DATA.copy()})["data"]
    form_data = {f["id"]: common.get(f["id"], "") for f in schema.get("fields", [])}
    # Skip hardcoded demo defaults when real LLM is active and not in presenter mode.
    project = artifact_json(db, case_id, "lv_project", "main", {})
    presenter_mode = bool(project.get("presenter_mode"))
    if not presenter_mode and _real_llm_enabled_from_env():
        pass  # no hardcoded defaults — real AI fills suggestions
    elif permit_type == "building_residential":
        form_data.update({"intended_use": "Asuinrakennus", "floor_area_m2": "145", "distance_to_boundary_m": "4"})
    elif permit_type == "environmental_industrial":
        form_data.update({"activity_name": "Vihreän vedyn tuotantolaitos", "competent_authority": "Aluehallintovirasto"})
    elif permit_type == "event_public":
        form_data.update({"event_name": "Koivutien korttelijuhla", "expected_attendees": "250", "event_location": common.get("address", "")})
    app = {
        "application_id": permit_type,
        "permit_type": permit_type,
        "schema_id": schema.get("schema_id"),
        "status": "draft",
        "form_data": form_data,
        "field_sources": {k: "lv_common_data" for k in COMMON_FIELDS if k in form_data},
        "versions": [],
        "updated_at": time.time(),
    }
    save_artifact(db, case_id, "permit_application", permit_type, app)
    return app


def update_lv_common_data(db: Any, case_id: str, updates: dict[str, str]) -> dict[str, Any]:
    ensure_seed_state(db, case_id)
    current = artifact_json(db, case_id, "lv_common_data", "main", {"data": DEFAULT_COMMON_DATA.copy(), "version": 1})
    current["data"].update({k: v for k, v in updates.items() if k in COMMON_FIELDS})
    # pi-lens-ignore: unchecked-throwing-call-python
    current["version"] = int(current.get("version", 1)) + 1
    save_artifact(db, case_id, "lv_common_data", "main", current)
    detect_common_data_changes(db, case_id)
    event(db, case_id, "lv.common_data.updated", "LV common data updated", {"fields": list(updates)})
    return current


def update_application_data(db: Any, case_id: str, permit_type: str, updates: dict[str, str]) -> dict[str, Any]:
    app = ensure_application(db, case_id, permit_type)
    before = dict(app.get("form_data") or {})
    app.setdefault("form_data", {}).update(updates)
    app.setdefault("versions", []).append({"ts": time.time(), "source": "user", "before": before, "after": dict(app["form_data"])})
    app["updated_at"] = time.time()
    save_artifact(db, case_id, "permit_application", app["permit_type"], app)
    detect_common_data_changes(db, case_id)
    event(db, case_id, "application.saved", "Application fields saved", {"application_id": app["permit_type"], "fields": list(updates)})
    return app


def detect_common_data_changes(db: Any, case_id: str) -> dict[str, Any]:
    common = artifact_json(db, case_id, "lv_common_data", "main", {"data": DEFAULT_COMMON_DATA.copy()})["data"]
    sync = artifact_json(db, case_id, "common_data_sync_state", "main", {"base": DEFAULT_COMMON_DATA.copy(), "changes": []})
    base = sync.get("base") or DEFAULT_COMMON_DATA.copy()
    changes: list[dict[str, Any]] = []
    for schema_field in COMMON_FIELDS:
        lv_value = common.get(schema_field, "")
        base_value = base.get(schema_field, "")
        app_entries = []
        for permit_type in PERMIT_SCHEMAS:
            app = artifact_json(db, case_id, "permit_application", permit_type, {})
            if not app:
                continue
            form_data = app.get("form_data", {})
            app_entries.append({"application_id": permit_type, "value": form_data.get(schema_field, base_value)})
        if not app_entries:
            continue
        app_values = [entry["value"] for entry in app_entries]
        changed_entries = [entry for entry in app_entries if entry["value"] != base_value]
        app_changed = bool(changed_entries)
        lv_changed = lv_value != base_value
        if lv_changed and app_changed and any(v != lv_value for v in app_values):
            direction = "conflict"
        elif lv_changed:
            direction = "lv_to_app"
        elif app_changed:
            direction = "app_to_lv"
        else:
            continue
        source_entry = changed_entries[0] if changed_entries else (app_entries[0] if app_entries else {"application_id": "", "value": ""})
        changes.append(
            {
                "id": f"chg_{schema_field}_{source_entry.get('application_id') or 'lv'}",
                "field_id": schema_field,
                "direction": direction,
                "lv_value": lv_value,
                "app_value": source_entry.get("value", ""),
                "app_values": app_values,
                "app_entries": app_entries,
                "base_value": base_value,
                "source_system": "kevytasiointi" if direction in {"app_to_lv", "conflict"} else "lv",
                "source_application_id": source_entry.get("application_id", ""),
                "status": "pending",
                "created_at": time.time(),
                "resolved_at": None,
                "resolution": None,
            }
        )
    sync["changes"] = changes
    save_artifact(db, case_id, "common_data_sync_state", "main", sync)
    change_log = artifact_json(db, case_id, "common_data_change_log", "main", {"changes": []})
    previous = {item.get("id"): item for item in change_log.get("changes", []) if item.get("id")}
    merged = []
    for change in changes:
        old = previous.get(change["id"], {})
        if old.get("status") == "resolved":
            continue
        merged.append({**old, **change, "created_at": old.get("created_at") or change.get("created_at")})
    change_log["changes"] = merged
    save_artifact(db, case_id, "common_data_change_log", "main", change_log)
    return sync


def accept_common_data_sync(
    db: Any,
    case_id: str,
    field_id: str,
    direction: str,
    *,
    source_application_id: str = "",
    resolution: str = "",
    manual_value: str = "",
) -> dict[str, Any]:
    common = artifact_json(db, case_id, "lv_common_data", "main", {"data": DEFAULT_COMMON_DATA.copy(), "version": 1})
    value_for_base = common["data"].get(field_id, "")
    if direction == "lv_to_app" or (direction == "conflict" and resolution == "lv_wins"):
        for permit_type in PERMIT_SCHEMAS:
            app = ensure_application(db, case_id, permit_type)
            if field_id in COMMON_FIELDS or field_id in app.get("form_data", {}):
                app["form_data"][field_id] = common["data"].get(field_id, "")
                app["updated_at"] = time.time()
                save_artifact(db, case_id, "permit_application", permit_type, app)
    elif direction == "app_to_lv" or (direction == "conflict" and resolution == "app_wins"):
        source_id = source_application_id or "building_residential"
        app = ensure_application(db, case_id, source_id)
        common["data"][field_id] = app.get("form_data", {}).get(field_id, common["data"].get(field_id, ""))
        # pi-lens-ignore: unchecked-throwing-call-python
        common["version"] = int(common.get("version", 1)) + 1
        save_artifact(db, case_id, "lv_common_data", "main", common)
        value_for_base = common["data"].get(field_id, "")
        if direction == "conflict":
            for permit_type in PERMIT_SCHEMAS:
                app_item = ensure_application(db, case_id, permit_type)
                if field_id in COMMON_FIELDS or field_id in app_item.get("form_data", {}):
                    app_item["form_data"][field_id] = value_for_base
                    app_item["updated_at"] = time.time()
                    save_artifact(db, case_id, "permit_application", permit_type, app_item)
    elif direction == "conflict" and resolution == "manual":
        common["data"][field_id] = manual_value
        # pi-lens-ignore: unchecked-throwing-call-python
        common["version"] = int(common.get("version", 1)) + 1
        save_artifact(db, case_id, "lv_common_data", "main", common)
        value_for_base = manual_value
    elif direction == "conflict" and resolution == "keep_separate":
        value_for_base = common["data"].get(field_id, "")
    sync = artifact_json(db, case_id, "common_data_sync_state", "main", {"base": DEFAULT_COMMON_DATA.copy(), "changes": []})
    sync["base"][field_id] = value_for_base
    save_artifact(db, case_id, "common_data_sync_state", "main", sync)
    change_log = artifact_json(db, case_id, "common_data_change_log", "main", {"changes": []})
    for change in change_log.get("changes", []):
        if change.get("field_id") == field_id and change.get("status") == "pending":
            change["status"] = "resolved"
            change["resolved_at"] = time.time()
            change["resolution"] = resolution or direction
    save_artifact(db, case_id, "common_data_change_log", "main", change_log)
    event(db, case_id, "common_data.sync.accepted", "Common data sync accepted", {"field_id": field_id, "direction": direction, "source_application_id": source_application_id, "resolution": resolution})
    return detect_common_data_changes(db, case_id)
