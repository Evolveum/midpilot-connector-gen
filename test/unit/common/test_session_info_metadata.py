# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from src.common.enums import ApiType
from src.common.utils.session_info_metadata import is_sql_api, resolve_session_api_type


def test_resolve_session_api_type_defaults_to_rest():
    assert resolve_session_api_type([]) == ApiType.REST


def test_resolve_session_api_type_prefers_sql():
    assert resolve_session_api_type(["REST", "SQL"]) == ApiType.SQL


def test_resolve_session_api_type_detects_scim_case_insensitively():
    assert resolve_session_api_type([" scim "]) == ApiType.SCIM


def test_is_sql_api_detects_sql_case_insensitively():
    assert is_sql_api([" sql "])
