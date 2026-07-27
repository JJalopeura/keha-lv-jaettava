from __future__ import annotations

import json
import time
import threading
import os
from typing import Any, Dict, Iterator, List, Optional

from .config import Settings
from .llm_factory import LLMClient
from .db import DB
from .llm_profiles import FAST_DEFAULTS

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
    client: LLMClient,
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
        db=db,
        agent=agent,
        reasoning_effort=reasoning_effort,
        text_verbosity=text_verbosity,
        max_output_tokens=max_output_tokens,
    )
    payload = _build_responses_payload(
        client,
        prompt,
        vector_store_ids_list=vector_store_ids_list,
        response_format="json_object",
        max_output_tokens=max_output_tokens,
        previous_response_id=None,
        reasoning_effort=reasoning_effort,
        text_verbosity=text_verbosity,
    )
    start = time.time()
    resp: Optional[Dict[str, Any]] = None
    err: Optional[str] = None
    log_id: Optional[str] = None
    if db is not None:
        try:
            log_id = db.add_ai_log(
                case_id=case_id,
                task_id=task_id,
                agent=agent,
                model=payload.get("model"),
                request_json=json.dumps(payload, ensure_ascii=False),
                response_json=None,
                duration_ms=None,
                error=None,
            )
        except Exception:
            log_id = None
    try:
        _rate_limit()
        resp = client.responses(
            input_text=prompt,
            vector_store_ids=vector_store_ids_list,
            response_format="json_object",
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
            text_verbosity=text_verbosity,
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
                    duration_ms=duration_ms,
                    error=err,
                )
            except Exception:
                pass


def extract_response_id(resp: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(resp, dict):
        return None
    for key in ("id", "response_id"):
        value = resp.get(key)
        if value:
            return str(value)
    nested = resp.get("response")
    if isinstance(nested, dict):
        for key in ("id", "response_id"):
            value = nested.get(key)
            if value:
                return str(value)
    return None


def call_llm_text(
    client: LLMClient,
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
    previous_response_id: Optional[str] = None,
    capture_response: bool = False,
) -> str | tuple[str, Dict[str, Any]]:
    reasoning_effort, text_verbosity, max_output_tokens = _apply_llm_overrides(
        db=db,
        agent=agent,
        reasoning_effort=reasoning_effort,
        text_verbosity=text_verbosity,
        max_output_tokens=max_output_tokens,
    )
    payload = _build_responses_payload(
        client,
        prompt,
        vector_store_ids_list=vector_store_ids_list,
        response_format=None,
        max_output_tokens=max_output_tokens,
        previous_response_id=previous_response_id,
        reasoning_effort=reasoning_effort,
        text_verbosity=text_verbosity,
    )
    start = time.time()
    resp: Optional[Dict[str, Any]] = None
    err: Optional[str] = None
    log_id: Optional[str] = None
    if db is not None:
        try:
            log_id = db.add_ai_log(
                case_id=case_id,
                task_id=task_id,
                agent=agent,
                model=payload.get("model"),
                request_json=json.dumps(payload, ensure_ascii=False),
                response_json=None,
                duration_ms=None,
                error=None,
            )
        except Exception:
            log_id = None
    try:
        _rate_limit()
        resp = client.responses(
            input_text=prompt,
            vector_store_ids=vector_store_ids_list,
            response_format=None,
            max_output_tokens=max_output_tokens,
            previous_response_id=previous_response_id,
            reasoning_effort=reasoning_effort,
            text_verbosity=text_verbosity,
        )
        text = client.extract_output_text(resp)
        if text is None:
            err = f"No output_text in response: {resp}"
            raise RuntimeError(err)
        if capture_response:
            return text, resp
        return text
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
                    duration_ms=duration_ms,
                    error=err,
                )
            except Exception:
                pass


def call_llm_text_stream_events(
    client: LLMClient,
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
    previous_response_id: Optional[str] = None,
) -> Iterator[dict[str, Any]]:
    reasoning_effort, text_verbosity, max_output_tokens = _apply_llm_overrides(
        db=db,
        agent=agent,
        reasoning_effort=reasoning_effort,
        text_verbosity=text_verbosity,
        max_output_tokens=max_output_tokens,
    )
    payload = _build_responses_payload(
        client,
        prompt,
        vector_store_ids_list=vector_store_ids_list,
        response_format=None,
        max_output_tokens=max_output_tokens,
        previous_response_id=previous_response_id,
        reasoning_effort=reasoning_effort,
        text_verbosity=text_verbosity,
    )
    start = time.time()
    log_id: Optional[str] = None
    err: Optional[str] = None
    full_text_parts: list[str] = []
    final_resp: Optional[Dict[str, Any]] = None
    response_id: Optional[str] = previous_response_id
    if db is not None:
        try:
            log_id = db.add_ai_log(
                case_id=case_id,
                task_id=task_id,
                agent=agent,
                model=payload.get("model"),
                request_json=json.dumps(payload, ensure_ascii=False),
                response_json=None,
                duration_ms=None,
                error=None,
            )
        except Exception:
            log_id = None
    try:
        _rate_limit()
        for raw_event in client.responses_stream(
            input_text=prompt,
            vector_store_ids=vector_store_ids_list,
            response_format=None,
            max_output_tokens=max_output_tokens,
            previous_response_id=previous_response_id,
            reasoning_effort=reasoning_effort,
            text_verbosity=text_verbosity,
        ):
            event_type = str((raw_event or {}).get("event") or (raw_event or {}).get("type") or "").strip()
            data = (raw_event or {}).get("data")
            if event_type == "response.created":
                if isinstance(data, dict):
                    response_id = str(data.get("id") or response_id or "").strip() or response_id
                continue
            if event_type == "response.output_text.delta":
                chunk = ""
                if isinstance(data, dict):
                    chunk = str(data.get("delta") or data.get("text") or "")
                    if data.get("id"):
                        response_id = str(data.get("id") or response_id or "").strip() or response_id
                elif data is not None:
                    chunk = str(data)
                if chunk:
                    full_text_parts.append(chunk)
                    yield {"type": "delta", "delta": chunk, "response_id": response_id}
                continue
            if event_type in {"response.output_text.done", "response.completed"}:
                if isinstance(data, dict):
                    response_id = str(data.get("id") or response_id or "").strip() or response_id
                    if event_type == "response.completed":
                        final_resp = dict(data)
                continue
            if event_type in {"error", "response.error"}:
                if isinstance(data, dict):
                    err = str(data.get("message") or data.get("error") or data)
                else:
                    err = str(data or "LLM stream failed")
                raise RuntimeError(err)
            # Preserve forward compatibility by ignoring unrecognized events.
        full_text = "".join(full_text_parts)
        if final_resp is None:
            final_resp = {"id": response_id, "output_text": full_text}
        elif "output_text" not in final_resp:
            final_resp["output_text"] = full_text
        yield {"type": "done", "text": full_text, "response_id": response_id, "response": final_resp}
    except Exception as e:
        if err is None:
            err = str(e)
        yield {"type": "error", "error": err, "text": "".join(full_text_parts), "response_id": response_id}
    finally:
        if db is not None and log_id:
            duration_ms = int((time.time() - start) * 1000)
            response_json = None
            if final_resp is not None:
                response_json = json.dumps(final_resp, ensure_ascii=False)
            elif full_text_parts or response_id:
                response_json = json.dumps(
                    {
                        "id": response_id,
                        "output_text": "".join(full_text_parts),
                    },
                    ensure_ascii=False,
                )
            try:
                db.update_ai_log(
                    log_id,
                    response_json=response_json,
                    duration_ms=duration_ms,
                    error=err,
                )
            except Exception:
                pass


def _build_responses_payload(
    client: LLMClient,
    prompt: str,
    *,
    vector_store_ids_list: Optional[List[str]],
    response_format: Optional[str],
    max_output_tokens: Optional[int],
    previous_response_id: Optional[str],
    reasoning_effort: Optional[str],
    text_verbosity: Optional[str],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"model": client.model, "input": prompt}
    if vector_store_ids_list:
        payload["tools"] = [{"type": "file_search", "vector_store_ids": vector_store_ids_list}]
        payload["tool_choice"] = "auto"
    if response_format == "json_object":
        payload.setdefault("text", {})
        payload["text"]["format"] = {"type": "json_object"}
    if text_verbosity:
        payload.setdefault("text", {})
        payload["text"]["verbosity"] = text_verbosity
    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort}
    if max_output_tokens is not None:
        payload["max_output_tokens"] = int(max_output_tokens)
    if previous_response_id is not None:
        payload["previous_response_id"] = previous_response_id
    return payload


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
        reasoning_effort = FAST_DEFAULTS["reasoning_effort"]
        text_verbosity = FAST_DEFAULTS["text_verbosity"]
        cap = FAST_DEFAULTS.get("max_output_tokens")
        if cap is not None:
            if max_output_tokens is None:
                max_output_tokens = int(cap)
            else:
                try:
                    max_output_tokens = min(int(max_output_tokens), int(cap))
                except Exception:
                    max_output_tokens = int(cap)

    # Optional per-agent override fields (future-proof)
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
