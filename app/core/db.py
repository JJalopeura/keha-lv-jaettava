from __future__ import annotations
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from typing import Any, Iterator, Optional


class DB:
    """Pieni, KEHA LV:n tarvitsemia sovellustiloja varten rajattu SQLite-varasto."""

    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS cases (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, language TEXT NOT NULL,
                    status TEXT NOT NULL, case_vector_store_id TEXT, research_vector_store_id TEXT,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL, filename TEXT NOT NULL,
                    local_path TEXT NOT NULL, mime_type TEXT NOT NULL, size_bytes INTEGER NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'rfp', source_group TEXT NOT NULL DEFAULT 'rfp',
                    type_primary TEXT, type_tags TEXT, vectorize INTEGER NOT NULL DEFAULT 1,
                    azure_upload_id TEXT, azure_file_id TEXT, azure_vs_file_id TEXT,
                    status TEXT NOT NULL, error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY, case_id TEXT NOT NULL, type TEXT NOT NULL, name TEXT NOT NULL,
                    data_json TEXT, data_text TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS llm_overrides (
                    agent TEXT PRIMARY KEY, mode TEXT NOT NULL, reasoning_effort TEXT,
                    text_verbosity TEXT, max_output_tokens INTEGER, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ai_logs (
                    id TEXT PRIMARY KEY, case_id TEXT, task_id TEXT, agent TEXT, model TEXT,
                    request_json TEXT, response_json TEXT, duration_ms INTEGER, error TEXT,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_files_case_id ON files(case_id);
                CREATE INDEX IF NOT EXISTS idx_artifacts_case_updated ON artifacts(case_id, updated_at);
                CREATE INDEX IF NOT EXISTS idx_ai_logs_case_created ON ai_logs(case_id, created_at);
                """
            )

    def create_case(self, title: str, language: str = "fi") -> str:
        case_id, now = str(uuid.uuid4()), time.time()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO cases VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (case_id, title, language, "new", None, None, now, now),
            )
        return case_id

    _ALLOWED_CASE_COLUMNS = {"case_vector_store_id", "research_vector_store_id", "status"}

    def _set_case_value(self, case_id: str, field: str, value: str) -> None:
        if field not in self._ALLOWED_CASE_COLUMNS:
            raise ValueError(f"Column {field} is not allowed for dynamic update")
        with self._conn() as conn:
            conn.execute(f"UPDATE cases SET {field}=?, updated_at=? WHERE id=?", (value, time.time(), case_id))

    def set_case_vector_store(self, case_id: str, vs_id: str) -> None:
        self._set_case_value(case_id, "case_vector_store_id", vs_id)

    def set_research_vector_store(self, case_id: str, vs_id: str) -> None:
        self._set_case_value(case_id, "research_vector_store_id", vs_id)

    def set_case_status(self, case_id: str, status: str) -> None:
        self._set_case_value(case_id, "status", status)

    def list_cases(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM cases ORDER BY updated_at DESC")]

    def get_case(self, case_id: str) -> Optional[dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
        return dict(row) if row else None

    def add_file(self, case_id: str, filename: str, local_path: str, mime_type: str, size_bytes: int, *,
                 kind: str = "rfp", source_group: str = "rfp", type_primary: str = "",
                 type_tags: str = "", vectorize: int = 1) -> int:
        now = time.time()
        with self._conn() as conn:
            cursor = conn.execute(
                """INSERT INTO files
                (case_id, filename, local_path, mime_type, size_bytes, kind, source_group, type_primary,
                 type_tags, vectorize, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)""",
                (case_id, filename, local_path, mime_type, int(size_bytes), kind, source_group,
                 type_primary, type_tags, int(vectorize), now, now),
            )
            return int(cursor.lastrowid or 0)

    def update_file(self, file_row_id: int, **fields: Any) -> None:
        allowed = {
            "filename", "local_path", "mime_type", "size_bytes", "kind", "source_group",
            "type_primary", "type_tags", "vectorize", "azure_upload_id", "azure_file_id",
            "azure_vs_file_id", "status", "error",
        }
        values = {key: value for key, value in fields.items() if key in allowed}
        if not values:
            return
        values["updated_at"] = time.time()
        columns = list(values)
        with self._conn() as conn:
            conn.execute(
                f"UPDATE files SET {', '.join(f'{column}=?' for column in columns)} WHERE id=?",
                [values[column] for column in columns] + [file_row_id],
            )

    def delete_file(self, file_row_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM files WHERE id=?", (file_row_id,))

    def get_file(self, file_row_id: int) -> Optional[dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM files WHERE id=?", (file_row_id,)).fetchone()
        return dict(row) if row else None

    def upsert_artifact(self, case_id: str, type_: str, name: str, *, data_json: Optional[dict[str, Any]] = None,
                        data_text: Optional[str] = None) -> str:
        artifact_id, now = f"{case_id}:{type_}:{name}", time.time()
        payload = json.dumps(data_json, ensure_ascii=False, default=asdict) if data_json is not None else None
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET data_json=excluded.data_json, data_text=excluded.data_text,
                updated_at=excluded.updated_at""",
                (artifact_id, case_id, type_, name, payload, data_text, now, now),
            )
        return artifact_id

    def get_artifact(self, case_id: str, type_: str, name: str) -> Optional[dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM artifacts WHERE id=?", (f"{case_id}:{type_}:{name}",)).fetchone()
        return self._artifact_row(row)

    def list_artifacts(self, case_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM artifacts WHERE case_id=? ORDER BY updated_at DESC", (case_id,))
            return [item for row in rows if (item := self._artifact_row(row)) is not None]

    @staticmethod
    def _artifact_row(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
        if not row:
            return None
        item = dict(row)
        if item.get("data_json"):
            item["data_json"] = json.loads(item["data_json"])
        return item

    def get_app_setting(self, key: str) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def set_app_setting(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO app_settings VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (key, value, time.time()),
            )

    def get_llm_override(self, agent: str) -> Optional[dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM llm_overrides WHERE agent=?", (agent,)).fetchone()
        return dict(row) if row else None

    def add_ai_log(self, *, case_id: Optional[str], task_id: Optional[str], agent: Optional[str],
                   model: Optional[str], request_json: Optional[str], response_json: Optional[str],
                   duration_ms: Optional[int], error: Optional[str]) -> str:
        log_id = str(uuid.uuid4())
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO ai_logs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (log_id, case_id, task_id, agent, model, request_json, response_json, duration_ms, error, time.time()),
            )
        return log_id

    def update_ai_log(self, log_id: str, *, response_json: Optional[str] = None,
                      duration_ms: Optional[int] = None, error: Optional[str] = None) -> None:
        values = {"response_json": response_json, "duration_ms": duration_ms, "error": error}
        fields = {key: value for key, value in values.items() if value is not None}
        if not fields:
            return
        with self._conn() as conn:
            conn.execute(
                f"UPDATE ai_logs SET {', '.join(f'{key}=?' for key in fields)} WHERE id=?",
                list(fields.values()) + [log_id],
            )

    def list_ai_logs(self, *, limit: int = 200, case_id: Optional[str] = None,
                     task_id: Optional[str] = None, agent: Optional[str] = None) -> list[dict[str, Any]]:
        clauses, params = [], []
        for column, value in (("case_id", case_id), ("task_id", task_id), ("agent", agent)):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM ai_logs {where} ORDER BY created_at DESC LIMIT ?", params + [int(limit)]
            )
            return [dict(row) for row in rows]
