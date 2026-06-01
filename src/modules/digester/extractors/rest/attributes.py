# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from uuid import UUID

from langchain_core.output_parsers import BaseOutputParser, PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

from src.common.enums import JobStage
from src.common.jobs import (
    update_job_progress,
)
from src.common.langfuse import langfuse_handler
from src.common.llm import get_default_llm, make_basic_chain
from src.config import config
from src.modules.digester.prompts.rest.attributes_prompts import (
    attribute_deduplication_system_prompt,
    attribute_deduplication_user_prompt,
    get_attribute_discovery_system_prompt,
    get_attribute_discovery_user_prompt,
    get_build_boolean_flags_from_sequences_system_prompt,
    get_build_boolean_flags_from_sequences_user_prompt,
    get_build_type_format_from_sequences_system_prompt,
    get_build_type_format_from_sequences_user_prompt,
    get_consolidate_attributes_system_prompt,
    get_consolidate_attributes_user_prompt,
)
from src.modules.digester.schema import (
    AttributeBooleanFlagsBuildResponse,
    AttributeBuildResponse,
    AttributeDedupResponse,
    AttributeDiscoveryResponse,
    AttributeInfoRest,
    AttributeProcessingInfo,
    AttributeResponse,
    AttributeTypeFormatBuildResponse,
    DiscoveryAttribute,
    DocSequenceItem,
)
from src.modules.digester.utils.attribute_filters import (
    filter_ignored_attributes,
    normalize_readability_flags,
)
from src.modules.digester.utils.chunk_extraction import extract_single_chunk
from src.modules.digester.utils.llm_execution import invoke_llm, run_chunks_concurrently
from src.modules.digester.utils.merges import merge_attribute_candidates

logger = logging.getLogger(__name__)

TYPE_FORMAT_FIELDS = ("type", "format")
BOOLEAN_FLAG_FIELDS = ("mandatory", "updatable", "creatable", "readable", "multivalue", "returnedByDefault")
FINAL_ATTRIBUTE_FIELDS = (*TYPE_FORMAT_FIELDS, "description", *BOOLEAN_FLAG_FIELDS)


def _format_attributes_as_table(attributes: Dict[str, AttributeInfoRest]) -> str:
    """Format consolidated attributes as a fixed-width ASCII table for logging/display."""
    if not attributes:
        return "No attributes extracted."

    columns: List[Tuple[str, int]] = [
        ("name", 18),
        ("description", 56),
        ("type", 12),
        ("format", 12),
        ("mandatory", 10),
        ("updatable", 10),
        ("creatable", 10),
        ("readable", 10),
        ("multivalue", 11),
        ("returnedByDefault", 18),
    ]

    def _normalize_cell(value: Any, width: int) -> str:
        if value is None:
            text = "-"
        else:
            text = str(value)

        # Keep one-line cells and avoid table-breaking characters.
        text = text.replace("|", "/")
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            text = "-"

        if len(text) > width:
            text = text[: max(width - 3, 1)].rstrip() + "..."
        return text.ljust(width)

    def _bool_or_dash(value: Optional[bool]) -> str:
        return "-" if value is None else str(value).lower()

    header = "| " + " | ".join(name.ljust(width) for name, width in columns) + " |"
    separator = "+-" + "-+-".join("-" * width for _, width in columns) + "-+"

    rows: List[str] = []
    for attr_name, attr_info in sorted(attributes.items()):
        values = [
            attr_name,
            attr_info.description,
            attr_info.type,
            attr_info.format,
            _bool_or_dash(attr_info.mandatory),
            _bool_or_dash(attr_info.updatable),
            _bool_or_dash(attr_info.creatable),
            _bool_or_dash(attr_info.readable),
            _bool_or_dash(attr_info.multivalue),
            _bool_or_dash(attr_info.returnedByDefault),
        ]
        row = (
            "| "
            + " | ".join(_normalize_cell(value, width) for value, (_, width) in zip(values, columns, strict=True))
            + " |"
        )
        rows.append(row)

    return "\n".join([separator, header, separator, *rows, separator])


def _build_dedupe_chain() -> Any:
    """
    Build the LLM chain used to resolve attribute duplicates across chunks.
    """
    parser: BaseOutputParser = PydanticOutputParser(pydantic_object=AttributeDedupResponse)
    llm = get_default_llm()
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", attribute_deduplication_system_prompt + "\n\n{format_instructions}"),
            ("human", attribute_deduplication_user_prompt),
        ]
    ).partial(format_instructions=parser.get_format_instructions())
    return make_basic_chain(prompt, llm, parser)


def _build_type_format_chain() -> Any:
    """
    Build the LLM chain used to enrich attribute type and format from sequences.
    """
    parser: PydanticOutputParser[AttributeTypeFormatBuildResponse] = PydanticOutputParser(
        pydantic_object=AttributeTypeFormatBuildResponse
    )
    llm = get_default_llm()
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", get_build_type_format_from_sequences_system_prompt + "\n\n{format_instructions}"),
            ("user", get_build_type_format_from_sequences_user_prompt),
        ]
    ).partial(format_instructions=parser.get_format_instructions())
    return make_basic_chain(prompt, llm, parser)


def _build_boolean_flags_chain() -> Any:
    """
    Build the LLM chain used to enrich boolean attribute flags from sequences.
    """
    parser: PydanticOutputParser[AttributeBooleanFlagsBuildResponse] = PydanticOutputParser(
        pydantic_object=AttributeBooleanFlagsBuildResponse
    )
    llm = get_default_llm()
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", get_build_boolean_flags_from_sequences_system_prompt + "\n\n{format_instructions}"),
            ("user", get_build_boolean_flags_from_sequences_user_prompt),
        ]
    ).partial(format_instructions=parser.get_format_instructions())
    return make_basic_chain(prompt, llm, parser)


def _build_consolidation_chain() -> Any:
    """
    Build the LLM chain used for final consolidation of attributes.
    """
    parser: PydanticOutputParser[AttributeBuildResponse] = PydanticOutputParser(pydantic_object=AttributeBuildResponse)
    llm = get_default_llm()
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", get_consolidate_attributes_system_prompt + "\n\n{format_instructions}"),
            ("user", get_consolidate_attributes_user_prompt),
        ]
    ).partial(format_instructions=parser.get_format_instructions())
    return make_basic_chain(prompt, llm, parser)


def _non_null_fields(attr: AttributeProcessingInfo, fields: Tuple[str, ...]) -> Dict[str, Any]:
    return {field: getattr(attr, field) for field in fields if getattr(attr, field, None) is not None}


def _attribute_identity_payload(attr: AttributeProcessingInfo) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"name": attr.name}
    if attr.description is not None:
        payload["description"] = attr.description
    return payload


def _sequence_evidence_payload(sequences: Sequence[DocSequenceItem]) -> List[Dict[str, str]]:
    return [
        {
            "start_sequence": seq.start_sequence,
            "end_sequence": seq.end_sequence,
            "text": getattr(seq, "text", ""),
        }
        for seq in sequences
    ]


def _type_format_context(attr: AttributeProcessingInfo, sequences: Sequence[DocSequenceItem]) -> Dict[str, Any]:
    return {
        **_attribute_identity_payload(attr),
        "current_type_format": _non_null_fields(attr, TYPE_FORMAT_FIELDS),
        "evidence": _sequence_evidence_payload(sequences),
    }


def _boolean_flags_context(attr: AttributeProcessingInfo, sequences: Sequence[DocSequenceItem]) -> Dict[str, Any]:
    return {
        **_attribute_identity_payload(attr),
        "type_format": _non_null_fields(attr, TYPE_FORMAT_FIELDS),
        "current_boolean_flags": _non_null_fields(attr, BOOLEAN_FLAG_FIELDS),
        "evidence": _sequence_evidence_payload(sequences),
    }


def _final_consolidation_context(attr: AttributeProcessingInfo, sequences: Sequence[DocSequenceItem]) -> Dict[str, Any]:
    return {
        **_attribute_identity_payload(attr),
        "known_values": _non_null_fields(attr, FINAL_ATTRIBUTE_FIELDS),
        "evidence": _sequence_evidence_payload(sequences),
    }


async def _build_attr_from_sequences(
    chain: Any,
    object_class: str,
    attr: AttributeProcessingInfo,
    use_steps: bool,
    fields_to_update: List[str],
    log_stage: str,
    response_model: type[BaseModel],
    context_builder: Callable[[AttributeProcessingInfo, Sequence[DocSequenceItem]], Dict[str, Any]],
) -> AttributeProcessingInfo | None:
    """
    Calls llm on existing AttributeProcessingInfo object with sequences, optionally in steps.
    Primary function is to fill missing details in the AttributeInfoRest object based on the sequences provided.
    Secondary is to fix issues in already existing details, type, description, etc. based on the sequences.

    Args:
        chain: LLM chain to use for extraction
        object_class: Name of the object class
        attr: AttributeProcessingInfo object containing existing attribute information and sequences
        use_steps: Whether to use steps when processing sequences or process all at once (if false, the whole sequence list will be used in one call)
        fields_to_update: Attribute fields this chain is allowed to update
        log_stage: Human-readable stage name for logs
        response_model: Pydantic model expected from this extraction stage
    Returns:
        AttributeProcessingInfo object with filled and potentially corrected attribute information
    """

    seq_step = len(attr.relevant_sequences) if not use_steps else config.digester.build_from_sequences_step_size

    for begin in range(0, len(attr.relevant_sequences), seq_step):
        end = min(begin + seq_step, len(attr.relevant_sequences))
        sequence_batch = attr.relevant_sequences[begin:end]
        attribute_context = json.dumps(context_builder(attr, sequence_batch), ensure_ascii=False, indent=2)
        logger.debug(
            "[Digester:Attributes] Phase=%s attribute=%s sequences=%s-%s/%s",
            log_stage,
            attr.name,
            begin,
            end,
            len(attr.relevant_sequences),
        )
        try:
            result = await invoke_llm(
                chain,
                {
                    "object_class": object_class,
                    "attribute_context": attribute_context,
                },
                config={"callbacks": [langfuse_handler]},
            )

            if isinstance(result, response_model):
                parsed = result
            elif isinstance(result, dict):
                parsed = response_model.model_validate(result)
            else:
                content = getattr(result, "content", None)
                if isinstance(content, str) and content.strip():
                    parsed = response_model.model_validate(json.loads(content))
                else:
                    return None

            for param in fields_to_update:
                value = getattr(parsed, param, None)
                if value is not None:
                    setattr(attr, param, value)

        except Exception as exc:
            logger.warning(
                "[Digester:Attributes] %s from sequences failed for attribute %s: %s, sequences number: %s - %s",
                log_stage,
                attr.name,
                exc,
                begin,
                end,
            )
            pass

    return attr


async def build_attributes_from_sequences(
    attrs: List[AttributeProcessingInfo], object_class: str
) -> List[AttributeProcessingInfo]:
    """
    Run the build-from-sequences chain for each attribute that has relevant sequences, in order to fill missing details and correct existing ones based on the sequences.

    Args:
        attrs: List of AttributeProcessingInfo objects to process
        object_class: Name of the object class for context
    """

    type_format_chain = _build_type_format_chain()
    boolean_flags_chain = _build_boolean_flags_chain()

    logger.info(
        "[Digester:Attributes] Phase=type_format_enrichment object_class=%s attributes=%d",
        object_class,
        len(attrs),
    )
    tasks = [
        _build_attr_from_sequences(
            type_format_chain,
            object_class,
            attr,
            use_steps=True,
            fields_to_update=["type", "format"],
            log_stage="Type/format enrichment",
            response_model=AttributeTypeFormatBuildResponse,
            context_builder=_type_format_context,
        )
        for attr in attrs
        if attr.relevant_sequences
    ]

    type_format_attrs = await asyncio.gather(*tasks)
    filtered_type_format_attrs: List[AttributeProcessingInfo] = [attr for attr in type_format_attrs if attr is not None]

    logger.info(
        "[Digester:Attributes] Phase=boolean_flags_enrichment object_class=%s attributes=%d",
        object_class,
        len(filtered_type_format_attrs),
    )
    flag_tasks = [
        _build_attr_from_sequences(
            boolean_flags_chain,
            object_class,
            attr,
            use_steps=True,
            fields_to_update=["mandatory", "updatable", "creatable", "readable", "multivalue", "returnedByDefault"],
            log_stage="Boolean flag enrichment",
            response_model=AttributeBooleanFlagsBuildResponse,
            context_builder=_boolean_flags_context,
        )
        for attr in filtered_type_format_attrs
        if attr.relevant_sequences
    ]

    all_builded_attrs = await asyncio.gather(*flag_tasks)
    enriched_attrs = [attr for attr in all_builded_attrs if attr is not None]
    logger.info(
        "[Digester:Attributes] Phase=attribute_enrichment_finished object_class=%s attributes=%d",
        object_class,
        len(enriched_attrs),
    )
    return enriched_attrs


async def consolidate_attributes(attrs: List[AttributeProcessingInfo], object_class: str) -> AttributeResponse:
    """
    Final consolidation of attributes - one final LLM call with all of the sequences for each attribute to correct any issues.
    Transforms AttributeProcessingInfo objects into AttributeInfoRest objects and creates an AttributeResponse object for the final output.
    Args:
        attrs: List of AttributeProcessingInfo objects to consolidate
        object_class: Name of the object class for context
    Returns:
        An AttributeResponse object with consolidated and finalized attribute information ready for output
    """

    build_chain = _build_consolidation_chain()

    logger.info(
        "[Digester:Attributes] Phase=final_consolidation object_class=%s attributes=%d",
        object_class,
        len(attrs),
    )
    tasks = []
    for attr in attrs:
        if attr.relevant_sequences:
            tasks.append(
                _build_attr_from_sequences(
                    build_chain,
                    object_class,
                    attr,
                    use_steps=False,
                    fields_to_update=[
                        "type",
                        "format",
                        "description",
                        "mandatory",
                        "updatable",
                        "creatable",
                        "readable",
                        "multivalue",
                        "returnedByDefault",
                    ],
                    log_stage="Final consolidation",
                    response_model=AttributeBuildResponse,
                    context_builder=_final_consolidation_context,
                )
            )
        else:
            logger.warning(
                "[Digester:Attributes] Attribute %s has no relevant sequences; skipping final consolidation", attr.name
            )

    processed_attrs: List[AttributeProcessingInfo | None] = await asyncio.gather(*tasks)

    consolidated_attrs: AttributeResponse = AttributeResponse(attributes={})
    for attr_prc in processed_attrs:
        if attr_prc is None or not attr_prc.name:
            continue
        consolidated_attrs.attributes[attr_prc.name] = AttributeInfoRest(
            type=attr_prc.type,
            format=attr_prc.format,
            description=attr_prc.description,
            mandatory=attr_prc.mandatory,
            updatable=attr_prc.updatable,
            creatable=attr_prc.creatable,
            readable=attr_prc.readable,
            multivalue=attr_prc.multivalue,
            returnedByDefault=attr_prc.returnedByDefault,
            relevant_documentations=attr_prc.relevant_documentations,
            relevant_sequences=[DocSequenceItem(**seq.__dict__) for seq in attr_prc.relevant_sequences],
        )

    logger.info(
        "[Digester:Attributes] Phase=final_consolidation_finished object_class=%s attributes=%d",
        object_class,
        len(consolidated_attrs.attributes),
    )
    return consolidated_attrs


async def extract_attributes(
    chunks: List[str],
    object_class: str,
    job_id: UUID,
    chunk_details: List[str],
    chunk_metadata_map: Optional[Dict[str, Dict[str, Any]]] = None,
    chunk_id_to_doc_id: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Extract object class attributes from document chunks using LLM analysis.

    Processes chunks of text to identify and extract attribute information including
    names, types, descriptions, and metadata for a specific object class. Uses parallel
    processing for efficiency and includes duplicate resolution across chunks.

    Args:
        chunks: List of text chunks to analyze for attribute information
        object_class: Target object class for attribute extraction context
        job_id: UUID for job tracking and progress updates
        chunk_details: List of chunk IDs for each chunk, required
        chunk_metadata_map: Optional metadata mapping for chunk IDs (default: None)
        chunk_id_to_doc_id: Optional mapping of chunk ID to doc ID

    Returns:
        Dict containing:
        - "result": Dict with "attributes" key containing extracted attribute information
        - "relevantDocumentations": List of chunks that contained relevant attribute information
    """
    if not chunk_details:
        logger.error("[Digester:Attributes] chunk_details is required but was empty")
        await update_job_progress(
            job_id, stage=JobStage.failed, message="No chunk details provided, cannot extract attributes"
        )
        return {"result": {"attributes": {}}, "relevantDocumentations": []}

    if len(chunks) != len(chunk_details):
        logger.error(
            "[Digester:Attributes] Length mismatch: %d chunks vs %d chunk_details",
            len(chunks),
            len(chunk_details),
        )
        await update_job_progress(
            job_id, stage=JobStage.failed, message="Chunk length mismatch, cannot extract attributes"
        )
        return {"result": {"attributes": {}}, "relevantDocumentations": []}

    if len(chunk_details) != len(set(chunk_details)):
        logger.error("[Digester:Attributes] Duplicate chunk IDs found in chunk_details")
        await update_job_progress(
            job_id, stage=JobStage.failed, message="Duplicate chunk IDs found, cannot extract attributes"
        )
        return {"result": {"attributes": {}}, "relevantDocumentations": []}

    logger.info(
        "[Digester:Attributes] Processing %d pre-selected chunks for %s (chunk IDs: %s)",
        len(chunks),
        object_class,
        chunk_details,
    )
    chunks_by_id: List[Dict[str, str]] = []

    for chunk_text, chunk_id in zip(chunks, chunk_details):
        chunks_by_id.append({"chunkId": chunk_id, "content": chunk_text})

    total_chunk_ids = len(chunks_by_id)

    await update_job_progress(
        job_id,
        total_processing=total_chunk_ids,
        processing_completed=0,
        message="Processing chunks and try to extract relevant information",
    )

    all_discovery_results: List[DiscoveryAttribute] = []

    async def _extract_for_chunk_id(
        chunk_text: str, job_id_ext: UUID, chunk_id: UUID
    ) -> Tuple[List[DiscoveryAttribute], bool]:
        chunk_metadata = chunk_metadata_map.get(str(chunk_id)) if chunk_metadata_map else None

        def parse_fn(result: AttributeDiscoveryResponse) -> List[DiscoveryAttribute]:
            return result.attributes or []

        per_chunk_results, relevant_data = await extract_single_chunk(
            schema=chunk_text,
            pydantic_model=AttributeDiscoveryResponse,
            system_prompt=get_attribute_discovery_system_prompt,
            user_prompt=get_attribute_discovery_user_prompt,
            parse_fn=parse_fn,
            logger_prefix="[Digester:Attributes] ",
            job_id=job_id_ext,
            chunk_id=chunk_id,
            chunk_metadata=chunk_metadata,
            enabled_sequence_checking=True,
            enable_marker_blending=True,
            extra_llm_attrs={"object_class": object_class},
            min_start_sequence_length=config.digester.min_start_sequence_len_attributes,
            max_start_sequence_length=config.digester.max_start_sequence_len_attributes,
            min_end_sequence_length=config.digester.min_end_sequence_len_attributes,
            max_end_sequence_length=config.digester.max_end_sequence_len_attributes,
        )

        logger.info(
            "[Digester:Attributes] Extraction complete for chunk %s. Found %d attributes.",
            chunk_id,
            len(per_chunk_results),
        )

        return per_chunk_results, relevant_data

    logger.info(
        "[Digester:Attributes] Phase=attribute_discovery object_class=%s chunks=%d",
        object_class,
        total_chunk_ids,
    )
    results = await run_chunks_concurrently(
        chunk_items=chunks_by_id,
        job_id=job_id,
        extractor=_extract_for_chunk_id,
        logger_scope="Digester:Attributes",
    )

    for chunk_results, relevant_data, chunk_id_debug in results:
        logger.debug(
            "[Digester:Attributes] Discovery results for document %s: %d attributes, relevant: %s, whole attributes: %s",
            str(chunk_id_debug),
            len(chunk_results),
            relevant_data,
            chunk_results,
        )
        for res in chunk_results:
            if res:
                all_discovery_results.append(res)

    logger.info(
        "[Digester:Attributes] Phase=attribute_discovery_finished object_class=%s candidates=%d",
        object_class,
        len(all_discovery_results),
    )
    logger.info(
        "[Digester:Attributes] Phase=attribute_deduplication object_class=%s candidates=%d",
        object_class,
        len(all_discovery_results),
    )
    merged_attributes = await merge_attribute_candidates(
        object_class=object_class,
        attribute_objects=all_discovery_results,
        job_id=job_id,
        build_dedup_chain=_build_dedupe_chain,
        chunk_id_doc_id_map=chunk_id_to_doc_id,
    )

    logger.info(
        "[Digester:Attributes] Phase=attribute_deduplication_finished object_class=%s attributes=%d",
        object_class,
        len(merged_attributes),
    )

    logger.debug(
        "[Digester:Attributes] Final merged attributes for %s: %s",
        object_class,
        [attr.name for attr in merged_attributes],
    )

    logger.info(
        "[Digester:Attributes] Phase=attribute_filtering object_class=%s attributes=%d",
        object_class,
        len(merged_attributes),
    )
    attributes_filtered_names = filter_ignored_attributes(merged_attributes)
    filtered_attributes = [attr for attr in merged_attributes if attr.name in attributes_filtered_names]
    removed_attributes = [attr.name for attr in merged_attributes if attr.name not in attributes_filtered_names]
    if removed_attributes:
        logger.info(
            "[Digester:Attributes] Removed %d ignored attributes during postprocessing: %s",
            len(removed_attributes),
            sorted(removed_attributes),
        )

    logger.info(
        "[Digester:Attributes] Phase=attribute_filtering_finished object_class=%s attributes=%d names=%s",
        object_class,
        len(filtered_attributes),
        [attr.name for attr in filtered_attributes],
    )

    logger.debug(
        "[Digester:Attributes] Complete filtered objects: %s",
        json.dumps(
            [
                attr.model_dump(exclude={"relevant_sequences", "relevant_documentations"})
                for attr in filtered_attributes
            ],
            indent=2,
            ensure_ascii=False,
        ),
    )

    logger.info(
        "[Digester:Attributes] Phase=attribute_enrichment object_class=%s attributes=%d",
        object_class,
        len(filtered_attributes),
    )
    builded_attributes = await build_attributes_from_sequences(filtered_attributes, object_class)

    logger.debug(
        "[Digester:Attributes] Attributes after building from sequences: %s",
        json.dumps(
            [attr.model_dump(exclude={"relevant_sequences", "relevant_documentations"}) for attr in builded_attributes],
            indent=2,
            ensure_ascii=False,
        ),
    )

    if not builded_attributes:
        logger.error("[Digester:Attributes] No attributes left after building from sequences, returning empty result")
        await update_job_progress(
            job_id, stage=JobStage.failed, message="Attribute extraction complete with no attributes found"
        )
        return {"result": {"attributes": {}}, "relevantDocumentations": []}

    consolidated_attributes = await consolidate_attributes(builded_attributes, object_class)

    logger.debug(
        "[Digester:Attributes] Attributes after final consolidation: %s",
        json.dumps(
            consolidated_attributes.model_dump(
                exclude={"attributes": {"__all__": {"relevant_documentations", "relevant_sequences"}}}
            ),
            indent=2,
            ensure_ascii=False,
        ),
    )

    if config.digester.attributes_debug_table_log:
        attributes_table = _format_attributes_as_table(consolidated_attributes.attributes)  # type: ignore
        logger.info("[Digester:Attributes] Final attributes table for %s:\n%s", object_class, attributes_table)

    relevant_chunks = []
    seen_chunk_ids = set()
    for attr in consolidated_attributes.attributes.values():
        for chk in attr.relevant_documentations:
            if chk["chunk_id"] not in seen_chunk_ids:
                relevant_chunks.append({"chunkId": chk["chunk_id"], "docId": chk.get("doc_id", "unknown")})
                seen_chunk_ids.add(chk["chunk_id"])

    normalized_attributes = normalize_readability_flags(consolidated_attributes.model_dump()["attributes"])

    await update_job_progress(job_id, stage=JobStage.schema_ready, message="Attribute extraction complete")

    return {"result": {"attributes": normalized_attributes}, "relevantDocumentations": relevant_chunks}
