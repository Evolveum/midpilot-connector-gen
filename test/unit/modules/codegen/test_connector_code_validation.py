# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import pytest
import yaml

from src.modules.codegen.enums import ConnectorCodeFormat
from src.modules.codegen.errors import ConnectorCodeValidationError
from src.modules.codegen.utils.connector_code_validation import (
    collect_connector_code_errors,
    detect_connector_code_format,
    ensure_valid_connector_code,
    validate_connector_code,
    validate_yaml_connector_code,
)

# Real Groovy samples from the existing DSL family, used to pin down the empirical basis for
# format detection: none of them parses to a YAML mapping (dict), so "parses to a dict" is a
# safe and simple discriminator between the two formats.
GROOVY_SAMPLES = [
    'objectClass("User") {}',
    "search {\n}\n",
    'objectClass("User") {\n    search {\n        endpoint("/users") {\n            singleResult()\n        }\n    }\n}\n',
    (
        "authentication {\n"
        "    rest {\n"
        "        bearer {\n"
        "            implementation {\n"
        '                request.header("Authorization", "Bearer " + decrypt(configuration.restTokenValue))\n'
        "            }\n"
        "        }\n"
        "    }\n"
        "}\n"
    ),
    'def token = configuration.token ?: "default"\nauthentication {\n    rest { }\n}\n',
    'def headers = [Authorization: "Bearer x"]\nrequest.headers(headers)\n',
    "oauth2ClientCredentials { oauth2Context ->\n    validateToken { true }\n}\n",
    "import java.time.Instant\ndef now = Instant.now()\nauthentication {\n  rest { }\n}\n",
]

YAML_SAMPLES = [
    "objectClasses: {User: {}}\n",
    (
        "objectClasses:\n"
        "  Person:\n"
        "    sql:\n"
        "      table: app_user\n"
        "    attributes:\n"
        "      user_id:\n"
        "        connId:\n"
        "          name: __UID__\n"
        "      loginCount: {}\n"
    ),
    "objectClasses:\n  Employee:\n    create:\n      enabled: false\n",
    'authentication:\n  rest:\n    bearer:\n      implementation: |\n        request.header("Authorization", token)\n',
]


@pytest.mark.parametrize("groovy_code", GROOVY_SAMPLES)
def test_detect_connector_code_format_classifies_groovy(groovy_code: str) -> None:
    assert detect_connector_code_format(groovy_code) is ConnectorCodeFormat.GROOVY


@pytest.mark.parametrize("yaml_code", YAML_SAMPLES)
def test_detect_connector_code_format_classifies_yaml(yaml_code: str) -> None:
    assert detect_connector_code_format(yaml_code) is ConnectorCodeFormat.YAML


def test_detect_connector_code_format_treats_empty_as_groovy() -> None:
    """Empty input is invalid either way; it is routed to the Groovy path, which reports it."""
    assert detect_connector_code_format("") is ConnectorCodeFormat.GROOVY


def test_detect_connector_code_format_strips_markdown_fences_first() -> None:
    fenced = "```yaml\nobjectClasses: {User: {}}\n```"
    assert detect_connector_code_format(fenced) is ConnectorCodeFormat.YAML


def test_validate_yaml_connector_code_accepts_minimal_native_schema() -> None:
    assert validate_yaml_connector_code("objectClasses: {User: {}}") is None


def test_validate_yaml_connector_code_accepts_full_native_schema() -> None:
    code = (
        "objectClasses:\n"
        "  Person:\n"
        "    sql:\n"
        "      table: app_user\n"
        "    readOnly: true\n"
        "    connId:\n"
        "      UID: user_id\n"
        "    attributes:\n"
        "      user_id:\n"
        "        connId:\n"
        "          name: __UID__\n"
        "        sql:\n"
        "          type: INT\n"
        "          primaryKey: true\n"
        "    references:\n"
        "      manager:\n"
        "        objectClass: Person\n"
        "        role: SUBJECT\n"
    )
    assert validate_yaml_connector_code(code) is None


def test_validate_yaml_connector_code_accepts_operation_override() -> None:
    assert validate_yaml_connector_code("objectClasses:\n  Employee:\n    create:\n      enabled: false\n") is None


def test_yaml_search_endpoint_accepts_a_stated_method_search_custom_supports_other_methods() -> None:
    """
    Search endpoints are always GET; stating a method explicitly (even a redundant GET) is
    accepted rather than rejected as an unknown key, mirroring ``_WriteEndpoint.method``.
    """
    code = (
        "objectClasses:\n  User:\n    search:\n      endpoints:\n        - path: /users/search\n          method: GET\n"
    )
    assert validate_yaml_connector_code(code) is None
    custom = "objectClasses: {User: {search: {custom: {implementation: 'return null'}}}}"
    assert validate_yaml_connector_code(custom) is None


def test_validate_yaml_connector_code_accepts_authentication_document() -> None:
    code = "authentication:\n  rest:\n    bearer:\n      implementation: |\n        request.header('X', 'y')\n"
    assert validate_yaml_connector_code(code) is None


def test_validate_yaml_connector_code_rejects_unknown_top_level_key() -> None:
    error = validate_yaml_connector_code("objectClass: Person\n")
    assert error is not None
    assert "objectClass" in error


@pytest.mark.parametrize(
    ("code", "path"),
    [
        ("objectClasses: {Person: {bogusKey: true}}", "objectClasses.Person.bogusKey"),
        ("authentication: {bogus: {basic: {}}}", "authentication.bogus"),
        ("objectClasses: {User: {attributes: {id: {connId: {typo: x}}}}}", "attributes.id.connId.typo"),
        ("objectClasses: {User: {relationships: {}}}", "objectClasses.User.relationships"),
        ('authentication: {rest: {bearer: {validateToken: "true"}}}', "bearer.validateToken"),
        (
            'objectClasses: {User: {scim: {extensions: {enterprise: {uri: "urn:example", unknown: true}}}}}',
            "scim.extensions.enterprise.unknown",
        ),
    ],
)
def test_unknown_nested_options_are_preserved_with_warning(code, path, caplog):
    assert ensure_valid_connector_code(code) == code
    assert "[Codegen:Validation] Unrecognized YAML option" in caplog.text
    assert path in caplog.text


def test_collect_errors_returns_every_structural_error_without_input_values():
    errors = collect_connector_code_errors("objectClasses: {User: {references: [], search: {endpoints: 42}}}")
    assert [error.split(": ", 1)[0] for error in errors] == [
        "objectClasses.User.references",
        "objectClasses.User.search.endpoints",
    ]
    assert errors[0] == "objectClasses.User.references: Input should be a valid dictionary"
    assert "42" not in errors[1]


def test_collect_errors_reports_script_errors_and_only_logs_unknown_options(caplog):
    code = "objectClasses: {User: {attributeResolvers: secret-value, search: {custom: {implementation: 'return ('}}}}"
    errors = collect_connector_code_errors(code)
    assert len(errors) == 1
    assert errors[0].startswith("objectClasses.User.search.custom.implementation: ")
    assert "objectClasses.User.attributeResolvers" in caplog.text
    assert "secret-value" not in caplog.text


def test_collect_errors_is_empty_for_valid_code():
    assert (
        collect_connector_code_errors(
            "objectClasses: {User: {search: {attributeResolvers: [{attribute: team, implementation: 'return []'}]}}}"
        )
        == ()
    )


def test_reference_metadata_is_preserved_without_logging_values(caplog):
    code = (
        "# Keep the user's formatting and metadata\n"
        "objectClasses:\n  group:\n    references:\n      members:\n"
        "        objectClass: User\n        role: object\n"
        "        description: private-description-value\n        multiValued: true"
    )
    assert ensure_valid_connector_code(code) == code
    assert "objectClasses.group.references.members.description" in caplog.text
    assert "objectClasses.group.references.members.multiValued" in caplog.text
    assert "private-description-value" not in caplog.text


@pytest.mark.parametrize(
    ("code", "path"),
    [
        (
            "objectClasses: {group: {references: {members: {description: x, multiValued: true, role: []}}}}",
            "references.members.role",
        ),
        (
            "objectClasses: {group: {references: {members: {description: x}}, "
            "search: {custom: {implementation: 'return ('}}}}",
            "search.custom.implementation",
        ),
    ],
)
def test_unknown_options_do_not_bypass_known_field_or_script_validation(code, path):
    error = validate_connector_code(code)
    assert error is not None
    assert path in error


def test_validate_yaml_connector_code_allows_arbitrary_object_class_and_attribute_names() -> None:
    code = "objectClasses:\n  AnythingGoesHere:\n    attributes:\n      whateverAttributeName: {}\n"
    assert validate_yaml_connector_code(code) is None


def test_validate_yaml_connector_code_rejects_duplicate_keys() -> None:
    error = validate_yaml_connector_code("objectClasses:\n  User: {}\n  User: {}\n")
    assert error is not None
    assert "duplicate" in error.lower()


def test_connector_duplicate_key_rules_do_not_modify_safe_loader() -> None:
    code = "objectClasses: {User: {}, User: {}}"
    assert validate_yaml_connector_code(code) is not None
    assert yaml.safe_load(code) == {"objectClasses": {"User": {}}}


def test_validate_yaml_connector_code_rejects_multiple_documents() -> None:
    error = validate_yaml_connector_code("objectClasses: {User: {}}\n---\nobjectClasses: {Group: {}}\n")
    assert error is not None


@pytest.mark.parametrize(
    "value, expected_error",
    [
        ("&loop [*loop]", "cyclic aliases"),
        ("&loop {self: *loop}", "cyclic aliases"),
        ("&loop [{nested: *loop}]", "cyclic aliases"),
    ],
)
def test_validate_connector_code_rejects_cyclic_aliases(value: str, expected_error: str) -> None:
    code = (
        "objectClasses: {User: {update: {endpoints: [{path: /users, "
        "supportedAttributes: [{name: status, value: " + value + "}]}]}}}"
    )
    error = validate_connector_code(code)
    assert error is not None
    assert expected_error in error


def test_validate_connector_code_accepts_shared_acyclic_aliases() -> None:
    code = (
        "objectClasses: {User: {update: {endpoints: [{path: /users, "
        "supportedAttributes: [{name: status, value: &shared [active]}, "
        "{name: previousStatus, value: *shared}]}]}}}"
    )
    assert validate_connector_code(code) is None


def test_validate_yaml_connector_code_rejects_non_mapping_root() -> None:
    error = validate_yaml_connector_code("- User\n- Group\n")
    assert error is not None
    assert "mapping" in error.lower()


def test_validate_yaml_connector_code_rejects_unsafe_tag() -> None:
    error = validate_yaml_connector_code("objectClasses: !!python/object:os.system {}\n")
    assert error is not None


def test_validate_yaml_connector_code_rejects_empty_string() -> None:
    error = validate_yaml_connector_code("")
    assert error == "Connector code cannot be empty"


@pytest.mark.parametrize("groovy_code", GROOVY_SAMPLES)
def test_validate_connector_code_routes_groovy_to_groovy_validator(groovy_code: str) -> None:
    # Every sample here is syntactically valid Groovy per the existing groovy-parser-backed check.
    assert validate_connector_code(groovy_code) is None


@pytest.mark.parametrize("yaml_code", YAML_SAMPLES)
def test_validate_connector_code_routes_yaml_to_yaml_validator(yaml_code: str) -> None:
    assert validate_connector_code(yaml_code) is None


def test_validate_connector_code_rejects_invalid_groovy() -> None:
    error = validate_connector_code('objectClass("User") { search {')
    assert error is not None


def test_ensure_valid_connector_code_raises_for_invalid_input() -> None:
    with pytest.raises(ConnectorCodeValidationError):
        ensure_valid_connector_code("objectClass: Person\n")


def test_ensure_valid_connector_code_returns_normalized_code_for_valid_yaml() -> None:
    result = ensure_valid_connector_code("```yaml\nobjectClasses: {User: {}}\n```")
    assert result == "objectClasses: {User: {}}"


def test_ensure_valid_connector_code_returns_normalized_code_for_valid_groovy() -> None:
    result = ensure_valid_connector_code('```groovy\nobjectClass("User") {}\n```')
    assert result == 'objectClass("User") {}'


@pytest.mark.parametrize(
    ("code", "path"),
    [
        ("objectClasses: 3", "objectClasses"),
        ("objectClasses: {User: null}", "objectClasses.User"),
        ("objectClasses: {User: {attributes: []}}", "objectClasses.User.attributes"),
        ("objectClasses: {User: {attributes: {id: 3}}}", "objectClasses.User.attributes.id"),
        ('objectClasses: {User: {attributes: {id: {required: "false"}}}}', "required"),
        ("objectClasses: {User: {attributes: {id: {scim: {path: 3}}}}}", "scim.path"),
        ('objectClasses: {User: {create: {enabled: "false"}}}', "create.enabled"),
        ("objectClasses: {User: {search: {endpoints: {path: /users}}}}", "search.endpoints"),
        ("objectClasses: {User: {search: {endpoints: [3]}}}", "search.endpoints.0"),
        ("objectClasses: {User: {search: {endpoints: [{path: false}]}}}", "path"),
        ("objectClasses: {User: {references: {group: null}}}", "references.group"),
        ("authentication: null", "authentication"),
        ("authentication: {rest: {bearer: {implementation: 3}}}", "implementation"),
        ("authentication: {rest: {preference: bearer}}", "preference"),
    ],
)
def test_nested_configuration_rejects_wrong_types_and_shapes(code, path):
    error = validate_connector_code(code)
    assert error is not None
    assert path in error


@pytest.mark.parametrize(
    "code",
    [
        "{}",
        "objectClasses: {}",
        "objectClasses: {User: {attributes: {id: null, name: {}}}}",
        "objectClasses: {User: {create: {}, search: {normalize: {}}, references: {group: {}}}}",
        "authentication: {rest: {bearer: {}, oauth2Password: {}}}",
        "objectClasses: {User: {update: {endpoints: [{path: /users, supportedAttributes: [name, {name: active, value: true}, {name: status, transition: {from: active, to: locked}}]}]}}}",
    ],
)
def test_documented_defaults_and_supported_attribute_variants(code):
    assert validate_connector_code(code) is None


# Every documented script group is checked with real parser input. YAML paths are
# assembled as mappings so YAML quoting cannot mask a broken Groovy expression.
SCRIPT_PATHS = [
    f"authentication.{channel}.{method}.{hook}"
    for channel in ("rest", "scim")
    for method, hooks in [
        *[(method, ("implementation",)) for method in ("basic", "bearer", "jwtBearer", "apiKey")],
        *[
            (
                method,
                (
                    "implementation",
                    "validateToken",
                    "buildTokenRequest",
                    "parseTokenResponse",
                    "applyToken",
                    "onResponse",
                ),
            )
            for method in ("oauth2ClientCredentials", "oauth2Password", "oauth2JwtBearer", "oauth2Saml")
        ],
    ]
    for hook in hooks
] + [
    *[
        f"objectClasses.User.search.normalize.{hook}"
        for hook in ("rewriteUid", "rewriteName", "restoreUid", "restoreName")
    ],
    "objectClasses.User.search.custom.implementation",
]


@pytest.mark.parametrize("path", SCRIPT_PATHS)
@pytest.mark.parametrize("script", ["return value", "return ("])
def test_embedded_hooks_are_parsed_with_yaml_path(path, script):
    import yaml

    value = script
    for key in reversed(path.split(".")):
        value = {key: value}
    error = validate_connector_code(yaml.safe_dump(value))
    if script == "return value":
        assert error is None
    else:
        assert error is not None
        assert path in error


@pytest.mark.parametrize("script", ["value", "("])
@pytest.mark.parametrize("hook", ["objectExtractor", "pagingSupport", "spec", "request", "body", "resolver"])
def test_endpoint_filter_and_resolver_scripts(script, hook):
    import yaml

    endpoint = {"path": "/users"}
    document = {"objectClasses": {"User": {"search": {"endpoints": [endpoint]}}}}
    if hook in ("objectExtractor", "pagingSupport"):
        endpoint[hook] = script
    elif hook in ("spec", "request"):
        endpoint["supportedFilters"] = [{hook: script}]
    elif hook == "body":
        document = {
            "objectClasses": {"User": {"create": {"endpoints": [{"path": "/users", "request": {"body": script}}]}}}
        }
    else:
        document["objectClasses"]["User"]["search"]["attributeResolvers"] = [
            {"attribute": "team", "implementation": script}
        ]
    error = validate_connector_code(yaml.safe_dump(document))
    assert (error is None) == (script == "value")
    if error:
        assert "objectClasses.User." in error


def test_only_request_body_empty_sentinel_bypasses_parser():
    from unittest.mock import patch

    with patch("src.modules.codegen.utils.connector_code_validation.validate_groovy_code", return_value=None) as parser:
        assert (
            validate_connector_code(
                "objectClasses: {User: {create: {endpoints: [{path: /users, request: {body: EMPTY}}]}}}"
            )
            is None
        )
        parser.assert_not_called()
        assert validate_connector_code("authentication: {rest: {bearer: {implementation: EMPTY}}}") is None
        parser.assert_called_once()


def test_configuration_strings_are_never_parsed_as_groovy():
    code = 'objectClasses: {User: {search: {normalize: {toSingleValue: "invalid("}}, attributes: {email: {scim: {path: "emails[primary eq true].value"}}}}}'
    assert validate_connector_code(code) is None


def test_bundled_declarative_examples_are_valid_artifacts():
    import re
    from pathlib import Path

    import yaml

    root = Path("src/modules/codegen/documentations")
    # Every bundled YAML example is imitated by the model, so each must pass the same validation.
    blocks_by_path = {
        path: re.findall(r"\[source,yaml\]\n----\n(.*?)\n----", path.read_text(), re.DOTALL)
        for path in sorted(root.rglob("*.adoc"))
    }
    assert blocks_by_path[root / "declarative-yaml.adoc"]
    assert blocks_by_path[root / "sql/declarative-yaml.adoc"]
    for path, blocks in blocks_by_path.items():
        for block in blocks:
            if re.search(r"^connector:", block, re.MULTILINE):
                continue  # Manifest examples contain AsciiDoc callouts, not artifact YAML.
            document = yaml.safe_load(block)
            if "attributes" in document:
                # Explicitly shown as an attribute fragment in the reference.
                assert validate_yaml_connector_code(yaml.safe_dump({"objectClasses": {"User": document}})) is None
            else:
                assert ensure_valid_connector_code(block) == block.strip(), path


# Values accepted by the connector runtime parsers (see docs/codegen-expert-references.adoc).
@pytest.mark.parametrize(
    "code",
    [
        "objectClasses: {User: {attributes: {password: {connId: {name: __PASSWORD__, type: GuardedString}}}}}",
        "objectClasses: {User: {attributes: {enabledAt: {connId: {name: ENABLE_DATE, type: Long}}}}}",
        "objectClasses: {User: {attributes: {photo: {jsonType: binary, json: {type: binary}}}}}",
        "objectClasses: {User: {attributes: {login: {sql: {name: LOGIN, type: VARCHAR(255)}}}}}",
        "objectClasses: {User: {search: {endpoints: [{path: /users, method: put, responseFormat: JSON_ARRAY}]}}}",
        "objectClasses: {User: {search: {endpoints: [{path: /users, objectExtractor: {value: $.data}}]}}}",
        'objectClasses: {User: {search: {endpoints: [{path: /users, objectExtractor: "// data\\nresponse"}]}}}',
        "objectClasses: {User: {search: {attributeResolvers: [{attribute: team, resolutionType: per_object}]}}}",
        "objectClasses: {User: {delete: {endpoints: [{path: /users/{id}, method: delete}]}}}",
    ],
)
def test_runtime_vocabulary_is_accepted(code):
    assert validate_connector_code(code) is None


@pytest.mark.parametrize(
    "extractor",
    [
        "/data/.with { key -> response.body().get(key) }",
        "$/data/$.with { key -> response.body().get(key) }",
        "  /data/.with { key -> response.body().get(key) }\n",
        "  $/data/$.with { key -> response.body().get(key) }\n",
        "// Extract data\n/data/.with { key -> response.body().get(key) }",
        "/* Extract data */\n$/data/$.with { key -> response.body().get(key) }",
        "$key = 'data'; response.body().get($key)",
    ],
)
def test_object_extractor_accepts_groovy_starting_with_path_like_prefix(extractor):
    code = yaml.safe_dump(
        {"objectClasses": {"User": {"search": {"endpoints": [{"path": "/users", "objectExtractor": extractor}]}}}}
    )
    assert ensure_valid_connector_code(code) == code.strip()


@pytest.mark.parametrize(
    "extractor",
    ["$", "$.data", "$..data", "$.data.items[0]", "$['data']", "$.data[*]", "/data", "/data/items/0", "/data~1items"],
)
@pytest.mark.parametrize("padding", ["", " \n"])
def test_object_extractor_rejects_complete_bare_paths(extractor, padding):
    code = yaml.safe_dump(
        {
            "objectClasses": {
                "User": {
                    "search": {"endpoints": [{"path": "/users", "objectExtractor": f"{padding}{extractor}{padding}"}]}
                }
            }
        }
    )
    error = validate_connector_code(code)
    assert error is not None
    assert "objectExtractor" in error
    assert "write a JSONPath or JSON Pointer as {value: ...}" in error


@pytest.mark.parametrize(
    "extractor",
    [
        "/data/.with { key -> response.body().get(key)",
        "$/data/$.with { key -> response.body().get(key)",
    ],
)
def test_object_extractor_still_rejects_invalid_groovy_literals(extractor):
    code = yaml.safe_dump(
        {"objectClasses": {"User": {"search": {"endpoints": [{"path": "/users", "objectExtractor": extractor}]}}}}
    )
    error = validate_connector_code(code)
    assert error is not None
    assert "objectExtractor" in error
    assert "a scalar objectExtractor is a Groovy block" not in error


@pytest.mark.parametrize(
    ("code", "path"),
    [
        ("objectClasses: {User: {attributes: {a: {connId: {type: long}}}}}", "connId.type"),
        ("objectClasses: {User: {attributes: {a: {connId: {type: guardedstring}}}}}", "connId.type"),
        ("objectClasses: {User: {attributes: {a: {jsonType: User}}}}", "jsonType"),
        ("objectClasses: {User: {attributes: {a: {jsonType: object}}}}", "jsonType"),
        ("objectClasses: {User: {attributes: {a: {json: {type: array}}}}}", "json.type"),
        ("objectClasses: {User: {search: {endpoints: [{path: /users, responseFormat: XML}]}}}", "responseFormat"),
        ("objectClasses: {User: {search: {endpoints: [{path: /users, method: FETCH}]}}}", "method"),
        ("objectClasses: {User: {create: {endpoints: [{path: /users, method: SEND}]}}}", "method"),
        ("objectClasses: {User: {search: {attributeResolvers: [{resolutionType: EAGER}]}}}", "resolutionType"),
        ("objectClasses: {User: {search: {endpoints: [{path: /users, objectExtractor: $.data}]}}}", "objectExtractor"),
        ("objectClasses: {User: {search: {endpoints: [{path: /users, objectExtractor: /data}]}}}", "objectExtractor"),
    ],
)
def test_values_the_runtime_rejects_are_rejected(code, path):
    error = validate_connector_code(code)
    assert error is not None
    assert path in error


@pytest.mark.parametrize(
    ("code", "key"),
    [
        ("relationships: {member: {subject: {class: User}}}", "relationships"),
        (
            "objectClasses: {User: {create: {endpoints: [{path: /users, supportedAttributes: [name]}]}}}",
            "supportedAttributes",
        ),
        (
            "objectClasses: {User: {delete: {endpoints: [{path: /users, supportedAttributes: [name]}]}}}",
            "supportedAttributes",
        ),
        ("objectClasses: {User: {attributes: {a: {scim: {implementation: {deserialize: it}}}}}}", "implementation"),
        ("objectClasses: {User: {attributes: {a: {json: {implementation: {serialize: it}}}}}}", "implementation"),
    ],
)
def test_keys_the_runtime_rejects_are_rejected_with_reason(code, key):
    error = validate_connector_code(code)
    assert error is not None
    assert f"'{key}' is not supported in declarative YAML" in error
