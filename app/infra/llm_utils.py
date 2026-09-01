
from __future__ import annotations
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from .config import Settings
from .db import DB

from .azure_client import _build_responses_payload

# Global inter-call throttle so parallel AI jobs do not slam a rate-limited
# Azure deployment. Configure with KEHA_LV_LLM_MIN_INTERVAL_S (seconds).
_llm_throttle_lock = threading.Lock()
_llm_last_call_at = 0.0


def _min_call_interval() -> float:
    try:
        return max(0.0, float(os.getenv("KEHA_LV_LLM_MIN_INTERVAL_S", "0")))
    except (TypeError, ValueError):
        return 0.0


def _throttle() -> None:
    """Enforce a minimum spacing between LLM calls across the whole process."""
    interval = _min_call_interval()
    if interval <= 0:
        return
    global _llm_last_call_at
    with _llm_throttle_lock:
        now = time.time()
        wait = (_llm_last_call_at + interval) - now
        if wait > 0:
            time.sleep(wait)
        _llm_last_call_at = time.time()


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
    # Azure OpenAI requires the prompt to contain "json" when using json_object
    # response format. Append a JSON output instruction if not already present.
    normalized_prompt = prompt if prompt else ""
    if "json" not in normalized_prompt.lower():
        normalized_prompt = f"{normalized_prompt}\n\nReturn the result as a JSON object."
    payload = _build_responses_payload(
        client.model, input_text=normalized_prompt, vector_store_ids=vector_store_ids_list,
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
        _throttle()
        for attempt in range(2):
            retrying = attempt == 1
            request_tokens = max(max_output_tokens or 0, 6000) * 2 if retrying else max_output_tokens
            request_reasoning = "low" if retrying else reasoning_effort
            resp = client.responses(
                input_text=normalized_prompt, vector_store_ids=vector_store_ids_list,
                response_format="json_object", max_output_tokens=request_tokens,
                reasoning_effort=request_reasoning, text_verbosity=text_verbosity,
            )
            text = client.extract_output_text(resp)
            if text is None:
                err = f"No output_text in response: {resp}"
                incomplete = resp.get("status") == "incomplete" and (
                    (resp.get("incomplete_details") or {}).get("reason") == "max_output_tokens"
                )
                if not retrying and incomplete:
                    continue
                raise RuntimeError(err)
            try:
                result = json.loads(text)
                err = None
                return result
            except json.JSONDecodeError as exc:
                err = f"Failed to parse JSON output: {exc}. Raw: {text[:500]}"
                if not retrying:
                    continue
                raise RuntimeError(err) from exc
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

