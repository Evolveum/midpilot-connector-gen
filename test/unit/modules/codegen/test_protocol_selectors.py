# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import pytest

from src.common.enums import ApiType
from src.modules.codegen.enums import SearchIntent
from src.modules.codegen.prompts.native_schema_prompts import get_native_schema_system_prompt
from src.modules.codegen.prompts.scim.delete_prompts import get_scim_delete_system_prompt
from src.modules.codegen.prompts.scim.native_schema_prompts import get_scim_native_schema_system_prompt
from src.modules.codegen.prompts.sql.create_prompts import get_sql_create_system_prompt
from src.modules.codegen.prompts.sql.search_prompts import get_sql_search_filter_system_prompt
from src.modules.codegen.selection.protocol_selectors import get_operation_assets, get_search_operation_assets


def test_get_operation_assets_selects_sql_create_assets():
    assets = get_operation_assets("create", ApiType.SQL)

    assert assets.system_prompt == get_sql_create_system_prompt
    assert assets.docs_path == "sql/50-create.adoc"


def test_get_search_operation_assets_selects_sql_filter_assets():
    assets = get_search_operation_assets(ApiType.SQL, SearchIntent.FILTER)

    assert assets.system_prompt == get_sql_search_filter_system_prompt
    assert assets.docs_path == "sql/40-search.adoc"


def test_get_operation_assets_selects_complete_scim_delete_assets():
    assets = get_operation_assets("delete", ApiType.SCIM)

    assert assets.system_prompt == get_scim_delete_system_prompt
    assert assets.docs_path == "scim/70-delete.adoc"


def test_native_schema_assets_keep_rest_and_scim_prompts_separate():
    rest_assets = get_operation_assets("native_schema", ApiType.REST)
    scim_assets = get_operation_assets("native_schema", ApiType.SCIM)

    assert rest_assets.system_prompt == get_native_schema_system_prompt
    assert "SCIM" not in rest_assets.system_prompt
    assert scim_assets.system_prompt == get_scim_native_schema_system_prompt
    assert "SCIM CONTRACT CONTEXT RULES" in scim_assets.system_prompt


def test_get_operation_assets_rejects_sql_authorization_until_supported():
    with pytest.raises(ValueError, match="authorization"):
        get_operation_assets("authorization", ApiType.SQL)
