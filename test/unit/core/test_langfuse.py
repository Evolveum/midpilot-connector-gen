# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import pytest
from langchain_core.runnables import RunnableLambda

from src.config import config
from src.core.observability.langfuse import build_llm_config, langfuse_handler


@pytest.mark.asyncio
async def test_named_chain_runs_with_tracing_export_disabled():
    assert config.langfuse.tracing_enabled is False
    llm_config = build_llm_config("Codegen:Search:REST:All:Repair")
    chain = RunnableLambda(lambda value: value)

    assert await chain.ainvoke("result", config=llm_config) == "result"
    assert llm_config["run_name"] == "Codegen:Search:REST:All:Repair"
    assert llm_config["callbacks"] == [langfuse_handler]


@pytest.mark.parametrize("run_name", ["", " ", "\t\n"])
def test_blank_trace_names_are_rejected(run_name):
    with pytest.raises(ValueError, match="run_name must not be blank"):
        build_llm_config(run_name)
