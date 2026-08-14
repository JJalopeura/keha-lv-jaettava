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
                    id TEXT PRIMARY KEY, customer_id TEXT NOT NULL DEFAULT '', title TEXT NOT NULL, language TEXT NOT NULL,
                    status TEXT NOT NULL, case_vector_store_id TEXT, research_vector_store_id TEXT,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS customers (
                    id TEXT PRIMARY KEY, display_name TEXT NOT NULL, profile_json TEXT NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS package_reviews (
                    id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, permit_type TEXT NOT NULL,
                    municipality TEXT NOT NULL DEFAULT '', display_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active', promoted_case_id TEXT,
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
                CREATE TABLE IF NOT EXISTS ai_logs (
                    id TEXT PRIMARY KEY, case_id TEXT, task_id TEXT, agent TEXT, model TEXT,
                    request_json TEXT, response_json TEXT, duration_ms INTEGER, error TEXT,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_files_case_id ON files(case_id);
                CREATE INDEX IF NOT EXISTS idx_cases_customer_id ON cases(customer_id);
                CREATE INDEX IF NOT EXISTS idx_package_reviews_customer_id ON package_reviews(customer_id);
                CREATE INDEX IF NOT EXISTS idx_artifacts_case_updated ON artifacts(case_id, updated_at);
                CREATE INDEX IF NOT EXISTS idx_ai_logs_case_created ON ai_logs(case_id, created_at);
                """
            )
            case_columns = {row["name"] for row in conn.execute("PRAGMA table_info(cases)")}
            if "customer_id" not in case_columns:
                conn.execute("ALTER TABLE cases ADD COLUMN customer_id TEXT NOT NULL DEFAULT ''")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_cases_customer_id ON cases(customer_id)")

    def create_case(self, title: str, language: str = "fi", *, customer_id: str = "") -> str:
        case_id, now = str(uuid.uuid4()), time.time()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO cases
                (id, customer_id, title, language, status, case_vector_store_id,
                 research_vector_store_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (case_id, customer_id, title, language, "new", None, None, now, now),
            )
        return case_id

    def set_case_vector_store(self, case_id: str, vs_id: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE cases SET case_vector_store_id=?, updated_at=? WHERE id=?", (vs_id, time.time(), case_id))

    def set_research_vector_store(self, case_id: str, vs_id: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE cases SET research_vector_store_id=?, updated_at=? WHERE id=?", (vs_id, time.time(), case_id))

    def set_case_status(self, case_id: str, status: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE cases SET status=?, updated_at=? WHERE id=?", (status, time.time(), case_id))

    def list_cases(self, *, customer_id: Optional[str] = None) -> list[dict[str, Any]]:
        with self._conn() as conn:
            if customer_id is None:
                rows = conn.execute("SELECT * FROM cases ORDER BY updated_at DESC")
            else:
                rows = conn.execute(
                    "SELECT * FROM cases WHERE customer_id=? ORDER BY updated_at DESC",
                    (customer_id,),
                )
            return [dict(row) for row in rows]

    def list_cases_for_customer(self, customer_id: str) -> list[dict[str, Any]]:
        return self.list_cases(customer_id=customer_id)

    def get_case(self, case_id: str) -> Optional[dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
        return dict(row) if row else None

    def upsert_customer(self, customer_id: str, display_name: str, profile: dict[str, Any]) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO customers (id, display_name, profile_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET display_name=excluded.display_name,
                profile_json=excluded.profile_json, updated_at=excluded.updated_at""",
                (customer_id, display_name, json.dumps(profile, ensure_ascii=False), now, now),
            )

    def get_customer(self, customer_id: str) -> Optional[dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
        if not row:
            return None
        customer = dict(row)
        customer["profile"] = json.loads(customer.pop("profile_json") or "{}")
        return customer

    def list_customers(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM customers ORDER BY display_name").fetchall()
        customers = []
        for row in rows:
            customer = dict(row)
            customer["profile"] = json.loads(customer.pop("profile_json") or "{}")
            customers.append(customer)
        return customers

    def create_package_review(
        self,
        customer_id: str,
        permit_type: str,
        municipality: str,
        display_name: str,
    ) -> str:
        review_id, now = f"review_{uuid.uuid4().hex[:12]}", time.time()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO package_reviews
                (id, customer_id, permit_type, municipality, display_name, status, promoted_case_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'active', NULL, ?, ?)""",
                (review_id, customer_id, permit_type, municipality, display_name, now, now),
            )
        return review_id

    def get_package_review(self, review_id: str) -> Optional[dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM package_reviews WHERE id=?", (review_id,)).fetchone()
        return dict(row) if row else None

    def list_package_reviews(self, customer_id: Optional[str] = None) -> list[dict[str, Any]]:
        with self._conn() as conn:
            if customer_id is None:
                rows = conn.execute("SELECT * FROM package_reviews ORDER BY updated_at DESC").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM package_reviews WHERE customer_id=? ORDER BY updated_at DESC",
                    (customer_id,),
                ).fetchall()
        return [dict(row) for row in rows]

    def update_package_review(self, review_id: str, *, status: str, promoted_case_id: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE package_reviews SET status=?, promoted_case_id=?, updated_at=? WHERE id=?",
                (status, promoted_case_id or None, time.time(), review_id),
            )

    def copy_artifacts(self, source_scope: str, target_case_id: str) -> None:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT type, name, data_json, data_text FROM artifacts WHERE case_id=?",
                (source_scope,),
            ).fetchall()
        for row in rows:
            data_json = json.loads(row["data_json"]) if row["data_json"] else None
            self.upsert_artifact(target_case_id, row["type"], row["name"], data_json=data_json, data_text=row["data_text"])

    def move_files(self, source_scope: str, target_case_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE files SET case_id=?, updated_at=? WHERE case_id=?",
                (target_case_id, time.time(), source_scope),
            )

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
