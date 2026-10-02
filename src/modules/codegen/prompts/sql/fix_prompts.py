# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""SQL connector-fix prompts with separate physical and ConnID contexts."""

from src.modules.codegen.prompts.fix_prompts import (
    build_connector_fix_system_prompt,
    build_connector_fix_user_prompt,
)
from src.modules.codegen.prompts.sql.shared_context_prompts import (
    SQL_PHYSICAL_PROJECTION_SYSTEM_RULES,
    SQL_PHYSICAL_PROJECTION_USER_SECTION,
)

get_sql_connector_fix_system_prompt = build_connector_fix_system_prompt(SQL_PHYSICAL_PROJECTION_SYSTEM_RULES)
get_sql_connector_fix_user_prompt = build_connector_fix_user_prompt(SQL_PHYSICAL_PROJECTION_USER_SECTION)
