# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Package final connector code with its format, without changing the artifact."""

import asyncio

from src.modules.codegen.schema import ConnectorCodeOutput
from src.modules.codegen.utils.connector_code_validation import detect_connector_code_format


async def build_connector_code_output(code: str) -> ConnectorCodeOutput:
    """Classify accepted code off the event loop; an empty result has no format."""
    code_format = await asyncio.to_thread(detect_connector_code_format, code) if code.strip() else None
    return {"format": code_format, "code": code}
