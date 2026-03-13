import logging
import re
from uuid import UUID

from ....common.database.config import async_session_maker
from ....common.database.repositories.documentation_repository import DocumentationRepository

logger = logging.getLogger(__name__)


async def extract_sequence(docId: str, start_pattern: str, end_pattern: str, logger_prefix: str = "") -> str:
    """
    Extract sequence from text using start and end sequences.

    Args:
        docId: str - chunk ID to fetch the text from the database
        start_pattern: str - the exact opening phrase from the documentation (word-for-word, searchable)
        end_pattern: str - the exact closing phrase from the documentation (word-for-word, searchable)
        logger_prefix: str - prefix for logging to identify the context
    """
    text = ""
    async with async_session_maker() as db:
        doc_repo = DocumentationRepository(db)
        doc = await doc_repo.get_documentation_item(UUID(docId))
        text = doc["content"] if doc and "content" in doc else ""

    if not text:
        logger.warning("%sNo text found for document ID: %s", logger_prefix, docId)
        return ""

    start_match = re.search(re.escape(start_pattern), text)
    start_index = start_match.start() if start_match else 0
    offset = start_index + len(start_pattern)
    end_match = re.search(re.escape(end_pattern), text[offset:]) if start_match else None

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
