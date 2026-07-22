# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.modules.digester.extractors.scim.attributes import (
    extract_scim_attributes,
    get_scim_schema_attributes_for_object_class,
)
from src.modules.digester.extractors.scim.baseline import (
    ConnIdObjectClassDefinition,
    ScimResourceDefinition,
    build_scim_baseline_bundle,
)
from src.modules.digester.schemas.common import ChunkReference

_SCHEMA_DIR = Path(__file__).parent / "scim_schemas"


def _baseline_schemas() -> dict:
    """Load the SCIM baseline schemas that stand in for a session's conndev documents."""
    schemas: dict = {}
    for path in sorted(_SCHEMA_DIR.glob("*.json")):
        schema = json.loads(path.read_text())
        schemas[schema["name"]] = schema
    return schemas


BASELINE_SCHEMAS = _baseline_schemas()
BASELINE_BUNDLE = build_scim_baseline_bundle(BASELINE_SCHEMAS)


def test_scim_user_attributes_are_derived_from_schema_with_embedded_complex_attributes():
    attributes = get_scim_schema_attributes_for_object_class(BASELINE_SCHEMAS, "User")

    assert attributes is not None
    assert "userName" in attributes
    assert "displayName" in attributes
    assert "password" in attributes
    assert "name" in attributes
    assert "phoneNumbers" in attributes
    assert "groups" in attributes

    username = attributes["userName"]
    assert username["type"] == "string"
    assert username["format"] is None
    assert username["description"]
    assert username["mandatory"] is True
    assert username["updatable"] is True
    assert username["creatable"] is True
    assert username["readable"] is True
    assert username["multivalue"] is False
    assert username["returnedByDefault"] is True
    assert username["scimAttribute"] == "userName"

    password = attributes["password"]
    assert password["readable"] is False
    assert password["returnedByDefault"] is False

    phone_numbers = attributes["phoneNumbers"]
    assert phone_numbers["type"] == "UserPhoneNumbers"
    assert phone_numbers["format"] == "embedded"
    assert phone_numbers["multivalue"] is True
    assert phone_numbers["scimAttribute"] == "phoneNumbers"


def test_scim_embedded_attributes_are_derived_from_source_subattributes():
    attributes = get_scim_schema_attributes_for_object_class(BASELINE_SCHEMAS, "UserPhoneNumbers")

    assert attributes is not None
    assert set(attributes) == {"value", "display", "type", "primary"}
    assert attributes["value"]["type"] == "string"
    assert attributes["value"]["scimAttribute"] == "phoneNumbers.value"
    assert attributes["type"]["type"] == "string"
    assert attributes["type"]["scimAttribute"] == "phoneNumbers.type"
    assert attributes["primary"]["type"] == "boolean"
    assert attributes["primary"]["scimAttribute"] == "phoneNumbers.primary"


def test_scim_readonly_embedded_attributes_are_not_creatable_or_updatable():
    attributes = get_scim_schema_attributes_for_object_class(BASELINE_SCHEMAS, "UserGroups")

    assert attributes is not None
    assert attributes["value"]["updatable"] is False
    assert attributes["value"]["creatable"] is False
    assert attributes["value"]["readable"] is True
    assert attributes["$ref"]["format"] == "reference"
    assert attributes["$ref"]["scimAttribute"] == "groups.$ref"


def test_unknown_scim_object_class_returns_none_for_documentation_fallback():
    assert get_scim_schema_attributes_for_object_class(BASELINE_SCHEMAS, "CustomApplication") is None


@pytest.mark.asyncio
async def test_extract_scim_attributes_persists_schema_context_and_provenance_without_llm():
    schema_reference = ChunkReference(doc_id=str(uuid4()), chunk_id=str(uuid4()))
    resource_reference = ChunkReference(doc_id=str(uuid4()), chunk_id=str(uuid4()))
    object_class_reference = ChunkReference(doc_id=str(uuid4()), chunk_id=str(uuid4()))
    user_schema = BASELINE_SCHEMAS["User"]
    bundle = build_scim_baseline_bundle(
        {"User": user_schema},
        resources={
            "User": ScimResourceDefinition(
                name="User",
                endpoint="/Users",
                schema_urn=user_schema["id"],
                primary_schema=user_schema,
                source_reference=resource_reference,
            )
        },
        connid_classes={
            "User": ConnIdObjectClassDefinition(
                name="User",
                namespace=user_schema["id"],
                locator="/Users",
                uid="User",
                attributes=[
                    {"name": "userName", "type": "string", "required": True},
                    {"name": "id", "type": "string"},
                ],
                source_reference=object_class_reference,
            )
        },
        schema_references={"User": schema_reference},
    )

    with (
        patch("src.modules.digester.extractors.scim.attributes.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.extractors.scim.attributes.increment_processed_documents", new_callable=AsyncMock),
        patch(
            "src.modules.digester.extractors.scim.attributes.load_session_scim_baseline",
            new_callable=AsyncMock,
            return_value=bundle,
        ),
    ):
        result = await extract_scim_attributes([], "User", uuid4(), uuid4())

    expected_references = {
        (reference.doc_id, reference.chunk_id)
        for reference in (schema_reference, resource_reference, object_class_reference)
    }
    assert {
        (reference["docId"], reference["chunkId"])
        for reference in result["result"]["attributes"]["userName"]["relevantDocumentations"]
    } == expected_references
    assert result["result"]["scimContext"]["resource"]["endpoint"] == "/Users"
    assert [
        attribute["name"] for attribute in result["result"]["scimContext"]["connectorObjectClass"]["attributes"]
    ] == ["userName", "id"]


@pytest.mark.asyncio
async def test_extract_scim_attributes_does_not_send_conndev_contracts_to_llm():
    chunk_id = str(uuid4())
    with (
        patch("src.modules.digester.extractors.scim.attributes.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.extractors.scim.attributes.increment_processed_documents", new_callable=AsyncMock),
        patch("src.modules.digester.extractors.scim.attributes._build_scim_attribute_chain") as build_chain,
        patch(
            "src.modules.digester.extractors.scim.attributes.load_session_scim_baseline",
            new_callable=AsyncMock,
            return_value=BASELINE_BUNDLE,
        ),
    ):
        result = await extract_scim_attributes(
            [json.dumps({"schemaContent": json.dumps(BASELINE_SCHEMAS["User"])})],
            "User",
            uuid4(),
            uuid4(),
            [chunk_id],
            chunk_metadata_map={
                chunk_id: {
                    "@metadata": {"content_type": "application/com.evolveum.conndev+json"},
                }
            },
        )

    assert "userName" in result["result"]["attributes"]
    build_chain.assert_not_called()


@pytest.mark.asyncio
async def test_extract_scim_attributes_uses_general_discovery_without_scim_baseline():
    job_id = uuid4()
    conndev_chunk_id = str(uuid4())
    documentation_chunk_id = str(uuid4())
    documentation_doc_id = str(uuid4())
    metadata_map = {
        conndev_chunk_id: {
            "@metadata": {"content_type": "application/com.evolveum.conndev+json"},
        },
        documentation_chunk_id: {
            "@metadata": {"content_type": "text/html"},
        },
    }
    documented_result = {
        "result": {
            "attributes": {
                "actionId": {
                    "type": "string",
                    "description": "Identifier documented for the custom Action resource.",
                }
            }
        },
        "relevantDocumentations": [
            {"docId": documentation_doc_id, "chunkId": documentation_chunk_id},
        ],
    }

    with (
        patch("src.modules.digester.extractors.scim.attributes._build_scim_attribute_chain") as build_chain,
        patch(
            "src.modules.digester.extractors.scim.attributes.load_session_scim_baseline",
            new_callable=AsyncMock,
            return_value=build_scim_baseline_bundle({}),
        ),
        patch(
            "src.modules.digester.extractors.scim.attributes.extract_documented_attributes",
            new_callable=AsyncMock,
            return_value=documented_result,
        ) as extract_documented,
    ):
        result = await extract_scim_attributes(
            ["connector contract", "Action attributes include actionId."],
            "Action",
            job_id,
            uuid4(),
            [conndev_chunk_id, documentation_chunk_id],
            chunk_metadata_map=metadata_map,
            chunk_id_to_doc_id={documentation_chunk_id: documentation_doc_id},
        )

    extract_documented.assert_awaited_once_with(
        chunks=["Action attributes include actionId."],
        object_class="Action",
        job_id=job_id,
        chunk_details=[documentation_chunk_id],
        chunk_metadata_map=metadata_map,
        chunk_id_to_doc_id={documentation_chunk_id: documentation_doc_id},
    )
    build_chain.assert_not_called()
    assert "actionId" in result["result"]["attributes"]
    assert result["result"]["scimContext"] == {}
    assert result["relevantDocumentations"] == [{"docId": documentation_doc_id, "chunkId": documentation_chunk_id}]


@pytest.mark.asyncio
async def test_extract_scim_attributes_without_baseline_or_documentation_returns_empty_result():
    with (
        patch(
            "src.modules.digester.extractors.scim.attributes.load_session_scim_baseline",
            new_callable=AsyncMock,
            return_value=build_scim_baseline_bundle({}),
        ),
        patch(
            "src.modules.digester.extractors.scim.attributes.extract_documented_attributes",
            new_callable=AsyncMock,
        ) as extract_documented,
    ):
        result = await extract_scim_attributes([], "Action", uuid4(), uuid4())

    extract_documented.assert_not_awaited()
    assert result["result"]["attributes"] == {}
    assert result["result"]["scimContext"] == {}
    assert result["relevantDocumentations"] == []


@pytest.mark.asyncio
async def test_extract_scim_attributes_merges_documented_mapping_over_schema_baseline():
    chunk_id = str(uuid4())
    doc_id = str(uuid4())

    with (
        patch("src.modules.digester.extractors.scim.attributes.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.extractors.scim.attributes.increment_processed_documents", new_callable=AsyncMock),
        patch("src.modules.digester.extractors.scim.attributes._build_scim_attribute_chain", return_value=object()),
        patch(
            "src.modules.digester.extractors.scim.attributes.load_session_scim_baseline",
            new_callable=AsyncMock,
            return_value=BASELINE_BUNDLE,
        ),
        patch(
            "src.modules.digester.extractors.scim.attributes.invoke_chunk_chain",
            new_callable=AsyncMock,
            return_value={
                "attributes": {
                    "Username": {
                        "type": "string",
                        "format": None,
                        "description": "Slack Username maps to SCIM userName but is optional in the target app.",
                        "mandatory": False,
                        "updatable": True,
                        "creatable": True,
                        "readable": True,
                        "multivalue": False,
                        "returnedByDefault": True,
                        "scimAttribute": "userName",
                    },
                    "Slack Profile Id": {
                        "type": "string",
                        "format": None,
                        "description": "Slack profile id maps to a Slack SCIM extension field.",
                        "mandatory": False,
                        "updatable": True,
                        "creatable": True,
                        "readable": True,
                        "multivalue": False,
                        "returnedByDefault": True,
                        "scimAttribute": "urn:scim:schemas:extension:slack:profile:2.0:User:profileId",
                    },
                    "Emails": {
                        "multivalue": True,
                        "description": "This complex SCIM attribute belongs to UserEmails.",
                        "scimAttribute": "emails",
                    },
                    "City": {
                        "multivalue": False,
                        "description": "This complex SCIM sub-attribute belongs to UserAddresses.",
                        "scimAttribute": "addresses[primary]['locality']",
                    },
                    "Members": {
                        "multivalue": True,
                        "description": "This belongs to Group, not User.",
                        "scimAttribute": "members",
                    },
                }
            },
        ) as mock_invoke,
    ):
        result = await extract_scim_attributes(
            ["mapping table"],
            "User",
            uuid4(),
            uuid4(),
            [chunk_id],
            chunk_id_to_doc_id={chunk_id: doc_id},
        )

    attributes = result["result"]["attributes"]
    assert "Username" in attributes
    assert "userName" not in attributes
    assert attributes["Username"]["mandatory"] is False
    assert (
        attributes["Username"]["description"]
        == "Slack Username maps to SCIM userName but is optional in the target app."
    )
    assert attributes["Username"]["scimAttribute"] == "userName"
    assert attributes["Username"]["relevantDocumentations"] == [{"docId": doc_id, "chunkId": chunk_id}]
    assert "Slack Profile Id" in attributes
    assert (
        attributes["Slack Profile Id"]["scimAttribute"] == "urn:scim:schemas:extension:slack:profile:2.0:User:profileId"
    )
    assert attributes["Emails"]["type"] == "UserEmails"
    assert attributes["Emails"]["format"] == "embedded"
    assert attributes["Emails"]["scimAttribute"] == "emails"
    assert attributes["City"]["type"] == "UserAddresses"
    assert attributes["City"]["format"] == "embedded"
    assert attributes["City"]["scimAttribute"] == "addresses[primary].locality"
    assert "Members" not in attributes
    mock_invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_scim_embedded_attributes_match_indexed_documented_paths_to_schema_baseline():
    chunk_id = str(uuid4())
    doc_id = str(uuid4())

    with (
        patch("src.modules.digester.extractors.scim.attributes.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.extractors.scim.attributes.increment_processed_documents", new_callable=AsyncMock),
        patch("src.modules.digester.extractors.scim.attributes._build_scim_attribute_chain", return_value=object()),
        patch(
            "src.modules.digester.extractors.scim.attributes.load_session_scim_baseline",
            new_callable=AsyncMock,
            return_value=BASELINE_BUNDLE,
        ),
        patch(
            "src.modules.digester.extractors.scim.attributes.invoke_chunk_chain",
            new_callable=AsyncMock,
            return_value={
                "attributes": {
                    "Primary Email": {
                        "type": "string",
                        "format": "email",
                        "description": "Target primary email maps to the first SCIM email value.",
                        "mandatory": True,
                        "updatable": True,
                        "creatable": True,
                        "readable": True,
                        "multivalue": False,
                        "returnedByDefault": True,
                        "scimAttribute": "emails[0].value",
                    },
                    "Work Email Display": {
                        "type": "string",
                        "format": None,
                        "description": "Target display label maps to the filtered work email display value.",
                        "mandatory": False,
                        "updatable": True,
                        "creatable": True,
                        "readable": True,
                        "multivalue": False,
                        "returnedByDefault": True,
                        "scimAttribute": "emails[type eq 'work'].display",
                    },
                }
            },
        ) as mock_invoke,
    ):
        result = await extract_scim_attributes(
            ["email mapping table"],
            "UserEmails",
            uuid4(),
            uuid4(),
            [chunk_id],
            chunk_id_to_doc_id={chunk_id: doc_id},
        )

    attributes = result["result"]["attributes"]
    assert set(attributes) == {"Primary Email", "Work Email Display", "type", "primary"}
    assert attributes["Primary Email"]["description"] == "Target primary email maps to the first SCIM email value."
    assert attributes["Primary Email"]["scimAttribute"] == "emails.value"
    assert attributes["Primary Email"]["mandatory"] is True
    assert attributes["Primary Email"]["relevantDocumentations"] == [{"docId": doc_id, "chunkId": chunk_id}]
    assert attributes["Work Email Display"]["scimAttribute"] == "emails.display"
    assert attributes["Work Email Display"]["relevantDocumentations"] == [{"docId": doc_id, "chunkId": chunk_id}]
    assert "value" not in attributes
    assert "display" not in attributes
    mock_invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_scim_embedded_attributes_discards_unmatched_documented_mappings():
    chunk_id = str(uuid4())
    doc_id = str(uuid4())

    with (
        patch("src.modules.digester.extractors.scim.attributes.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.extractors.scim.attributes.increment_processed_documents", new_callable=AsyncMock),
        patch("src.modules.digester.extractors.scim.attributes._build_scim_attribute_chain", return_value=object()),
        patch(
            "src.modules.digester.extractors.scim.attributes.load_session_scim_baseline",
            new_callable=AsyncMock,
            return_value=BASELINE_BUNDLE,
        ),
        patch(
            "src.modules.digester.extractors.scim.attributes.invoke_chunk_chain",
            new_callable=AsyncMock,
            return_value={
                "attributes": {
                    "Formatted name": {
                        "type": "string",
                        "format": None,
                        "description": "Target display name maps to the SCIM formatted name component.",
                        "mandatory": False,
                        "updatable": True,
                        "creatable": True,
                        "readable": True,
                        "multivalue": False,
                        "returnedByDefault": True,
                        "scimAttribute": "name.formatted",
                    },
                    "Username": {
                        "type": "string",
                        "format": None,
                        "description": "This belongs to the parent User, not UserName.",
                        "mandatory": True,
                        "updatable": True,
                        "creatable": True,
                        "readable": True,
                        "multivalue": False,
                        "returnedByDefault": True,
                        "scimAttribute": "userName",
                    },
                }
            },
        ) as mock_invoke,
    ):
        result = await extract_scim_attributes(
            ["mapping table"],
            "UserName",
            uuid4(),
            uuid4(),
            [chunk_id],
            chunk_id_to_doc_id={chunk_id: doc_id},
        )

    attributes = result["result"]["attributes"]
    assert set(attributes) == {
        "Formatted name",
        "familyName",
        "givenName",
        "middleName",
        "honorificPrefix",
        "honorificSuffix",
    }
    assert (
        attributes["Formatted name"]["description"] == "Target display name maps to the SCIM formatted name component."
    )
    assert attributes["Formatted name"]["scimAttribute"] == "name.formatted"
    assert attributes["Formatted name"]["relevantDocumentations"] == [{"docId": doc_id, "chunkId": chunk_id}]
    assert "Username" not in attributes
    assert "userName" not in attributes
    mock_invoke.assert_awaited_once()
