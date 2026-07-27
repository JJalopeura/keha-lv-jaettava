
from __future__ import annotations
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from .config import Settings
from .db import DB


from .azure_client import _build_responses_payload
_LLM_RATE_LOCK = threading.Lock()
_LLM_LAST_CALL = 0.0
_LLM_MIN_INTERVAL_S = float(os.getenv("KEHA_LV_LLM_MIN_INTERVAL_S", "0") or "0")


def _rate_limit() -> None:
    if _LLM_MIN_INTERVAL_S <= 0:
        return
    global _LLM_LAST_CALL
    with _LLM_RATE_LOCK:
        now = time.time()
        wait_s = _LLM_MIN_INTERVAL_S - (now - _LLM_LAST_CALL)
        if wait_s > 0:
            time.sleep(wait_s)
        _LLM_LAST_CALL = time.time()


def vector_store_ids(
    case_vector_store_id: Optional[str],
    settings: Settings,
    db: Optional[DB] = None,
    *,
    include_offerings: bool = False,
    include_company: bool = True,
    max_ids: int = 2,
) -> List[str]:
    ids: List[str] = []
    if case_vector_store_id:
        ids.append(case_vector_store_id)
    if db is not None and include_offerings:
        vs_id = db.get_app_setting("offerings_vector_store_id")
        enabled = str(db.get_app_setting("offerings_enabled") or "1").strip().lower() in {"1", "true", "yes", "on"}
        if vs_id and enabled:
            ids.append(vs_id)
    if include_company:
        ids.extend(settings.company_vector_store_ids)
    seen: set[str] = set()
    deduped: List[str] = []
    for vs_id in ids:
        if not vs_id or vs_id in seen:
            continue
        seen.add(vs_id)
        deduped.append(vs_id)
    if max_ids and len(deduped) > max_ids:
        return deduped[:max_ids]
    return deduped


def call_llm_json(
    client,
    prompt: str,
    *,
    db: Optional[DB] = None,
    case_id: Optional[str] = None,
    task_id: Optional[str] = None,
    agent: Optional[str] = None,
    vector_store_ids_list: Optional[List[str]] = None,
    max_output_tokens: Optional[int] = None,
    reasoning_effort: Optional[str] = None,
    text_verbosity: Optional[str] = None,
) -> Dict[str, Any]:
    reasoning_effort, text_verbosity, max_output_tokens = _apply_llm_overrides(
        db=db, agent=agent,
        reasoning_effort=reasoning_effort, text_verbosity=text_verbosity,
        max_output_tokens=max_output_tokens,
    )
    payload = _build_responses_payload(
        client.model, input_text=prompt, vector_store_ids=vector_store_ids_list,
        response_format="json_object", max_output_tokens=max_output_tokens,
        reasoning_effort=reasoning_effort, text_verbosity=text_verbosity,
    )
    start = time.time()
    resp: Optional[Dict[str, Any]] = None
    err: Optional[str] = None
    log_id: Optional[str] = None
    if db is not None:
        try:
            log_id = db.add_ai_log(
                case_id=case_id, task_id=task_id, agent=agent,
                model=payload.get("model"),
                request_json=json.dumps(payload, ensure_ascii=False),
                response_json=None, duration_ms=None, error=None,
            )
        except Exception:
            log_id = None
    try:
        _rate_limit()
        resp = client.responses(
            input_text=prompt, vector_store_ids=vector_store_ids_list,
            response_format="json_object", max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort, text_verbosity=text_verbosity,
        )
        text = client.extract_output_text(resp)
        if text is None:
            err = f"No output_text in response: {resp}"
            raise RuntimeError(err)
        try:
            return json.loads(text)
        except Exception as e:
            err = f"Failed to parse JSON output: {e}. Raw: {text[:500]}"
            raise RuntimeError(err)
    except Exception as e:
        if err is None:
            err = str(e)
        raise
    finally:
        if db is not None and log_id:
            duration_ms = int((time.time() - start) * 1000)
            try:
                db.update_ai_log(
                    log_id,
                    response_json=json.dumps(resp, ensure_ascii=False) if resp is not None else None,
                    duration_ms=duration_ms, error=err,
                )
            except Exception:
                pass


def _apply_llm_overrides(
    *,
    db: Optional[DB],
    agent: Optional[str],
    reasoning_effort: Optional[str],
    text_verbosity: Optional[str],
    max_output_tokens: Optional[int],
) -> tuple[Optional[str], Optional[str], Optional[int]]:
    if db is None or not agent:
        return reasoning_effort, text_verbosity, max_output_tokens
    try:
        global_fast = db.get_app_setting("fast_mode")
    except Exception:
        global_fast = None
    fast_enabled = str(global_fast or "").strip() in {"1", "true", "yes", "on"}

    override = None
    try:
        override = db.get_llm_override(agent)
    except Exception:
        override = None

    mode = (override or {}).get("mode") if isinstance(override, dict) else None
    mode = (mode or "auto").strip().lower()

    force_fast = mode == "fast" or (mode == "auto" and fast_enabled)
    force_normal = mode == "normal"

    if force_fast and not force_normal:
        reasoning_effort = "low"
        text_verbosity = "low"

    if isinstance(override, dict):
        if override.get("reasoning_effort"):
            reasoning_effort = override.get("reasoning_effort")
        if override.get("text_verbosity"):
            text_verbosity = override.get("text_verbosity")
        if override.get("max_output_tokens") is not None:
            try:
                max_output_tokens = int(override.get("max_output_tokens"))
            except Exception:
                pass

    # Chat model guardrails (some chat deployments only support medium values)
    if agent == "case.chat":
        if reasoning_effort == "low":
            reasoning_effort = "medium"
        if text_verbosity == "low":
            text_verbosity = "medium"

    return reasoning_effort, text_verbosity, max_output_tokens
