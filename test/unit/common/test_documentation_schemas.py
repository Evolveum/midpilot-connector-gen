# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from src.common.documentation import SavedDocumentation


def test_saved_documentation_accepts_content_type_alias_and_serializes_camel_case() -> None:
    documentation = SavedDocumentation(url="https://example.com/docs", contentType="application/json")

    assert documentation.content_type == "application/json"
    assert documentation.model_dump(by_alias=True)["contentType"] == "application/json"
    assert documentation.to_dict()["contentType"] == "application/json"
