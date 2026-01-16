#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

import pytest

patch = pytest.MonkeyPatch()

# don't want to call llm from unit tests, therefore forcing invalid llm options
patch.setenv("LLM__OPENAI_API_KEY", "invalid")
patch.setenv("LLM__OPENAI_API_BASE", "invalid")
patch.setenv("LLM__MODEL_NAME", "invalid")
# also don't want to use langfuse tracing
patch.setenv("LANGFUSE__TRACING_ENABLED", "false")
