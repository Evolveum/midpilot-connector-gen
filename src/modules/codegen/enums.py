# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from enum import StrEnum


class SearchIntent(StrEnum):
    ALL = "all"
    FILTER = "filter"
    ID = "id"


_SEARCH_INTENT_SUFFIX: dict[SearchIntent, str] = {
    SearchIntent.ALL: "All",
    SearchIntent.FILTER: "Filter",
    SearchIntent.ID: "Id",
}


def build_search_operation_key(object_class: str, intent: SearchIntent | str) -> str:
    normalized_intent = SearchIntent(intent) if isinstance(intent, str) else intent
    return f"{object_class}Search{_SEARCH_INTENT_SUFFIX[normalized_intent]}"
