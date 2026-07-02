# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from src.modules.codegen.utils.prompt_records import build_attribute_context_records, build_attribute_mapping_records
from src.modules.digester.schemas import AttributeInfoScim, AttributeResponse


def test_build_attribute_context_records_strips_relevant_documentation_refs():
    payload = AttributeResponse(
        attributes={
            "userName": AttributeInfoScim(
                type="string",
                description="Login",
                relevant_documentations=[{"docId": "doc-1", "chunkId": "chunk-1"}],
            )
        }
    )

    records = build_attribute_context_records(payload)

    assert len(records) == 1
    assert records[0]["name"] == "userName"
    assert records[0]["type"] == "string"
    assert records[0]["description"] == "Login"
    assert "relevantDocumentations" not in records[0]
    assert "relevant_documentations" not in records[0]


def test_build_attribute_context_records_accepts_nested_and_flat_mappings():
    nested_records = build_attribute_context_records(
        {
            "attributes": {
                "email": {
                    "type": "string",
                    "relevantDocumentations": [{"docId": "doc-1", "chunkId": "chunk-1"}],
                }
            }
        }
    )
    flat_records = build_attribute_context_records({"id": {"type": "integer"}})

    assert nested_records == [{"name": "email", "type": "string"}]
    assert flat_records == [{"name": "id", "type": "integer"}]


def test_build_attribute_mapping_records_uses_prompt_shape_and_sorting():
    records = build_attribute_mapping_records(
        {
            "attributes": {
                "id": {"type": "integer"},
                "email": {
                    "name": "Email",
                    "type": "string",
                    "format": "email",
                    "description": "Primary email",
                    "mandatory": True,
                    "updatable": True,
                    "updateable": False,
                    "creatable": True,
                    "readable": False,
                    "multivalue": True,
                    "returnedByDefault": False,
                    "relevantDocumentations": [{"docId": "doc-1", "chunkId": "chunk-1"}],
                },
            }
        }
    )

    assert records == [
        {
            "name": "Email",
            "jsonType": "string",
            "openApiFormat": "email",
            "description": "Primary email",
            "mandatory": True,
            "updateable": True,
            "creatable": True,
            "readable": False,
            "multivalue": True,
            "returnedByDefault": False,
        },
        {
            "name": "id",
            "jsonType": "integer",
            "openApiFormat": "",
            "description": "",
            "mandatory": False,
            "updateable": False,
            "creatable": False,
            "readable": True,
            "multivalue": False,
            "returnedByDefault": True,
        },
    ]
