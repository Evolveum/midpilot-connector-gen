# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
from collections import Counter
from typing import Any

import pytest
import yaml

from src.documents.chunking import ApiDescriptionSplit, count_tokens, split_api_description
from src.documents.spec_validation import load_structured_document

HTTP_METHODS = {"get", "put", "post", "delete", "patch"}
RESOURCES = [("Users", "User"), ("Groups", "Group"), ("Projects", "Project"), ("Roles", "Role")]


def _schema(name: str, properties: int) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            f"{name.lower()}Attribute{index}": {"type": "string", "description": f"Attribute {index} of a {name}."}
            for index in range(properties)
        },
    }
    if name != "User":
        schema["properties"]["owner"] = {"$ref": "#/components/schemas/User"}
    return schema


def _openapi(resources: list[tuple[str, str]] = RESOURCES, *, properties: int = 12) -> dict[str, Any]:
    """An OpenAPI 3 description with a collection and an item path per resource and one unreferenced schema."""
    error_response = {"$ref": "#/components/responses/Error"}
    paths: dict[str, Any] = {}
    schemas: dict[str, Any] = {"Error": {"type": "object", "properties": {"message": {"type": "string"}}}}
    for tag, schema in resources:
        item = {"$ref": f"#/components/schemas/{schema}"}
        body = {"content": {"application/json": {"schema": item}}}
        collection = f"/api/v1/{tag.lower()}"
        paths[collection] = {
            "get": {
                "tags": [tag],
                "summary": f"List {tag.lower()}",
                "responses": {
                    "200": {"content": {"application/json": {"schema": {"type": "array", "items": item}}}},
                    "default": error_response,
                },
            },
            "post": {"tags": [tag], "requestBody": body, "responses": {"201": body, "default": error_response}},
        }
        paths[f"{collection}/{{id}}"] = {
            "parameters": [{"$ref": "#/components/parameters/Id"}],
            **{
                method: {"tags": [tag], "responses": {"200": body, "default": error_response}}
                for method in ("get", "patch", "delete")
            },
        }
        schemas[schema] = _schema(schema, properties)
    schemas["AuditEvent"] = _schema("AuditEvent", 4)
    return {
        "openapi": "3.0.3",
        "info": {"title": "Example API", "version": "2.1", "description": "Manages the accounts of a tenant."},
        "servers": [{"url": "https://api.example.test"}],
        "security": [{"basicAuth": []}],
        "tags": [{"name": tag, "description": f"Manage {tag.lower()}."} for tag, _ in resources],
        "paths": paths,
        "components": {
            "schemas": schemas,
            "responses": {
                "Error": {
                    "description": "Error",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}},
                }
            },
            "parameters": {"Id": {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}},
            "securitySchemes": {"basicAuth": {"type": "http", "scheme": "basic"}},
        },
    }


def _documents(split: ApiDescriptionSplit) -> list[dict[str, Any]]:
    documents = []
    for chunk in split.chunks:
        loaded = load_structured_document(chunk.text)
        assert loaded is not None and isinstance(loaded[0], dict) and loaded[1] == split.syntax
        documents.append(loaded[0])
    return documents


def _operations(document: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (path, method)
        for path, item in (document.get("paths") or {}).items()
        for method in item
        if method in HTTP_METHODS
    ]


def _local_refs(value: Any) -> list[str]:
    if isinstance(value, dict):
        ref = value.get("$ref")
        return ([ref] if isinstance(ref, str) and ref.startswith("#/") else []) + [
            found for item in value.values() for found in _local_refs(item)
        ]
    if isinstance(value, list):
        return [found for item in value for found in _local_refs(item)]
    return []


def _resolves(document: dict[str, Any], ref: str) -> bool:
    node: Any = document
    for segment in ref[2:].split("/"):
        if not isinstance(node, dict) or segment not in node:
            return False
        node = node[segment]
    return True


def _component_names(document: dict[str, Any]) -> set[tuple[str, str]]:
    components = document.get("components") or {}
    return {(kind, name) for kind, entries in components.items() for name in entries}


def test_description_that_fits_one_chunk_is_kept_whole() -> None:
    text = json.dumps(_openapi(RESOURCES[:2]), indent=2)

    split = split_api_description(text, max_tokens=count_tokens(text))

    assert split is not None and len(split.chunks) == 1
    (chunk,) = split.chunks
    assert chunk.text == text
    assert chunk.operation_count == 10
    assert {"Users", "User", "Groups", "Group", "Error", "AuditEvent"} <= set(chunk.object_classes)


@pytest.mark.parametrize(
    ("serialize", "syntax", "indented"),
    [
        (lambda document: json.dumps(document, indent=2), "json", True),
        (lambda document: json.dumps(document, separators=(",", ":")), "json", False),
        (lambda document: yaml.safe_dump(document, sort_keys=False), "yaml", True),
    ],
)
def test_large_description_is_split_into_self_contained_descriptions(serialize, syntax: str, indented: bool) -> None:
    source = _openapi()
    max_tokens = 1500

    split = split_api_description(serialize(source), max_tokens=max_tokens)

    assert split is not None and split.syntax == syntax
    documents = _documents(split)
    assert len(documents) > 3
    assert all(chunk.token_count <= max_tokens for chunk in split.chunks)
    assert all(("\n" in chunk.text) is indented for chunk in split.chunks)

    # Every operation lands in exactly one chunk, and its chunk counts it.
    assert Counter(operation for document in documents for operation in _operations(document)) == Counter(
        _operations(source)
    )
    assert [chunk.operation_count for chunk in split.chunks] == [len(_operations(d)) for d in documents]

    # Every chunk repeats the header, resolves its own references and keeps the source components.
    for document in documents:
        assert document["openapi"] == "3.0.3"
        assert document["info"]["title"] == "Example API" and document["info"]["version"] == "2.1"
        assert document["servers"] == source["servers"]
        assert all(_resolves(document, ref) for ref in _local_refs(document))
    assert set().union(*(_component_names(document) for document in documents)) == _component_names(source)

    # Description-wide parts are in the overview only.
    overview, *rest = documents
    assert overview["info"] == source["info"] and overview["security"] == source["security"]
    assert overview["components"] == {"securitySchemes": source["components"]["securitySchemes"]}
    assert all("securitySchemes" not in (document.get("components") or {}) for document in rest)
    assert all("description" not in document["info"] for document in rest)


def test_operation_chunk_names_its_resource_and_the_schemas_its_operations_use() -> None:
    split = split_api_description(json.dumps(_openapi()), max_tokens=1500)

    assert split is not None
    documents = _documents(split)
    users_chunk = next(
        chunk for chunk, document in zip(split.chunks, documents) if ("/api/v1/users", "get") in _operations(document)
    )
    audit_chunk = next(
        chunk
        for chunk, document in zip(split.chunks, documents)
        if "AuditEvent" in (document.get("components") or {}).get("schemas", {})
    )

    assert {"Users", "User", "Error"} <= set(users_chunk.object_classes)
    assert "AuditEvent" in audit_chunk.object_classes and audit_chunk.operation_count == 0
    # The resource chunk includes its tag, so the resource description travels with its operations.
    users_document = documents[split.chunks.index(users_chunk)]
    assert {"name": "Users", "description": "Manage users."} in users_document["tags"]


def test_small_resources_share_a_chunk() -> None:
    split = split_api_description(json.dumps(_openapi(properties=2)), max_tokens=3000)

    assert split is not None
    resource_chunks = [chunk for chunk in split.chunks if chunk.operation_count]
    assert len(resource_chunks) == 1
    assert {"Users", "Groups", "Projects", "Roles"} <= set(resource_chunks[0].object_classes)


def test_resource_larger_than_a_chunk_is_split_by_path_and_operation() -> None:
    source = _openapi(RESOURCES[:1])
    for method, operation in source["paths"]["/api/v1/users/{id}"].items():
        if method in HTTP_METHODS:
            operation["description"] = " ".join(f"{method} detail {index}." for index in range(150))
    max_tokens = 2000

    split = split_api_description(json.dumps(source), max_tokens=max_tokens)

    assert split is not None
    documents = _documents(split)
    item_chunks = [
        document for document in documents if any(p == "/api/v1/users/{id}" for p, _ in _operations(document))
    ]
    assert len(item_chunks) > 1
    assert all(chunk.token_count <= max_tokens for chunk in split.chunks)
    # Each operation of the split path keeps the path-level parameters and the components it references.
    for document in item_chunks:
        assert document["paths"]["/api/v1/users/{id}"]["parameters"] == [{"$ref": "#/components/parameters/Id"}]
        assert all(_resolves(document, ref) for ref in _local_refs(document))


def test_component_larger_than_a_chunk_is_split_into_valid_fragments() -> None:
    source = _openapi(RESOURCES[:1], properties=200)
    max_tokens = 1500

    split = split_api_description(json.dumps(source), max_tokens=max_tokens)

    assert split is not None
    documents = _documents(split)
    fragments = [
        (chunk, document)
        for chunk, document in zip(split.chunks, documents)
        if not chunk.operation_count and "User" in (document.get("components") or {}).get("schemas", {})
    ]
    assert len(fragments) > 1
    assert all(chunk.token_count <= max_tokens and chunk.object_classes == ("User",) for chunk, _ in fragments)
    recovered = {
        name for _, document in fragments for name in document["components"]["schemas"]["User"].get("properties", {})
    }
    assert recovered == set(source["components"]["schemas"]["User"]["properties"])


def test_yaml_aliases_of_the_source_are_written_out() -> None:
    shared = {"description": "Error", "content": {"application/json": {"schema": {"type": "object"}}}}
    source = _openapi(properties=10)
    for item in source["paths"].values():
        for method, operation in item.items():
            if method in HTTP_METHODS:
                operation["responses"]["default"] = shared
    text = yaml.safe_dump(source, sort_keys=False)
    assert "&id001" in text

    split = split_api_description(text, max_tokens=1500)

    assert split is not None and len(split.chunks) > 1
    assert all("&id0" not in chunk.text and "*id0" not in chunk.text for chunk in split.chunks)


def test_swagger_description_is_split_with_its_definitions() -> None:
    source = {
        "swagger": "2.0",
        "info": {"title": "Forge", "version": "1.0"},
        "host": "forge.example.test",
        "basePath": "/api/v1",
        "securityDefinitions": {"token": {"type": "apiKey", "name": "Authorization", "in": "header"}},
        "paths": {
            f"/{resource}/{{id}}": {
                "get": {
                    "tags": [resource],
                    "parameters": [{"$ref": "#/parameters/Id"}],
                    "responses": {
                        "200": {"schema": {"$ref": f"#/definitions/{resource.title()}"}},
                        "404": {"$ref": "#/responses/NotFound"},
                    },
                }
            }
            for resource in ("user", "team", "repo")
        },
        "definitions": {
            name: {"type": "object", "properties": {f"field{index}": {"type": "string"} for index in range(40)}}
            for name in ("User", "Team", "Repo")
        },
        "parameters": {"Id": {"name": "id", "in": "path", "required": True, "type": "integer"}},
        "responses": {"NotFound": {"description": "Not found"}},
    }

    split = split_api_description(json.dumps(source), max_tokens=500)

    assert split is not None
    documents = _documents(split)
    overview, *rest = documents
    assert overview["securityDefinitions"] == source["securityDefinitions"]
    resource_documents = [document for document in rest if _operations(document)]
    assert len(resource_documents) == 3
    for document in resource_documents:
        assert document["host"] == "forge.example.test" and document["basePath"] == "/api/v1"
        assert "securityDefinitions" not in document
        assert all(_resolves(document, ref) for ref in _local_refs(document))
    user_chunk = split.chunks[documents.index(resource_documents[0])]
    assert user_chunk.object_classes == ("user", "User")


def test_operations_without_tags_are_grouped_by_their_resource_path_segment() -> None:
    source = {
        "openapi": "3.1.0",
        "info": {"title": "Untagged", "version": "1"},
        "paths": {
            "/api/v2/users": {"get": {"responses": {"200": {"description": "ok"}}}},
            "/api/v2/users/{id}": {"get": {"responses": {"200": {"description": "ok"}}}},
            "/api/v2/groups/{id}/members": {"get": {"responses": {"200": {"description": "ok"}}}},
        },
    }

    split = split_api_description(json.dumps(source), max_tokens=10_000)

    assert split is not None
    assert split.chunks[0].object_classes == ("users", "groups")


@pytest.mark.parametrize(
    "text",
    [
        "Plain prose about the API.",
        json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "properties": {}}),
        json.dumps({"openapi": "3.0.0"}),
    ],
)
def test_text_that_is_not_an_openapi_or_swagger_description_is_not_split(text: str) -> None:
    assert split_api_description(text, max_tokens=1000) is None
