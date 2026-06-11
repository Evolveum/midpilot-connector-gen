# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from uuid import uuid4

from src.common.session.documentation_upload import UploadedDocumentation
from src.common.session.router import _chunk_uploaded_documentation


def test_chunk_uploaded_documentation_preserves_schema_as_single_item():
    text = "CREATE TABLE users (id bigint primary key);\n" * 200
    uploaded = UploadedDocumentation(
        text=text,
        filename="schema.sql",
        content_type="text/sql",
        metadata={
            "filename": "schema.sql",
            "contentType": "text/sql",
            "parser": "text",
            "preserveAsSingleDocumentationItem": True,
            "chunkingStrategy": "single_item_schema",
        },
        preserve_as_single_item=True,
    )

    chunks = _chunk_uploaded_documentation(uuid4(), uploaded)

    assert len(chunks) == 1
    assert chunks[0][0] == text
    assert chunks[0][1] > 0
