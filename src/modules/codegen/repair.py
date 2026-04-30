# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
from typing import Any, Dict, Optional

from src.modules.codegen.prompts.repair_prompts import REPAIR_SYSTEM_SUFFIX, REPAIR_USER_SUFFIX
from src.modules.codegen.schema import CodegenRepairContext


def build_repair_prompt_vars(repair_context: Optional[CodegenRepairContext]) -> Dict[str, Any]:
    if repair_context is None:
        return {
            "repair_system_suffix": "",
            "repair_user_suffix": "",
        }

    return {
        "repair_system_suffix": REPAIR_SYSTEM_SUFFIX,
        "repair_user_suffix": REPAIR_USER_SUFFIX.format(
            current_script=repair_context.current_script or "",
            midpoint_errors_json=json.dumps(repair_context.midpoint_errors, ensure_ascii=False),
        ),
    }


def get_repair_initial_result(
    *,
    repair_context: Optional[CodegenRepairContext],
    fallback_result: str,
) -> str:
    if repair_context is not None and repair_context.current_script:
        return repair_context.current_script
    return fallback_result
