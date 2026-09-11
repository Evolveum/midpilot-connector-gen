# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import pytest

from src.modules.codegen.utils.connector_code_validation import (
    ConnectorCodeFormat,
    ConnectorCodeValidationError,
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


def test_validate_yaml_connector_code_accepts_explicit_method_on_search_endpoint() -> None:
    code = (
        "objectClasses:\n"
        "  User:\n"
        "    search:\n"
        "      endpoints:\n"
        "        - path: /users/search\n"
        "          method: POST\n"
    )
    assert validate_yaml_connector_code(code) is None


def test_validate_yaml_connector_code_accepts_authentication_document() -> None:
    code = "authentication:\n  rest:\n    bearer:\n      implementation: |\n        request.header('X', 'y')\n"
    assert validate_yaml_connector_code(code) is None


def test_validate_yaml_connector_code_rejects_unknown_top_level_key() -> None:
    error = validate_yaml_connector_code("objectClass: Person\n")
    assert error is not None
    assert "objectClass" in error


def test_validate_yaml_connector_code_rejects_unknown_object_class_key() -> None:
    error = validate_yaml_connector_code("objectClasses:\n  Person:\n    bogusKey: true\n")
    assert error is not None
    assert "bogusKey" in error


def test_validate_yaml_connector_code_rejects_unknown_authentication_key() -> None:
    error = validate_yaml_connector_code("authentication:\n  bogus:\n    basic: {}\n")
    assert error is not None
    assert "bogus" in error


def test_validate_yaml_connector_code_allows_arbitrary_object_class_and_attribute_names() -> None:
    code = "objectClasses:\n  AnythingGoesHere:\n    attributes:\n      whateverAttributeName: {}\n"
    assert validate_yaml_connector_code(code) is None


def test_validate_yaml_connector_code_rejects_duplicate_keys() -> None:
    error = validate_yaml_connector_code("objectClasses:\n  User: {}\n  User: {}\n")
    assert error is not None
    assert "duplicate" in error.lower()


def test_validate_yaml_connector_code_rejects_multiple_documents() -> None:
    error = validate_yaml_connector_code("objectClasses: {User: {}}\n---\nobjectClasses: {Group: {}}\n")
    assert error is not None


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
        ("objectClasses: {User: {attributes: {id: {connId: {typo: x}}}}}", "connId.typo"),
        ('objectClasses: {User: {create: {enabled: "false"}}}', "create.enabled"),
        ("objectClasses: {User: {search: {endpoints: {path: /users}}}}", "search.endpoints"),
        ("objectClasses: {User: {search: {endpoints: [3]}}}", "search.endpoints.0"),
        ("objectClasses: {User: {search: {endpoints: [{path: false}]}}}", "path"),
        ("objectClasses: {User: {references: {group: null}}}", "references.group"),
        ("objectClasses: {User: {relationships: {}}}", "objectClasses.User.relationships"),
        ("relationships: {member: {subject: {class: User, attribute: member}}}", "attribute"),
        ("authentication: null", "authentication"),
        ("authentication: {rest: {bearer: {implementation: 3}}}", "implementation"),
        ("authentication: {rest: {preference: bearer}}", "preference"),
        ('authentication: {rest: {bearer: {validateToken: "true"}}}', "validateToken"),
    ],
)
def test_nested_configuration_rejects_wrong_types_and_keys(code, path):
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
        "relationships: {}",
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
    "objectClasses.User.attributes.id.scim.implementation.deserialize",
    "objectClasses.User.attributes.id.scim.implementation.serialize",
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
@pytest.mark.parametrize(
    "hook", ["objectExtractor", "pagingSupport", "spec", "request", "body", "resolver", "relationship"]
)
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
    elif hook == "resolver":
        document["objectClasses"]["User"]["search"]["attributeResolvers"] = [
            {"attribute": "team", "implementation": script}
        ]
    else:
        document = {
            "relationships": {
                "membership": {
                    "subject": {
                        "class": "User",
                        "attribute": {"name": "group", "resolver": {"implementation": script}},
                    },
                    "object": {"class": "Group", "attribute": {"name": "member"}},
                }
            }
        }
    error = validate_connector_code(yaml.safe_dump(document))
    assert (error is None) == (script == "value")
    if error:
        assert "objectClasses.User." in error or "relationships.membership." in error


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


def test_bundled_declarative_examples_validate_without_reformatting():
    import re
    from pathlib import Path

    root = Path("src/modules/codegen/documentations")
    for path in (root / "declarative-yaml.adoc", root / "sql/declarative-yaml.adoc"):
        blocks = re.findall(r"\[source,yaml\]\n----\n(.*?)\n----", path.read_text(), re.DOTALL)
        assert blocks
        for block in blocks:
            if block.startswith("connector:"):
                continue  # Manifest packaging is outside the connector artifact contract.
            assert ensure_valid_connector_code(block) == block.strip(), path
