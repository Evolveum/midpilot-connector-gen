# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json

import pytest
import yaml

from src.documents.spec_validation import canonical_hash, describe_spec, parse_structured_document

OPENAPI = {
    "openapi": "3.0.3",
    "info": {"title": "Example API", "version": "2.1"},
    "paths": {"/users": {"get": {"responses": {"200": {"description": "Users"}}}}},
}


def test_openapi_document_is_described_with_title_and_version() -> None:
    spec = describe_spec(OPENAPI)

    assert spec is not None
    assert (spec.format, spec.title, spec.version) == ("openapi", "Example API", "2.1")


def test_json_and_yaml_serializations_of_one_spec_have_the_same_hash() -> None:
    # Unquoted YAML keys such as ``200`` become integers; the hash must not depend on that.
    yaml_text = yaml.safe_dump(OPENAPI).replace("'200':", "200:")
    from_yaml = parse_structured_document(yaml_text)
    from_json = parse_structured_document(json.dumps(OPENAPI))

    assert isinstance(from_yaml, dict) and from_json is not None
    assert 200 in from_yaml["paths"]["/users"]["get"]["responses"]
    assert canonical_hash(from_yaml) == canonical_hash(from_json)
    assert canonical_hash({**OPENAPI, "info": {"title": "Other"}}) != canonical_hash(OPENAPI)


def test_copies_that_differ_only_in_their_servers_have_the_same_hash() -> None:
    def spec_hash(document: dict) -> str:
        description = describe_spec(document)
        assert description is not None
        return description.canonical_hash

    swagger = {"swagger": "2.0", "info": {"title": "Forge", "version": "1"}, "basePath": "/api/v1", "paths": {}}

    assert spec_hash({**OPENAPI, "servers": [{"url": "https://tenant.example.test"}]}) == spec_hash(OPENAPI)
    assert spec_hash({**swagger, "host": "forge.example.test", "schemes": ["https"]}) == spec_hash(swagger)
    # The API version and the base path stay part of the identity.
    assert spec_hash({**OPENAPI, "info": {"title": "Example API", "version": "3.0"}}) != spec_hash(OPENAPI)
    assert spec_hash({**swagger, "basePath": "/api/v2"}) != spec_hash(swagger)


@pytest.mark.parametrize(
    ("document", "spec_format"),
    [
        ({"swagger": "2.0", "info": {"title": "Legacy"}, "paths": {}}, "swagger"),
        ({"openapi": 3.1, "components": {"schemas": {}}}, "openapi"),
        (
            {"schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"], "Resources": []},
            "scim",
        ),
        ([{"id": "urn:ietf:params:scim:schemas:core:2.0:User", "attributes": []}], "scim"),
        ({"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "User"}, "json_schema"),
        ({"type": "object", "properties": {"id": {"type": "string"}}}, "json_schema"),
    ],
)
def test_api_description_formats_are_recognized(document, spec_format: str) -> None:
    spec = describe_spec(document)

    assert spec is not None and spec.format == spec_format


@pytest.mark.parametrize(
    "document",
    [
        {"name": "docs-portal", "version": "1.0.0", "dependencies": {"react": "^18"}},
        {"login.title": "Sign in", "login.button": "Continue"},
        {"openapi": "3.0.0"},
        [{"id": 1}, {"id": 2}],
    ],
)
def test_other_structured_files_are_not_api_descriptions(document) -> None:
    assert describe_spec(document) is None


@pytest.mark.parametrize("text", ["", "# Users\n\nThe users endpoint lists users.", "just a sentence", "42"])
def test_prose_and_scalars_are_not_structured_documents(text: str) -> None:
    assert parse_structured_document(text) is None
