# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import Any, Dict, Mapping
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.common.chunk_filter.filter import filter_documentation_items
from src.common.enums import ApiType
from src.common.session.session import get_session_documentation
from src.common.utils.session_info_metadata import get_discovery_application_name, get_session_api_types, is_sql_api
from src.config import config
from src.modules.digester.selection.criteria import (
    CONNECTIVITY_ENDPOINT_CRITERIA,
    CONNECTIVITY_ENDPOINT_FALLBACK_CRITERIA,
    DEFAULT_AUTH_CRITERIA,
    DEFAULT_CRITERIA,
    EXTENDED_AUTH_CRITERIA,
    METADATA_CRITERIA,
)

logger = logging.getLogger(__name__)


def _api_type_override_from_payload(input_payload: Mapping[str, Any] | None) -> ApiType | None:
    if not input_payload:
        return None
    api_type = input_payload.get("apiType")
    if isinstance(api_type, ApiType):
        return api_type
    if isinstance(api_type, str):
        try:
            return ApiType(api_type.strip().lower())
        except ValueError:
            logger.warning("[Digester:ObjectClasses] Ignoring unsupported apiType override: %s", api_type)
    return None


async def build_object_class_extraction_input(
    db: AsyncSession,
    session_id: UUID,
    input_payload: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Dynamic input provider for object class extraction.

    SQL object-class extraction needs direct access to schema chunks, which may not
    be categorized as REST/OpenAPI-style API references. REST/SCIM keep the existing
    filtered input to avoid sending broad documentation context to the LLM.
    """
    api_type_override = _api_type_override_from_payload(input_payload)
    is_sql_session = api_type_override == ApiType.SQL
    if api_type_override is None:
        is_sql_session = is_sql_api(await get_session_api_types(session_id))

    if is_sql_session:
        doc_items = await get_session_documentation(session_id, db=db)
        logger.info(
            "[Digester:ObjectClasses] Using all %s documentation chunks for SQL object class extraction",
            len(doc_items),
        )
    else:
        doc_items = await filter_documentation_items(DEFAULT_CRITERIA, session_id, db=db)

    return {
        "sessionInput": {},
        "jobInput": {
            "documentationItems": doc_items,
        },
        "args": (doc_items,),
    }


async def auth_input(db: AsyncSession, session_id: UUID) -> Dict[str, Any]:
    """
    Dynamic input provider for auth extraction job.
    It is important to wait for the documentation to be ready before starting the job.
    input:
        session_id - session ID to retrieve documentation items from
        db - SQLAlchemy AsyncSession
    output:
        dict with:
            args - tuple of documentation items
            sessionInput - dict with documentationItemsCount and totalLength - used for input in session field
            jobInput - dict for job input field
    """
    # Prefer auth-specific chunks, but fall back to the broader default digester set
    # if the static auth filter is too restrictive for the current session metadata.
    doc_items = await filter_documentation_items(DEFAULT_AUTH_CRITERIA, session_id, db=db)
    min_doc_length = config.digester.auth_min_documentation_items
    used_auth_criteria = True
    if len(doc_items) < min_doc_length:
        doc_items = await filter_documentation_items(EXTENDED_AUTH_CRITERIA, session_id, db=db)
        logger.info("[Digester:Auth] Using extended auth criteria")
        used_auth_criteria = False
    return {
        "sessionInput": {},
        "jobInput": {
            "documentationItems": doc_items,
            "usedAuthCriteria": used_auth_criteria,
        },
        "args": (doc_items,),
    }


async def metadata_input(db: AsyncSession, session_id: UUID) -> Dict[str, Any]:
    """
    Dynamic input provider for metadata extraction job.
    It is important to wait for the documentation to be ready before starting the job.
    input:
        session_id - session ID to retrieve documentation items from
        db - SQLAlchemy AsyncSession
    output:
        dict with:
            'args' key containing tuple of documentation items,
            'sessionInput' key with metadata for input in session field,
            'jobInput' key with metadata for input in job field
    """
    doc_items = await filter_documentation_items(METADATA_CRITERIA, session_id, db=db)
    application_name = await get_discovery_application_name(session_id)
    return {
        "sessionInput": {},
        "jobInput": {
            "documentationItems": doc_items,
            "applicationName": application_name,
        },
        "args": (doc_items, application_name),
    }


async def connectivity_endpoint_input(db: AsyncSession, session_id: UUID) -> Dict[str, Any]:
    """
    Dynamic input provider for connectivity endpoint extraction.
    Prefer chunks that already contain endpoint metadata, but fall back to broader API/overview documentation when
    metadata classification is too restrictive.
    """
    doc_items = await filter_documentation_items(CONNECTIVITY_ENDPOINT_CRITERIA, session_id, db=db)
    used_connectivity_endpoint_criteria = True
    if not doc_items:
        doc_items = await filter_documentation_items(CONNECTIVITY_ENDPOINT_FALLBACK_CRITERIA, session_id, db=db)
        used_connectivity_endpoint_criteria = False
        logger.info("[Digester:ConnectivityEndpoint] Using fallback connectivity endpoint criteria")

    return {
        "sessionInput": {},
        "jobInput": {
            "documentationItems": doc_items,
            "usedConnectivityEndpointCriteria": used_connectivity_endpoint_criteria,
        },
        "args": (doc_items,),
    }
