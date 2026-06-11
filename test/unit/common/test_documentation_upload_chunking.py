# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from uuid import uuid4

from src.common.session.session import build_processed_chunk_metadata
from src.common.session.utils.documentation_upload import UploadedDocumentation, chunk_uploaded_documentation


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
