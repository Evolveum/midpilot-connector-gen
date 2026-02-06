#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

import logging
import re
import uuid
from typing import Any, Dict, List, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import config
from ..modules.digester.router import DEFAULT_CRITERIA
from ..modules.digester.schema import BaseAPIEndpoint, InfoMetadata
from .chunk_filter.filter import filter_documentation_items
from .database.repositories.session_repository import SessionRepository

logger = logging.getLogger(__name__)

# A good question is, should we calculate metadata only from new chunks added since last calculation
# and then merge them with existing metadata?
# Or should we always recalculate from all chunks?
# For now, we will calculate from only new chunks and merge with existing metadata.
# def join_metadata(generated: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
#     """
#     Joins two metadata dictionaries, preferring values from 'current' when there is a conflict.
#     inputs:
#         generated: Dict[str, Any] - the generated metadata
#         current: Dict[str, Any] - the current metadata
#     outputs:
#         joined: Dict[str, Any] - the joined metadata
#     """
#     joined = current.copy()
#     for key, value in generated.items():
#         if key not in joined or not joined[key] or joined[key] == "uncertain" or joined[key] == "not-found":
#             joined[key] = value
#         else:
#             if isinstance(joined[key], list) and isinstance(value, list):
#                 joined[key] = list(set(joined[key] + value))
#             elif isinstance(joined[key], dict) and isinstance(value, dict):
#                 joined[key] = join_metadata(joined[key], value)
#     return joined


async def generate_metadata_from_doc_items(session_id: uuid.UUID, db: AsyncSession) -> None:
    """
    Generate metadata dictionary from documentation items for a given session ID.
    inputs:
        session_id: uuid.UUID - the session ID to generate metadata for
    outputs:
        nothing, saves metadata to the session as metadataOutput
    """
    repo = SessionRepository(db)
    session_data = await repo.get_session_data(session_id) or {}
    discovery_input = session_data.get("discoveryInput", {})
    scrape_input = session_data.get("scrapeInput", {})
    # raw_items = await doc_repo.get_documentation_items_by_session(session_id)
    # if not raw_items:
    #     raise ValueError(f"Session with ID {session_id} has no documentation items stored.")

    # doc_items: List[Dict[str, Any]] = []
    # for item in raw_items:
    #     doc_items.append(
    #         {
    #             "uuid": item.get("id"),
    #             "pageId": item.get("pageId"),
    #             "source": item.get("source"),
    #             "url": item.get("url"),
    #             "summary": item.get("summary"),
    #             "content": item.get("content", ""),
    #             "@metadata": item.get("metadata", {}) or {},
    #         }
    #     )

    doc_items = await filter_documentation_items(DEFAULT_CRITERIA, session_id, db=db)

    synonyms = config.scrape_and_process.latest_version_synonyms

    app_version = discovery_input.get("applicationVersion") or scrape_input.get("applicationVersion") or "unknown"
    is_latest_version = app_version.lower().strip() in synonyms

    version_distribution: Dict[str, int] = {}
    total_items = len(doc_items)
    found_app_version = ""
    latest_numbered_version = None
    for item in doc_items:
        metadata = item.get("@metadata", {})
        version = metadata.get("application_version", "unknown")
        if version in synonyms:
            version = synonyms[0]
        if version is None:
            version = "unknown"
        if re.search(r"\d", version):
            version = re.sub(r"[^\d.]", "", version)
        version_distribution[version] = version_distribution.get(version, 0) + 1

    curr_version_items: List[Dict[str, Any]] = []

    if version_distribution.get("unknown", 0) < total_items * config.scrape_and_process.unknown_version_threshold:
        if is_latest_version:
            found_app_version = app_version
            # We need to filter out also versions without "." as those can be anything like api version etc"
            numbered_versions = [
                v for v in version_distribution.keys() if v != "unknown" and "." in v and re.match(r"^\d+(\.\d+)+$", v)
            ]
            if numbered_versions:
                latest_numbered_version = max(numbered_versions, key=lambda v: tuple(map(int, v.split("."))))
            curr_version_items = [
                item
                for item in doc_items
                if item.get("@metadata", {}).get("application_version") is None
                or item.get("@metadata", {}).get("application_version") == any(synonyms)
                or re.sub(r"[^\d.]", "", item.get("@metadata", {}).get("application_version"))
                == latest_numbered_version
            ]
        else:
            current_app_version_formalized = re.sub(r"[^\d.]", "", app_version)
            if current_app_version_formalized in version_distribution:
                found_app_version = app_version
                curr_version_items = [
                    item
                    for item in doc_items
                    if item.get("@metadata", {}).get("application_version", "") is None
                    or re.sub(r"[^\d.]", "", item.get("@metadata", {}).get("application_version", ""))
                    == current_app_version_formalized
                ]
            else:
                found_app_version = "not-found"
                curr_version_items = doc_items
    else:
        found_app_version = "uncertain"
        curr_version_items = doc_items

    api_type_distribution: Dict[str, int] = {}
    api_version_distribution: Dict[str, int] = {}
    base_api_endpoints_url_distribution: Dict[str, int] = {}
    base_api_endpoints_type_distribution: Dict[Tuple[str, str], int] = {}
    application_name_distribution: Dict[str, int] = {}
    for item in curr_version_items:
        metadata = item.get("@metadata", {})
        api_types = metadata.get("api_type", [])
        api_version = metadata.get("api_version", None)
        base_api_endpoints = metadata.get("base_api_endpoint", [])
        chunk_application_name = metadata.get("application_name", None)
        if base_api_endpoints:
            for endpoint in base_api_endpoints:
                uri = endpoint.get("uri", "").lower().strip()
                type_ = endpoint.get("type", "").lower().strip()
                if uri:
                    base_api_endpoints_url_distribution[uri] = base_api_endpoints_url_distribution.get(uri, 0) + 1
                if type_:
                    base_api_endpoints_type_distribution[(uri, type_)] = (
                        base_api_endpoints_type_distribution.get((uri, type_), 0) + 1
                    )
        if api_version:
            api_version_distribution[api_version] = api_version_distribution.get(api_version, 0) + 1
        if api_types:
            for api_type in api_types:
                api_type_distribution[api_type.lower().strip()] = (
                    api_type_distribution.get(api_type.lower().strip(), 0) + 1
                )
        if chunk_application_name:
            application_name_distribution[chunk_application_name] = (
                application_name_distribution.get(chunk_application_name, 0) + 1
            )

    found_api_types: List[str] = []
    for api_type in api_type_distribution.keys():
        if api_type_distribution[api_type] > total_items * config.scrape_and_process.metadata_uncertainty_threshold:
            found_api_types.append(api_type)

    found_api_version = ""
    if api_version_distribution:
        api_version = max(api_version_distribution.keys(), key=lambda v: api_version_distribution[v])
        if (
            api_version_distribution[api_version]
            > total_items * config.scrape_and_process.metadata_uncertainty_threshold
        ):
            found_api_version = api_version
        else:
            found_api_version = "uncertain"

    found_application_name = ""
    if application_name_distribution:
        application_name = max(application_name_distribution.keys(), key=lambda v: application_name_distribution[v])
        if (
            application_name_distribution[application_name]
            > total_items * config.scrape_and_process.metadata_uncertainty_threshold
        ):
            found_application_name = application_name
        else:
            found_application_name = "uncertain"

    found_base_api_endpoints: List[BaseAPIEndpoint] = []
    for uri in base_api_endpoints_url_distribution.keys():
        count = base_api_endpoints_url_distribution[uri]
        if count > total_items * config.scrape_and_process.metadata_uncertainty_threshold:
            types_for_uri = {
                type_: base_api_endpoints_type_distribution.get((uri, type_), 0)
                for (u, type_) in base_api_endpoints_type_distribution.keys()
                if u == uri
            }
            if types_for_uri:
                best_type = max(types_for_uri.keys(), key=lambda t: types_for_uri[t])
                found_base_api_endpoints.append(BaseAPIEndpoint(uri=uri, type=best_type))

    raw_metadata_output = {
        "name": found_application_name,
        "application_version": found_app_version,
        "api_version": found_api_version,
        "api_type": found_api_types,
        "base_api_endpoint": found_base_api_endpoints,
    }

    validated_metadata_output: Dict[str, Any] = {}

    try:
        info_metadata = InfoMetadata(**raw_metadata_output)
        logger.info("[Metadata] Generated InfoMetadata: %s", info_metadata)
        validated_metadata_output = info_metadata.model_dump()
        logger.info("[Metadata] Validated InfoMetadata: %s", validated_metadata_output)

    except Exception as e:
        logger.error("[Metadata] Error generating InfoMetadata: %s", e)
        return

    # if session_data.get("metadataOutput", {}):
    #     logger.info("[Metadata] Existing metadataOutput found, joining with generated metadata")
    #     final_metadata = join_metadata(session_data["metadataOutput"], validated_metadata_output)
    #     await repo.update_session(
    #         session_id,
    #         {
    #             "metadataOutput": final_metadata
    #         },
    #     )
    # else:
    #     logger.info("[Metadata] No existing metadataOutput found, saving generated metadata")
    await repo.update_session(
        session_id,
        {"metadataOutput": validated_metadata_output},
    )

    await db.commit()

    logger.info("[Metadata] Generated version distribution: %s", version_distribution)
    logger.info("[Metadata] Latest numbered version: %s", latest_numbered_version)
    logger.info("[Metadata] Generated API type distribution: %s", api_type_distribution)
    logger.info("[Metadata] Generated API version distribution: %s", api_version_distribution)
    logger.info("[Metadata] Generated base API endpoints URL distribution: %s", base_api_endpoints_url_distribution)
    logger.info("[Metadata] Generated base API endpoints type distribution: %s", base_api_endpoints_type_distribution)
    logger.info("[Metadata] Generated application name distribution: %s", application_name_distribution)
