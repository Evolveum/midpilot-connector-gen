# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""The key vocabulary must reproduce the session-data names already in use."""

from uuid import UUID

import pytest

from src.shared import session_keys
from src.shared.session_keys import (
    JobSessionKeys,
    attributes_keys,
    codegen_operation_keys,
    connector_fix_keys,
    endpoints_keys,
    is_attributes_output_key,
    is_endpoints_output_key,
    job_pointer_key_for_output,
    relation_code_keys,
    upload_job_pointer_key,
)


@pytest.mark.parametrize(
    ("keys", "prefix"),
    [
        (session_keys.DISCOVERY, "discovery"),
        (session_keys.SCRAPE, "scrape"),
        (session_keys.OBJECT_CLASSES, "objectClasses"),
        (session_keys.RELATIONS, "relations"),
        (session_keys.CONNECTIVITY_ENDPOINT, "connectivityEndpoint"),
        (session_keys.AUTH, "auth"),
        (session_keys.METADATA, "metadata"),
        (session_keys.AUTHORIZATION, "authorization"),
        (attributes_keys("user"), "userAttributes"),
        (endpoints_keys("user"), "userEndpoints"),
        (codegen_operation_keys("userSearchAll"), "userSearchAll"),
        (relation_code_keys("membership"), "membershipCode"),
        (connector_fix_keys("user"), "userConnectorFix"),
    ],
)
def test_every_family_keeps_its_established_names(keys: JobSessionKeys, prefix: str):
    assert (keys.input, keys.job_id, keys.output) == (f"{prefix}Input", f"{prefix}JobId", f"{prefix}Output")


def test_object_class_names_are_used_exactly_as_given():
    assert attributes_keys("m_user").output == "m_userAttributesOutput"


def test_upload_pointer_keeps_its_established_name():
    doc_id = UUID("8f1c3a2e-0000-4000-8000-000000000001")
    assert upload_job_pointer_key(doc_id) == f"documentation.processUpload_{doc_id}_job_id"


def test_job_pointer_is_derived_from_the_output_of_the_same_family():
    assert job_pointer_key_for_output(attributes_keys("user").output) == attributes_keys("user").job_id
    assert job_pointer_key_for_output(session_keys.OBJECT_CLASSES.output) == "objectClassesJobId"


@pytest.mark.parametrize("key", ["userAttributesInput", "Output", "objectClassesJobId", ""])
def test_job_pointer_derivation_rejects_keys_outside_the_output_convention(key: str):
    with pytest.raises(ValueError, match="Output convention"):
        job_pointer_key_for_output(key)


def test_a_family_needs_a_prefix():
    with pytest.raises(ValueError):
        JobSessionKeys("")


def test_result_family_predicates():
    assert is_attributes_output_key(attributes_keys("user").output)
    assert not is_attributes_output_key(attributes_keys("user").input)
    assert is_endpoints_output_key(endpoints_keys("group").output)
    assert not is_endpoints_output_key(session_keys.CONNECTIVITY_ENDPOINT.output)
