from __future__ import annotations
import hmac
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request


TENANTS_PATH = Path(__file__).resolve().parent / "tenants.json"
TENANTS: list[dict[str, Any]] | None = None


def _load_tenants() -> list[dict[str, Any]]:
    global TENANTS
    if TENANTS is None:
        with open(TENANTS_PATH) as f:
            TENANTS = json.load(f).get("clients", [])
    assert TENANTS is not None
    return TENANTS


def _get_client_by_key(api_key: str) -> dict[str, Any] | None:
    # compare_digest on str rejects non-ASCII, so compare bytes: a non-ASCII header must 403, not crash.
    supplied = api_key.encode("utf-8")
    for c in _load_tenants():
        if hmac.compare_digest(str(c.get("api_key") or "").encode("utf-8"), supplied):
            return c
    return None


def public_client_options() -> list[dict[str, Any]]:
    return [{"tenant_id": c["tenant_id"], "name": c["name"], "api_key": c["api_key"],
             "permit_types": c["permit_types"], "criteria_sets": c["criteria_sets"],
             "criteria_version_suffix": c["criteria_version_suffix"]} for c in _load_tenants()]


def require_api_client(request: Request) -> dict[str, Any]:
    key = str(request.headers.get("X-Lupademo-Api-Key") or request.headers.get("X-API-Key") or "").strip()
    if not key:
        raise HTTPException(status_code=401, detail="missing_api_key")
    client = _get_client_by_key(key)
    if not client:
        raise HTTPException(status_code=403, detail="invalid_api_key")
    return client


def validate_client_payload(client: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    tenant_id = client["tenant_id"]
    if out.get("tenant_id") and out["tenant_id"] != tenant_id:
        raise HTTPException(status_code=403, detail="API key tenant does not match request tenant_id")
    out["tenant_id"] = tenant_id
    permit_type = str(out.get("permit_type") or "").strip()
    if permit_type and permit_type not in client["permit_types"]:
        raise HTTPException(status_code=403, detail=f"Tenant {tenant_id} cannot use permit_type {permit_type}")
    criteria_sets = client["criteria_sets"]
    criteria_set_id = str(out.get("criteria_set_id") or "").strip()
    if criteria_set_id and criteria_set_id not in criteria_sets:
        raise HTTPException(status_code=403, detail=f"Tenant {tenant_id} cannot use criteria_set_id {criteria_set_id}")
    if not criteria_set_id and criteria_sets:
        out["criteria_set_id"] = criteria_sets[0]
    scopes = out.get("allowed_corpus_scopes") or out.get("requested_scopes") or []
    if isinstance(scopes, str):
        scopes = [scopes]
    if any(s in {"example_case_benchmark", "authority_example"} for s in scopes if s):
        raise HTTPException(status_code=403, detail="benchmark_scope_forbidden")
    return out


def client_can_access_criteria(client: dict[str, Any], criteria_set_id: str) -> bool:
    return criteria_set_id in client["criteria_sets"]


def annotate_api_response(client: dict[str, Any], payload: Any) -> dict[str, Any]:
    out: dict[str, Any] = dict(payload) if isinstance(payload, dict) else asdict(payload)
    out["tenant_id"] = client["tenant_id"]
    out["api_client_name"] = client["name"]
    suffix = client["criteria_version_suffix"]
    out["criteria_version"] = f"{out['criteria_version']}:{suffix}" if out.get("criteria_version") else suffix
    if "model_audit" not in out:
        suggestions = out.get("suggestions") or []
        issues = out.get("issues") or []
        extracted = out.get("extracted_fields") or []
        rows = suggestions or issues or extracted
        evidence_count = sum(len(r.get("evidence_refs") or []) for r in rows if isinstance(r, dict))
        fallback_used = any(r.get("fallback_used") for r in rows if isinstance(r, dict))
        agent = next((r.get("agent") for r in rows if isinstance(r, dict) and r.get("agent")), "")
        out["model_audit"] = {
            "agent": agent or ("criteria_registry" if "rules" in out else "lupademo_api"),
            "fallback_used": fallback_used,
            "duration_ms": 0,
            "evidence_count": evidence_count,
            "vector_store_ids": list(out.get("vector_store_ids") or []),
        }
    return out
