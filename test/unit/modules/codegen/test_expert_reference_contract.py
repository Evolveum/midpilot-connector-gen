# Copyright (C) 2010-2026 Evolveum and contributors
# Licensed under the EUPL-1.2 or later.

import pytest
from langchain_core.prompts import ChatPromptTemplate
from pydantic import ValidationError

from src.modules.codegen.schema import GroovyCodePayload
from src.modules.codegen.selection.docs_loader import (
    load_operation_documentation,
    load_required_adoc_text,
    select_adoc_sections,
)
from src.modules.codegen.selection.protocol_selectors import PROMPT_MAP, SEARCH_PROMPT_MAP

_ASSETS = [
    (f"{operation}/{protocol.value}", assets)
    for operation, protocols in PROMPT_MAP.items()
    for protocol, assets in protocols.items()
] + [
    (f"search/{protocol.value}/{intent.value}", assets)
    for protocol, intents in SEARCH_PROMPT_MAP.items()
    for intent, assets in intents.items()
]


@pytest.mark.parametrize("name,assets", _ASSETS, ids=[name for name, _ in _ASSETS])
def test_every_operation_renders_its_expert_reference_and_only_known_inputs(name, assets):
    docs, declarative = load_operation_documentation(assets)
    values = dict.fromkeys(
        (
            "idx",
            "total",
            "object_class",
            "intent",
            "attributes_json",
            "endpoints_json",
            "preferred_endpoints_json",
            "base_api_url",
            "database_name",
            "result",
            "chunk",
            "repair_system_suffix",
            "repair_user_suffix",
            "records_json",
            "protocol",
            "relation_name",
            "relation_json",
            "authentication_container",
            "preferred_authorizations_json",
            "scim_protocol_schema_json",
            "scim_resource_contract_json",
            "connid_object_class_json",
            "scim_service_provider_config_json",
            "sql_physical_table_json",
            "sql_connector_object_class_json",
        ),
        "",
    )
    values.update(
        dict.fromkeys(
            (
                "protocol_schema_docs",
                "create_docs",
                "update_docs",
                "delete_docs",
                "search_docs",
                "authorization_docs",
                "relation_docs",
            ),
            docs,
        )
    )
    values["declarative_docs"] = declarative
    values["connid_attribute_docs"] = load_required_adoc_text(
        "src.modules.codegen.documentations", "connid-attributes.adoc"
    )
    template = ChatPromptTemplate.from_messages([("system", assets.system_prompt), ("human", assets.user_prompt)])
    assert set(template.input_variables) <= values.keys(), name
    rendered = template.format(**values)
    assert docs in rendered
    assert declarative in rendered
    assert rendered.count("DECLARATIVE YAML VS GROOVY:") == 1
    assert "domain-expert .adoc reference is authoritative" in rendered
    assert not any(
        old in rendered
        for old in (
            "only these keys are valid: `enabled`, `emptyFilterSupported`",
            "preserving its existing YAML or Groovy format across chunks",
        )
    )


def test_documentation_selection_preserves_nested_sections_and_rejects_drift():
    text = "= Reference\nIntro\n== Schema\nAttributes\n=== Paths\nNested paths\n== Search\nEndpoints\n"
    selected = select_adoc_sections(text, ("Schema", "Paths"))
    assert "Intro" in selected
    assert selected.count("Nested paths") == 1
    assert "Endpoints" not in selected
    with pytest.raises(ValueError, match="Expected one documentation section"):
        select_adoc_sections(text, ("Renamed section",))


@pytest.mark.parametrize(
    "mapping",
    [
        'json: {path: "$.name.givenName"}',
        "json: {path: {type: json_pointer, value: /name/givenName}}",
        "scim: {path: {type: SCIM, value: name.givenName}}",
    ],
)
def test_override_boundary_accepts_documented_attribute_paths(mapping):
    code = "objectClasses: {User: {attributes: {firstName: {" + mapping + "}}}}"
    assert GroovyCodePayload(code=code).code == code


@pytest.mark.parametrize(
    "mapping,location",
    [
        ('scim: {extensions: {enterprise: "urn:example"}}', "scim.extensions"),
        ("attributes: {firstName: {json: {path: {type: UNKNOWN, value: x}}}}", "firstName.json.path"),
    ],
)
def test_override_boundary_rejects_unsupported_schema_shapes(mapping, location):
    with pytest.raises(ValidationError, match=location):
        GroovyCodePayload(code="objectClasses: {User: {" + mapping + "}}")


def test_search_retains_non_get_application_methods_as_requirements():
    from src.modules.codegen.enums import SearchIntent
    from src.shared.enums import ApiType

    assets = SEARCH_PROMPT_MAP[ApiType.REST][SearchIntent.FILTER]
    assert "not a restriction to HTTP GET" in assets.system_prompt
    assert "own method-specific documentation" in assets.system_prompt
    assert "never change the target method" in assets.system_prompt
    assert "search.custom.implementation" in assets.system_prompt
    _, docs = load_operation_documentation(assets)
    assert "search.custom" in docs
    assert "== Authentication" not in docs
    assert "== Schema documents" not in docs
