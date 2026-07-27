from __future__ import annotations

import uuid


class MockIngestBackend:
    def create_vector_store(self, name: str) -> str:
        return f"vs_mock_{uuid.uuid4().hex}"

    def upload_file_from_path(self, path: str) -> tuple[str, str]:
        return (f"upload_mock_{uuid.uuid4().hex}", f"file_mock_{uuid.uuid4().hex}")

    def attach_file_to_vector_store(self, vector_store_id: str, file_id: str) -> str:
        return f"vs_file_mock_{uuid.uuid4().hex}"

    def wait_vector_store_file_completed(self, vector_store_id: str, vs_file_id: str) -> None:
        return None
