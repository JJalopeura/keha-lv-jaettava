from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request


TENANTS_PATH = Path(__file__).resolve().parent / "tenants.json"


@dataclass(frozen=True)
class ApiClient:
    tenant_id: str
    name: str
    api_key: str
    permit_types: tuple[str, ...]
    criteria_sets: tuple[str, ...]
    criteria_version_suffix: str


def load_api_clients() -> list[ApiClient]:
    payload = json.loads(TENANTS_PATH.read_text(encoding="utf-8"))
    clients: list[ApiClient] = []
    for item in payload.get("clients", []):
        clients.append(
            ApiClient(
                tenant_id=str(item.get("tenant_id") or ""),
                name=str(item.get("name") or item.get("tenant_id") or ""),
                api_key=str(item.get("api_key") or ""),
                permit_types=tuple(str(x) for x in item.get("permit_types", [])),
                criteria_sets=tuple(str(x) for x in item.get("criteria_sets", [])),
                criteria_version_suffix=str(item.get("criteria_version_suffix") or item.get("tenant_id") or ""),
            )
        )
    return [client for client in clients if client.tenant_id and client.api_key]


def public_client_options() -> list[dict[str, Any]]:
    return [
        {
            "tenant_id": client.tenant_id,
            "name": client.name,
            "api_key": client.api_key,
            "permit_types": list(client.permit_types),
            "criteria_sets": list(client.criteria_sets),
            "criteria_version_suffix": client.criteria_version_suffix,
        }
        for client in load_api_clients()
    ]


def require_api_client(request: Request) -> ApiClient:
    key = str(request.headers.get("X-Lupademo-Api-Key") or request.headers.get("X-API-Key") or "").strip()
    if not key:
        raise HTTPException(status_code=401, detail="missing_api_key")
    for client in load_api_clients():
        if client.api_key == key:
            return client
    raise HTTPException(status_code=403, detail="invalid_api_key")


def validate_client_payload(client: ApiClient, payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    requested_tenant = str(out.get("tenant_id") or "").strip()
    if requested_tenant and requested_tenant != client.tenant_id:
        raise HTTPException(status_code=403, detail="API key tenant does not match request tenant_id")
    out["tenant_id"] = client.tenant_id

    permit_type = str(out.get("permit_type") or "").strip()
    if permit_type and permit_type not in client.permit_types:
        raise HTTPException(status_code=403, detail=f"Tenant {client.tenant_id} cannot use permit_type {permit_type}")

    criteria_set_id = str(out.get("criteria_set_id") or "").strip()
    if criteria_set_id and criteria_set_id not in client.criteria_sets:
        raise HTTPException(status_code=403, detail=f"Tenant {client.tenant_id} cannot use criteria_set_id {criteria_set_id}")
    if not criteria_set_id and client.criteria_sets:
        out["criteria_set_id"] = client.criteria_sets[0]

    forbidden_scopes = {"example_case_benchmark", "authority_example"}
    requested_scopes = out.get("allowed_corpus_scopes") or out.get("requested_scopes") or []
    if isinstance(requested_scopes, str):
        requested_scopes = [requested_scopes]
    if any(str(scope) in forbidden_scopes for scope in requested_scopes if scope):
        raise HTTPException(status_code=403, detail="benchmark_scope_forbidden")
    return out


def client_can_access_criteria(client: ApiClient, criteria_set_id: str) -> bool:
    return criteria_set_id in client.criteria_sets


def annotate_api_response(client: ApiClient, payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    out["tenant_id"] = client.tenant_id
    out["api_client_name"] = client.name
    if out.get("criteria_version"):
        out["criteria_version"] = f"{out['criteria_version']}:{client.criteria_version_suffix}"
    else:
        out["criteria_version"] = client.criteria_version_suffix
    if "model_audit" not in out:
        suggestions = out.get("suggestions") if isinstance(out.get("suggestions"), list) else []
        issues = out.get("issues") if isinstance(out.get("issues"), list) else []
        extracted = out.get("extracted_fields") if isinstance(out.get("extracted_fields"), list) else []
        rows = suggestions or issues or extracted
        evidence_count = 0
        fallback_used = False
        agent = ""
        for row in rows:
            if not isinstance(row, dict):
                continue
            refs = row.get("evidence_refs")
            if isinstance(refs, list):
                evidence_count += len(refs)
            fallback_used = fallback_used or bool(row.get("fallback_used"))
            agent = agent or str(row.get("agent") or "")
        out["model_audit"] = {
            "agent": agent or "criteria_registry" if "rules" in out else agent or "lupademo_api",
            "fallback_used": fallback_used,
            "duration_ms": 0,
            "evidence_count": evidence_count,
            "vector_store_ids": list(out.get("vector_store_ids") or []),
        }
    return out
