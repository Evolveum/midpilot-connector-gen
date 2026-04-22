import asyncio
import logging
import re
from uuid import UUID

from src.common.database.config import async_session_maker
from src.common.database.repositories.documentation_repository import DocumentationRepository

logger = logging.getLogger(__name__)


async def extract_sequence(
    chunk_id: str, start_pattern: str, end_pattern: str, enable_marker_blending: bool = False, logger_prefix: str = ""
) -> str:
    """
    Extract sequence from text using start and end sequences.

    Args:
        chunk_id: str - chunk ID to fetch the text from the database
        start_pattern: str - the exact opening phrase from the documentation (word-for-word, searchable)
        end_pattern: str - the exact closing phrase from the documentation (word-for-word, searchable)
        enable_marker_blending: bool - whether to enable marker blending
        logger_prefix: str - prefix for logging to identify the context
    """
    text = ""
    async with async_session_maker() as db:
        doc_repo = DocumentationRepository(db)
        doc = await doc_repo.get_documentation_item(UUID(chunk_id))
        text = doc["content"] if doc and "content" in doc else ""

    if not text:
        logger.warning("%sNo text found for chunk ID: %s", logger_prefix, chunk_id)
        return ""

    start_match = await asyncio.to_thread(lambda: re.search(re.escape(start_pattern), text))
    start_index = start_match.start() if start_match else 0
    offset = start_index + len(start_pattern) if not enable_marker_blending else start_index
    end_match = (
        await asyncio.to_thread(lambda: re.search(re.escape(end_pattern), text[offset:])) if start_match else None
    )

    if not start_match or not end_match:
        logger.warning(
            "%sFailed to find valid sequence. Start pattern: %s, End pattern: %s, Text: %s",
            logger_prefix,
            start_pattern,
            end_pattern,
            text,
        )
        return ""

    end_index = offset + end_match.end()
    full_sequence = text[start_match.start() : end_index]
    logger.debug(
        "%sExtracted sequence: %s, Start index: %d, End index: %d",
        logger_prefix,
        full_sequence,
        start_match.start(),
        end_index,
    )
    return full_sequence
