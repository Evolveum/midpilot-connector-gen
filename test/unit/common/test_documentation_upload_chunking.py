# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.common.session.session import build_processed_chunk_metadata
from src.common.session.utils.documentation_upload import (
    PreparedDocumentationUpload,
    RawUploadedDocumentation,
    SessionUploadContext,
    UploadedDocumentation,
    chunk_uploaded_documentation,
    queue_documentation_upload_job,
)


class _FakeSessionRepository:
    def __init__(self) -> None:
        self.updated_session_payloads: list[tuple[object, dict[str, str]]] = []

    async def update_session(self, session_id: object, data: dict[str, str]) -> None:
        self.updated_session_payloads.append((session_id, data))


def test_chunk_uploaded_documentation_preserves_schema_as_single_item():
    text = "CREATE TABLE users (id bigint primary key);\n" * 200
    uploaded = UploadedDocumentation(
        text=text,
        filename="schema.sql",
        content_type="text/sql",
        metadata={
            "filename": "schema.sql",
            "content_type": "text/sql",
            "parser": "text",
            "preserve_as_single_documentation_item": True,
            "chunking_strategy": "single_item_schema",
        },
        preserve_as_single_item=True,
    )

    chunks = chunk_uploaded_documentation(uuid4(), uploaded)

    assert len(chunks) == 1
    assert chunks[0][0] == text
    assert chunks[0][1] > 0


def test_processed_chunk_metadata_uses_token_count_name():
    metadata = build_processed_chunk_metadata(
        filename="schema.sql",
        chunk_number=0,
        token_count=42,
        character_count=17,
        num_endpoints=0,
        tags=["SQL"],
        category="reference_other",
    )

    assert metadata["token_count"] == 42
    assert metadata["character_count"] == 17
    assert "length" not in metadata


@pytest.mark.asyncio
async def test_queue_documentation_upload_job_schedules_raw_upload_without_storing_bytes_in_input():
    session_id = uuid4()
    doc_id = uuid4()
    job_id = uuid4()
    raw_upload = RawUploadedDocumentation(
        data=b"CREATE TABLE users (id bigint primary key);",
        filename="schema.sql",
        content_type="text/sql",
        content_hash="content-hash",
    )
    prepared = PreparedDocumentationUpload(
        raw_upload=raw_upload,
        context=SessionUploadContext(app="Example", app_version="1.0"),
    )
    repo = _FakeSessionRepository()

    with patch(
        "src.common.session.utils.documentation_upload.schedule_coroutine_job",
        new_callable=AsyncMock,
    ) as mock_schedule:
        mock_schedule.return_value = job_id

        returned_job_id = await queue_documentation_upload_job(
            repo=repo,  # type: ignore[arg-type]
            session_id=session_id,
            doc_id=doc_id,
            prepared=prepared,
            skip_cache=True,
        )

    assert returned_job_id == job_id
    schedule_kwargs = mock_schedule.await_args.kwargs
    input_payload = schedule_kwargs["input_payload"]
    worker_kwargs = schedule_kwargs["worker_kwargs"]

    assert "data" not in input_payload
    assert "chunks" not in input_payload
    assert input_payload["content_hash"] == "content-hash"
    assert input_payload["size_bytes"] == len(raw_upload.data)
    assert worker_kwargs["raw_upload"] is raw_upload
    assert "chunks" not in worker_kwargs
    assert repo.updated_session_payloads == [
        (session_id, {f"documentation.processUpload_{doc_id}_job_id": str(job_id)})
    ]
