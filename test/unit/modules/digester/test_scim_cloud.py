# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch

import pytest

from src.modules.digester.apitype.scim_cloud import (
    ScimCloudImplementation,
    lookup_scim_support,
    match_registry,
    parse_implementations,
)

# scim.cloud files are JS (const wrapper) and contain JS-style trailing commas.
_RAW_V2 = """const scim_v2_implementations = {
    "implementations": [
        {
            "project_name": "Slack SCIM",
            "client": "No",
            "server": "Yes",
            "open_source": "No",
            "developer": "Slack",
            "link": "https://api.slack.com/scim"
        },
        {
            "project_name": "Okta Provisioning",
            "client": "No",
            "server": "Yes",
            "open_source": "No",
            "developer": "Okta",
            "link": "https://okta.com",
        },
    ],
};
"""


def _registry():
    return [
        ScimCloudImplementation(project_name="Slack SCIM", developer="Slack", server="Yes", scim_version="2.0"),
        ScimCloudImplementation(project_name="Slack SCIM", developer="Slack", server="Yes", scim_version="1.1"),
        ScimCloudImplementation(project_name="Okta Provisioning", developer="Okta", server="Yes", scim_version="2.0"),
        ScimCloudImplementation(
            project_name="Azure Active Directory SCIM Provisioning",
            developer="Microsoft",
            server="Yes",
            scim_version="2.0",
        ),
        ScimCloudImplementation(
            project_name="SailPoint IdentityNow", developer="SailPoint", server="Yes", scim_version="2.0"
        ),
    ]


# ==================== PARSING ====================
def test_parse_implementations_strips_js_wrapper_and_trailing_commas():
    impls = parse_implementations(_RAW_V2, "2.0")

    assert len(impls) == 2
    assert impls[0].project_name == "Slack SCIM"
    assert impls[0].developer == "Slack"
    assert all(impl.scim_version == "2.0" for impl in impls)


# ==================== MATCHING ====================
def test_match_exact_name():
    match = match_registry("Slack SCIM", _registry())
    assert match.matched is True
    assert match.project_name == "Slack SCIM"
    assert match.matched_field == "project_name"
    assert match.scim_versions == ["1.1", "2.0"]


def test_match_developer_name():
    match = match_registry("okta", _registry())
    assert match.matched is True
    assert match.project_name == "Okta Provisioning"
    assert match.matched_field == "developer"


def test_match_query_subset_of_registry_name():
    # User typed a shorter/cleaner name than the verbose registry entry.
    match = match_registry("Slack", _registry())
    assert match.matched is True
    assert match.project_name == "Slack SCIM"


def test_match_registry_name_subset_of_longer_query():
    match = match_registry("SailPoint IdentityNow Cloud Connector", _registry())
    assert match.matched is True
    assert match.project_name == "SailPoint IdentityNow"


def test_generic_vendor_token_does_not_cause_false_positive():
    # "Microsoft" alone (developer) must not match unrelated Microsoft products.
    assert match_registry("Microsoft SQL Server", _registry()).matched is False


def test_unknown_application_does_not_match():
    assert match_registry("Totally Made Up App 9000", _registry()).matched is False


def test_blank_query_does_not_match():
    assert match_registry("", _registry()).matched is False


def test_client_only_entries_do_not_match():
    registry = [
        ScimCloudImplementation(
            project_name="Client Only App",
            developer="Client Vendor",
            client="Yes",
            server="No",
            scim_version="2.0",
        ),
        ScimCloudImplementation(
            project_name="Boolean Client Only",
            developer="Boolean Vendor",
            client=True,
            server=False,
            scim_version="2.0",
        ),
    ]

    assert match_registry("Client Only App", registry).matched is False
    assert match_registry("Boolean Client Only", registry).matched is False


def test_client_only_versions_are_not_collected():
    registry = [
        ScimCloudImplementation(
            project_name="Slack SCIM",
            developer="Slack",
            client="Yes",
            server="No",
            scim_version="1.1",
        ),
        ScimCloudImplementation(
            project_name="Slack SCIM",
            developer="Slack",
            client="No",
            server="Yes",
            scim_version="2.0",
        ),
    ]

    match = match_registry("Slack", registry)

    assert match.matched is True
    assert match.scim_versions == ["2.0"]


# ==================== LOOKUP ====================
@pytest.mark.asyncio
async def test_lookup_returns_match_from_registry():
    with patch(
        "src.modules.digester.apitype.scim_cloud.get_registry",
        new_callable=AsyncMock,
        return_value=_registry(),
    ):
        match = await lookup_scim_support("Slack")
    assert match.matched is True
    assert match.project_name == "Slack SCIM"


@pytest.mark.asyncio
async def test_lookup_empty_name_skips_registry():
    with patch("src.modules.digester.apitype.scim_cloud.get_registry", new_callable=AsyncMock) as mock_registry:
        match = await lookup_scim_support("   ")
    assert match.matched is False
    mock_registry.assert_not_awaited()


@pytest.mark.asyncio
async def test_lookup_registry_failure_is_graceful():
    with patch(
        "src.modules.digester.apitype.scim_cloud.get_registry",
        new_callable=AsyncMock,
        side_effect=RuntimeError("network down"),
    ):
        match = await lookup_scim_support("Slack")
    assert match.matched is False
