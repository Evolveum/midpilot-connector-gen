# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.config import config
from src.integrations.web.page_links import PageLink
from src.modules.scrape.core.assessment import assess_page, select_links, verify_assessment
from src.modules.scrape.schema import LinkSelection, PageAssessment

PAGE = """# Jira Cloud platform REST API

This reference documents **Jira Cloud** version `3` of the REST API.
"""


def _last_call(mock: AsyncMock) -> Any:
    assert mock.await_args is not None
    return mock.await_args


def _output(**overrides: Any) -> PageAssessment:
    return PageAssessment.model_validate(
        {"keep": True, "role": "api_reference", "product_match": "yes", "version_match": "yes", **overrides}
    )


@pytest.mark.parametrize(
    ("claimed", "evidence", "verdict", "kept_evidence"),
    [
        ("no", "Jira Cloud platform REST API", "no", "Jira Cloud platform REST API"),
        ("no", "documents Jira Cloud version 3", "no", "documents Jira Cloud version 3"),  # markdown marks ignored
        ("no", "Confluence Cloud REST API", "unclear", None),  # not on the page
        ("no", "", "unclear", None),
        ("yes", "Bitbucket", "yes", None),  # an unverifiable quote is not kept
    ],
)
def test_product_mismatch_needs_a_quote_from_the_page(claimed, evidence, verdict, kept_evidence) -> None:
    assessment = verify_assessment(
        _output(product_match=claimed, product_evidence=evidence), PAGE, url="u", version_assessed=True
    )

    assert (assessment.product_match, assessment.product_evidence) == (verdict, kept_evidence)


def test_version_is_unclear_when_the_scrape_targets_no_version() -> None:
    output = _output(version_match="no", version_evidence="version `3`")

    assert verify_assessment(output, PAGE, url="u", version_assessed=True).version_match == "no"
    assessment = verify_assessment(output, PAGE, url="u", version_assessed=False)
    assert (assessment.version_match, assessment.version_evidence) == ("unclear", None)


def test_assessment_output_is_parsed_leniently() -> None:
    output = PageAssessment.model_validate(
        {
            "keep": "false",
            "role": "Glossary",
            "covers": ["Users", "weather", "users"],
            "mentioned_object_classes": ["User", 7, " Group ", "User"],
            "product_match": "Probably",
            "product_evidence": None,
            "version_match": "NO",
        }
    )

    assert (output.keep, output.role, output.covers) == (False, "other", ["users"])
    assert output.mentioned_object_classes == ["User", "Group"]
    assert (output.product_match, output.product_evidence, output.version_match) == ("unclear", "", "no")
    assert LinkSelection.model_validate({"selected": [2, "3", "x", True, None]}).selected == [2, 3]


@pytest.mark.asyncio
async def test_assessment_reads_the_page_cut_to_the_token_limit(monkeypatch) -> None:
    monkeypatch.setattr(config.scrape_and_process, "assessment_content_tokens", 5)
    invoke = AsyncMock(return_value=_output(keep=False, mentioned_object_classes=["Issue"]))
    with patch("src.modules.scrape.core.assessment._invoke", invoke):
        keep, assessment = await assess_page(url="u", title="Jira", content=PAGE, app="Jira", version=None)

    user_message = _last_call(invoke).args[1]
    assert "# Jira Cloud platform" in user_message and "version `3`" not in user_message
    assert keep is False and assessment.object_classes == ["Issue"]
    assert _last_call(invoke).kwargs["run_name"] == "Scrape:Pages:Assess"


@pytest.mark.asyncio
async def test_links_are_selected_by_their_index_in_the_offered_list() -> None:
    links = [PageLink(f"https://docs.example.test/{name}", name) for name in ("users", "groups", "pricing")]
    invoke = AsyncMock(return_value=LinkSelection(selected=[1, 0, 1, 7]))
    with patch("src.modules.scrape.core.assessment._invoke", invoke):
        selected = await select_links(links, app="Example", version="2.0")

    assert selected == [links[1], links[0]]
    user_message = _last_call(invoke).args[1]
    assert "0. users | https://docs.example.test/users" in user_message
    assert "2. pricing | https://docs.example.test/pricing" in user_message


@pytest.mark.asyncio
async def test_a_mismatch_may_be_quoted_from_the_page_title() -> None:
    output = _output(product_match="no", product_evidence="Confluence Cloud REST API")
    with patch("src.modules.scrape.core.assessment._invoke", AsyncMock(return_value=output)):
        _, assessment = await assess_page(
            url="u", title="Confluence Cloud REST API", content=PAGE, app="Jira", version=None
        )

    assert (assessment.product_match, assessment.product_evidence) == ("no", "Confluence Cloud REST API")
