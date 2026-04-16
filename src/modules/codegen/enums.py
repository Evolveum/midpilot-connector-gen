# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from enum import StrEnum


class SearchIntent(StrEnum):
    ALL = "all"
    FILTER = "filter"
    ID = "id"
