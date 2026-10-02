# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from src.jobs.result_envelope import SESSION_COMPANION_OUTPUTS_KEY, strip_internal_result_fields


def test_public_job_result_omits_session_companion_outputs() -> None:
    internal = {
        "result": {"relations": []},
        SESSION_COMPANION_OUTPUTS_KEY: {"relationsAnalysisOutput": {"pairs": []}},
    }

    assert strip_internal_result_fields(internal) == {"result": {"relations": []}}
    assert SESSION_COMPANION_OUTPUTS_KEY in internal
