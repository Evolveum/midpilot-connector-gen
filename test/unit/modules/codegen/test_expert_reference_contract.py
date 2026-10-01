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
        ("scim: {extensions: {enterprise: {flatten: photos}}}", "scim.extensions"),
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


@pytest.mark.parametrize(
    "mapping",
    [
        'scim: {extensions: {enterprise: "urn:example"}}',
        'scim: {flatten: name, extensions: {enterprise: {uri: "urn:example", flatten: [photos]}}}',
        "scim: {flatten: [name, emails]}",
        'search: {endpoints: [{path: /users, objectExtractor: {value: "$.data"}}]}',
        "search: {endpoints: [{path: /users, objectExtractor: {type: json_pointer, value: /data}}]}",
        "search: {endpoints: [{path: /users, method: POST, pagingSupport: {pageSize: 50, parameters: "
        "{pageSize: {in: body, name: size}, page: {in: header}, offset: {in: query}}}}]}",
    ],
)
def test_new_declarative_forms_are_literal_not_groovy(mapping):
    from unittest.mock import patch

    code = "objectClasses: {User: {" + mapping + "}}"
    with patch("src.modules.codegen.utils.connector_code_validation.validate_groovy_code") as parser:
        assert GroovyCodePayload(code=code).code == code
        parser.assert_not_called()


@pytest.mark.parametrize(
    "mapping",
    [
        "scim: {flatten: {name: true}}",
        "scim: {flatten: [null]}",
        'scim: {flatten: " "}',
        "scim: {extensions: {enterprise: null}}",
        "search: {endpoints: [{path: /users, objectExtractor: {type: UNKNOWN, value: /data}}]}",
        "search: {endpoints: [{path: /users, objectExtractor: {type: JSON_PATH}}]}",
        "search: {endpoints: [{path: /users, pagingSupport: {pageSize: 0}}]}",
        "search: {endpoints: [{path: /users, pagingSupport: {parameters: {cursor: {in: query}}}}]}",
        "search: {endpoints: [{path: /users, pagingSupport: {parameters: {page: {in: cookie}}}}]}",
        "search: {endpoints: [{path: /users, pagingSupport: {parameters: {page: {name: page}}}}]}",
    ],
)
def test_new_declarative_forms_reject_invalid_shapes(mapping):
    with pytest.raises(ValidationError):
        GroovyCodePayload(code="objectClasses: {User: {" + mapping + "}}")


@pytest.mark.parametrize("hook", ["objectExtractor", "pagingSupport"])
def test_declarative_alternatives_do_not_bypass_script_validation(hook):
    code = "objectClasses: {User: {search: {endpoints: [{path: /users, " + hook + ": 'invalid('}]}}}"
    with pytest.raises(ValidationError, match=hook):
        GroovyCodePayload(code=code)


def test_bundled_references_have_no_unresolved_includes():
    from pathlib import Path

    for path in Path("src/modules/codegen/documentations").rglob("*.adoc"):
        assert "include::" not in path.read_text(), path


def test_new_schema_references_stay_scoped_to_their_protocol_and_operation():
    from src.modules.codegen.selection.protocol_selectors import get_operation_assets
    from src.shared.enums import ApiType

    scim_docs, _ = load_operation_documentation(get_operation_assets("native_schema", ApiType.SCIM))
    assert "Flattening extension attributes" in scim_docs
    sql_docs, _ = load_operation_documentation(get_operation_assets("native_schema", ApiType.SQL))
    assert "Schema Script Basics (SQL)" in sql_docs
    assert "Flattening SCIM Attributes" not in sql_docs
    for protocol in ApiType:
        docs, _ = load_operation_documentation(get_operation_assets("create", protocol))
        assert "Flattening SCIM Attributes" not in docs
        assert "JSON Types and Formats" not in docs


def test_authorization_includes_method_reference_and_customization_context():
    from src.modules.codegen.selection.protocol_selectors import get_operation_assets
    from src.shared.enums import ApiType

    docs, _ = load_operation_documentation(get_operation_assets("authorization", ApiType.REST))
    assert "oauth2ClientCredentials" in docs
    assert "== The implementation context" in docs
    assert "newRequest(url)" in docs
    assert "*replaces* the built-in behavior" in docs


def test_reference_lists_claim_only_chapters_present_in_the_same_context():
    import re

    from src.modules.codegen.selection.protocol_selectors import get_operation_assets
    from src.shared.enums import ApiType

    claim = re.compile(r"^\* \*([^*]+)\*[^*]*?\(chapter shown together with this page\)", re.MULTILINE)
    for protocol in ApiType:
        assets = get_operation_assets("native_schema", protocol)
        docs, _ = load_operation_documentation(assets)
        connid_docs = load_required_adoc_text("src.modules.codegen.documentations", assets.connid_docs_path)
        headings = set(re.findall(r"^=+ (.+)$", docs + "\n" + connid_docs, re.MULTILINE))
        claimed = claim.findall(docs)
        assert claimed, protocol
        assert set(claimed) <= headings, protocol


@pytest.mark.parametrize("protocol", ["rest", "scim", "sql"])
def test_native_schema_prompt_bridges_extracted_types_and_date_built_ins(protocol):
    from src.modules.codegen.selection.protocol_selectors import get_operation_assets
    from src.shared.enums import ApiType

    system_prompt = get_operation_assets("native_schema", ApiType(protocol)).system_prompt
    assert system_prompt.count("EXTRACTED TYPES:") == 1
    assert "__LAST_LOGIN_DATE__" in system_prompt


def test_connector_fix_prompts_bridge_extracted_types():
    from src.modules.codegen.prompts.fix_prompts import get_connector_fix_system_prompt, get_connector_fix_user_prompt
    from src.modules.codegen.prompts.sql.fix_prompts import get_sql_connector_fix_system_prompt

    assert get_connector_fix_system_prompt.count("EXTRACTED TYPES:") == 1
    assert get_sql_connector_fix_system_prompt.count("EXTRACTED TYPES:") == 1
    assert "Groovy scripts selected" not in get_connector_fix_user_prompt


@pytest.mark.parametrize("protocol", ["rest", "scim"])
def test_relationship_prompt_uses_groovy_because_yaml_relationships_are_rejected(protocol):
    from src.modules.codegen.selection.protocol_selectors import get_operation_assets
    from src.shared.enums import ApiType

    assets = get_operation_assets("relationship", ApiType(protocol))
    assert 'complete Groovy relationship("...") block' in assets.system_prompt
    assert "prefer the documented root-level relationships YAML map" not in assets.system_prompt
    _, declarative = load_operation_documentation(assets)
    assert "the schema loader rejects a `relationships` block" in declarative
