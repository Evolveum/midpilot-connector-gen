# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from functools import lru_cache
from typing import Any, Optional

from src.modules.codegen.utils.postprocess import strip_markdown_fences


class GroovyValidationError(ValueError):
    """Raised when Groovy code cannot be validated or parsed."""


def normalize_groovy_code(code: str) -> str:
    """Strip Markdown fences and surrounding whitespace from Groovy code."""
    return strip_markdown_fences(code).strip()


def validate_groovy_code(code: str) -> Optional[str]:
    """
    Validate Groovy syntax using the python `groovy-parser` package.

    Returns:
        None when the code is valid, otherwise a human-readable error message.
    """
    normalized = normalize_groovy_code(code)
    if not normalized:
        return "Groovy code cannot be empty"

    try:
        _parse_groovy_code(normalized)
    except ImportError:
        return "Groovy validation backend is unavailable. Install `groovy-parser`."
    except Exception as exc:
        return _clean_validation_message(str(exc)) or "groovy-parser rejected the Groovy code"

    return None


def ensure_valid_groovy_code(code: str) -> str:
    """Return normalized Groovy code or raise with validation details."""
    normalized = normalize_groovy_code(code)
    error = validate_groovy_code(normalized)
    if error is not None:
        raise GroovyValidationError(error)
    return normalized


@lru_cache(maxsize=1)
def _load_groovy_parser_components() -> tuple[Any, type[Any]]:
    from groovy_parser.parser import create_groovy_parser
    from groovy_parser.tokenizer import GroovyRestrictedTokenizer

    return create_groovy_parser(), GroovyRestrictedTokenizer


def _parse_groovy_code(code: str) -> None:
    parser, tokenizer_cls = _load_groovy_parser_components()
    tokens = list(tokenizer_cls().get_tokens(code))
    parser.parse(tokens)


def _clean_validation_message(message: str) -> str:
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    if not lines:
        return ""
    return " ".join(lines[:3])
