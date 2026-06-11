# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from src.common.session.documentation_upload import read_uploaded_documentation


def _upload(filename: str, content_type: str, data: bytes) -> UploadFile:
    upload = UploadFile(BytesIO(data), filename=filename)
    upload.headers = Headers({"content-type": content_type})
    return upload


@pytest.mark.asyncio
async def test_read_uploaded_documentation_keeps_content_type_metadata_for_json():
    uploaded = await read_uploaded_documentation(
        _upload("openapi.json", "application/json", b'{"openapi":"3.0.0","paths":{}}')
    )

    assert uploaded.filename == "openapi.json"
    assert uploaded.content_type == "application/json"
    assert uploaded.metadata["contentType"] == "application/json"
    assert uploaded.metadata["parser"] == "json"
    assert '"openapi": "3.0.0"' in uploaded.text


@pytest.mark.asyncio
async def test_read_uploaded_documentation_inferrs_content_type_when_upload_type_is_generic():
    uploaded = await read_uploaded_documentation(
        _upload("openapi.yaml", "application/octet-stream", b"openapi: 3.0.0\npaths: {}\n")
    )

    assert uploaded.content_type == "application/yaml"
    assert uploaded.metadata["contentType"] == "application/yaml"
    assert uploaded.metadata["parser"] == "yaml"
    assert "openapi: 3.0.0" in uploaded.text


@pytest.mark.asyncio
async def test_read_uploaded_documentation_inferrs_json_content_type_for_generic_json_upload():
    uploaded = await read_uploaded_documentation(
        _upload("user.json", "application/octet-stream", b'{"schemas":["urn:ietf:params:scim:schemas:core:2.0:User"]}')
    )

    assert uploaded.content_type == "application/json"
    assert uploaded.metadata["contentType"] == "application/json"
    assert uploaded.metadata["parser"] == "json"
    assert "urn:ietf:params:scim:schemas:core:2.0:User" in uploaded.text


@pytest.mark.asyncio
async def test_read_uploaded_documentation_uses_explicit_content_type_over_upload_default():
    uploaded = await read_uploaded_documentation(
        _upload("user.json", "application/octet-stream", b'{"schemas":["urn:ietf:params:scim:schemas:core:2.0:User"]}'),
        content_type="application/scim+json",
    )

    assert uploaded.content_type == "application/scim+json"
    assert uploaded.metadata["contentType"] == "application/scim+json"
    assert uploaded.metadata["parser"] == "json"
    assert uploaded.metadata["preserveAsSingleDocumentationItem"] is True
    assert uploaded.metadata["chunkingStrategy"] == "single_item_schema"
    assert uploaded.preserve_as_single_item is True
    assert "urn:ietf:params:scim:schemas:core:2.0:User" in uploaded.text


@pytest.mark.asyncio
async def test_read_uploaded_documentation_marks_raw_sql_schema_as_single_item():
    uploaded = await read_uploaded_documentation(
        _upload("schema.sql", "text/sql", b"CREATE TABLE users (id bigint primary key);\n")
    )

    assert uploaded.content_type == "text/sql"
    assert uploaded.metadata["parser"] == "text"
    assert uploaded.metadata["preserveAsSingleDocumentationItem"] is True
    assert uploaded.metadata["chunkingStrategy"] == "single_item_schema"
    assert uploaded.preserve_as_single_item is True


@pytest.mark.asyncio
async def test_read_uploaded_documentation_marks_conndev_yaml_schema_as_single_item():
    uploaded = await read_uploaded_documentation(
        _upload("schema.yaml", "application/conndev+yaml", b"objects:\n  - name: User\n")
    )

    assert uploaded.content_type == "application/conndev+yaml"
    assert uploaded.metadata["parser"] == "yaml"
    assert uploaded.metadata["preserveAsSingleDocumentationItem"] is True
    assert uploaded.metadata["chunkingStrategy"] == "single_item_schema"
    assert uploaded.preserve_as_single_item is True


@pytest.mark.asyncio
async def test_read_uploaded_documentation_extracts_html_text():
    uploaded = await read_uploaded_documentation(
        _upload("docs.html", "text/html", b"<html><script>ignore()</script><body><h1>API Docs</h1></body></html>")
    )

    assert uploaded.metadata["parser"] == "html"
    assert "API Docs" in uploaded.text
    assert "ignore()" not in uploaded.text


@pytest.mark.asyncio
async def test_read_uploaded_documentation_extracts_docx_text():
    from docx import Document

    buffer = BytesIO()
    document = Document()
    document.add_paragraph("Connector documentation")
    document.save(buffer)

    uploaded = await read_uploaded_documentation(
        _upload(
            "docs.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            buffer.getvalue(),
        )
    )

    assert uploaded.metadata["parser"] == "docx"
    assert "Connector documentation" in uploaded.text


@pytest.mark.asyncio
async def test_read_uploaded_documentation_rejects_unsupported_binary_type():
    with pytest.raises(HTTPException) as exc_info:
        await read_uploaded_documentation(_upload("archive.zip", "application/zip", b"PK\x03\x04"))

    assert exc_info.value.status_code == 415
    assert "Unsupported documentation content type" in exc_info.value.detail
