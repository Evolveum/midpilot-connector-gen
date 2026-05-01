# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from src.common.utils.normalize import normalize_input
from src.modules.discovery.schema import CandidateLinksInput
from src.modules.scrape.schema import ScrapeRequest


def test_normalize_input_ignores_skip_cache_for_job_identity() -> None:
    assert normalize_input({"applicationName": "Demo", "skipCache": True}) == {"applicationName": "Demo"}
    assert normalize_input({"applicationName": "Demo", "skipCache": False}) == {"applicationName": "Demo"}


def test_cache_control_defaults_to_reuse_for_request_models() -> None:
    discovery_input = CandidateLinksInput(application_name="Demo")
    scrape_input = ScrapeRequest(starter_links=["https://example.com/docs"], application_name="Demo")

    assert discovery_input.skip_cache is False
    assert discovery_input.model_dump(by_alias=True)["skipCache"] is False
    assert scrape_input.skip_cache is False
    assert scrape_input.model_dump(by_alias=True)["skipCache"] is False
