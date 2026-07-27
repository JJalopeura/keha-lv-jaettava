from __future__ import annotations

import mimetypes
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Tuple

import httpx


def guess_mime_type(filename: str) -> str:
    mt, _ = mimetypes.guess_type(filename)
    return mt or "application/octet-stream"


@dataclass
class AzureOpenAIClient:
    host: str
    api_version: str
    api_key: str
    model: str
    timeout_s: float = 180.0

    def _headers(self) -> Dict[str, str]:
        return {"api-key": self.api_key}

    def _url(self, path: str) -> str:
        return f"{self.host}{path}"

    def request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Tuple[str, bytes, str]]] = None,
        include_api_version: bool = True,
        query_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = self._url(path)
        params = dict(query_params or {})
        if include_api_version:
            params["api-version"] = self.api_version

        with httpx.Client(timeout=self.timeout_s) as client:
            if files is not None:
                resp = client.request(method, url, params=params, headers=self._headers(), files=files)
            else:
                headers = {"Content-Type": "application/json", **self._headers()}
                resp = client.request(method, url, params=params, headers=headers, json=json_body)

        if resp.status_code >= 400:
            try:
                data = resp.json()
                err = data.get("error", {})
                msg = err.get("message") or data
            except Exception:
                msg = resp.text
            raise RuntimeError(f"{method} {path} -> HTTP {resp.status_code}: {msg}")

        if not resp.text.strip():
            return {}
        return resp.json()

    def create_realtime_client_secret(
        self,
        *,
        session: Dict[str, Any],
        expires_after_s: int = 600,
        path: str = "/openai/v1/realtime/client_secrets",
    ) -> Dict[str, Any]:
        body = {
            "expires_after": {
                "anchor": "created_at",
                "seconds": max(int(expires_after_s), 10),
            },
            "session": session,
        }
        return self.request_json(
            "POST",
            path,
            json_body=body,
            include_api_version=False,
        )

    # -------- Vector stores --------
    def create_vector_store(self, name: Optional[str] = None) -> str:
        body: Dict[str, Any] = {}
        if name:
            body["name"] = name
        data = self.request_json("POST", "/openai/vector_stores", json_body=body)
        return data["id"]

    def attach_file_to_vector_store(self, vector_store_id: str, file_id: str) -> str:
        data = self.request_json(
            "POST",
            f"/openai/vector_stores/{vector_store_id}/files",
            json_body={"file_id": file_id},
        )
        return data["id"]

    def get_vector_store_file(self, vector_store_id: str, vector_store_file_id: str) -> Dict[str, Any]:
        return self.request_json("GET", f"/openai/vector_stores/{vector_store_id}/files/{vector_store_file_id}")

    def wait_vector_store_file_completed(
        self,
        vector_store_id: str,
        vector_store_file_id: str,
        *,
        poll_sleep_s: float = 2.0,
        max_tries: int = 60,
    ) -> None:
        last_status = "unknown"
        for _ in range(max_tries):
            info = self.get_vector_store_file(vector_store_id, vector_store_file_id)
            last_status = info.get("status", "unknown")
            if last_status == "completed":
                return
            if last_status in {"failed", "cancelled"}:
                raise RuntimeError(f"Vector store ingestion {last_status}: {info}")
            time.sleep(poll_sleep_s)
        raise TimeoutError(f"Timed out waiting for ingestion. last_status={last_status}")

    # -------- Uploads --------
    def start_upload(self, filename: str, size_bytes: int, mime_type: str, purpose: str = "assistants") -> str:
        data = self.request_json(
            "POST",
            "/openai/uploads",
            json_body={
                "purpose": purpose,
                "filename": filename,
                "bytes": int(size_bytes),
                "mime_type": mime_type,
            },
        )
        return data["id"]

    def upload_part_bytes(self, upload_id: str, filename: str, chunk: bytes, mime_type: str) -> str:
        data = self.request_json(
            "POST",
            f"/openai/uploads/{upload_id}/parts",
            files={"data": (filename, chunk, mime_type)},
        )
        return data["id"]

    def complete_upload(self, upload_id: str, part_ids: List[str]) -> str:
        data = self.request_json(
            "POST",
            f"/openai/uploads/{upload_id}/complete",
            json_body={"part_ids": part_ids},
        )
        if isinstance(data, dict):
            if isinstance(data.get("file"), dict) and data["file"].get("id"):
                return data["file"]["id"]
            if data.get("file_id"):
                return data["file_id"]
            if data.get("object") == "file" and data.get("id"):
                return data["id"]
        raise RuntimeError(f"Could not extract file id from complete response: {data}")

    def upload_file_from_path(self, local_path: str, *, chunk_size: int = 8 * 1024 * 1024) -> tuple[str, str]:
        filename = os.path.basename(local_path)
        mime_type = guess_mime_type(filename)
        size_bytes = os.path.getsize(local_path)

        upload_id = self.start_upload(filename=filename, size_bytes=size_bytes, mime_type=mime_type, purpose="assistants")

        part_ids: List[str] = []
        with open(local_path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                part_id = self.upload_part_bytes(upload_id, filename, chunk, mime_type)
                part_ids.append(part_id)

        file_id = self.complete_upload(upload_id, part_ids)
        return upload_id, file_id

    # -------- Responses --------
    def _responses_payload(
        self,
        *,
        input_text: Optional[str] = None,
        input_items: Optional[List[Dict[str, Any]]] = None,
        vector_store_ids: Optional[List[str]] = None,
        response_format: Optional[str] = None,
        max_output_tokens: Optional[int] = None,
        previous_response_id: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        text_verbosity: Optional[str] = None,
        model: Optional[str] = None,
        stream: bool = False,
    ) -> Dict[str, Any]:
        if input_text is None and input_items is None:
            raise ValueError("responses requires input_text or input_items")

        payload: Dict[str, Any] = {"model": model or self.model}
        if input_items is not None:
            payload["input"] = input_items
        else:
            payload["input"] = input_text
        if vector_store_ids:
            payload["tools"] = [{"type": "file_search", "vector_store_ids": vector_store_ids}]
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
        if stream:
            payload["stream"] = True
        return payload

    def responses(
        self,
        *,
        input_text: Optional[str] = None,
        input_items: Optional[List[Dict[str, Any]]] = None,
        vector_store_ids: Optional[List[str]] = None,
        response_format: Optional[str] = None,  # None | "json_object"
        max_output_tokens: Optional[int] = None,
        previous_response_id: Optional[str] = None,
        reasoning_effort: Optional[str] = None,  # low|medium|high
        text_verbosity: Optional[str] = None,    # low|medium|high
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = self._responses_payload(
            input_text=input_text,
            input_items=input_items,
            vector_store_ids=vector_store_ids,
            response_format=response_format,
            max_output_tokens=max_output_tokens,
            previous_response_id=previous_response_id,
            reasoning_effort=reasoning_effort,
            text_verbosity=text_verbosity,
            model=model,
        )
        return self.request_json("POST", "/openai/responses", json_body=payload)

    def responses_stream(
        self,
        *,
        input_text: Optional[str] = None,
        input_items: Optional[List[Dict[str, Any]]] = None,
        vector_store_ids: Optional[List[str]] = None,
        response_format: Optional[str] = None,
        max_output_tokens: Optional[int] = None,
        previous_response_id: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        text_verbosity: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Iterator[dict[str, Any]]:
        payload = self._responses_payload(
            input_text=input_text,
            input_items=input_items,
            vector_store_ids=vector_store_ids,
            response_format=response_format,
            max_output_tokens=max_output_tokens,
            previous_response_id=previous_response_id,
            reasoning_effort=reasoning_effort,
            text_verbosity=text_verbosity,
            model=model,
            stream=True,
        )
        url = self._url("/openai/responses")
        params = {"api-version": self.api_version}
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream", **self._headers()}

        with httpx.Client(timeout=self.timeout_s) as client:
            with client.stream("POST", url, params=params, headers=headers, json=payload) as resp:
                if resp.status_code >= 400:
                    try:
                        data = resp.json()
                        err = data.get("error", {})
                        msg = err.get("message") or data
                    except Exception:
                        msg = resp.text
                    raise RuntimeError(f"POST /openai/responses -> HTTP {resp.status_code}: {msg}")
                yield from self._iter_sse_events(resp)

    @staticmethod
    def extract_output_text(resp: Dict[str, Any]) -> Optional[str]:
        try:
            for o in resp.get("output", []):
                if o.get("type") == "message":
                    for c in o.get("content", []):
                        if c.get("type") == "output_text":
                            return c.get("text")
        except Exception:
            return None
        return None

    @staticmethod
    def _iter_sse_events(resp: httpx.Response) -> Iterator[dict[str, Any]]:
        event_name: Optional[str] = None
        data_lines: List[str] = []

        def flush() -> Optional[dict[str, Any]]:
            nonlocal event_name, data_lines
            if event_name is None and not data_lines:
                return None
            raw = "\n".join(data_lines)
            data: Any = raw
            if raw.strip():
                try:
                    data = json.loads(raw)
                except Exception:
                    data = raw
            event = {"event": event_name or "message", "data": data}
            event_name = None
            data_lines = []
            return event

        for raw_line in resp.iter_lines():
            if raw_line is None:
                continue
            line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else str(raw_line)
            line = line.rstrip("\r")
            if line == "":
                event = flush()
                if event is not None:
                    yield event
                continue
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip()
                continue
            if line.startswith("data:"):
                data_lines.append(line[len("data:") :].lstrip())
                continue
            if line.startswith(":"):
                continue
        event = flush()
        if event is not None:
            yield event
