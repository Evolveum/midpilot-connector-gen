# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Suite-wide safety net: no test may reach a real LLM or the tracing backend.

Applied at the test root rather than per subtree, so a test that moves between
``unit`` and ``integration`` cannot silently lose the guard.
"""

import pytest

patch = pytest.MonkeyPatch()

# Force invalid LLM options so an unmocked call fails loudly instead of billing a real model.
patch.setenv("LLM__OPENAI_API_KEY", "invalid")
patch.setenv("LLM__OPENAI_API_BASE", "invalid")
patch.setenv("LLM__MODEL_NAME", "invalid")
# Tracing stays off: test runs must not appear in the Langfuse project.
patch.setenv("LANGFUSE__TRACING_ENABLED", "false")
