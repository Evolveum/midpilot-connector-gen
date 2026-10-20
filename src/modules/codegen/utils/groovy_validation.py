# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from functools import lru_cache
from typing import Any, Optional

from src.modules.codegen.utils.postprocess import strip_markdown_fences


def validate_groovy_code(code: str) -> Optional[str]:
    """
    Validate Groovy syntax using the python `groovy-parser` package.

    Returns:
        None when the code is valid, otherwise a human-readable error message.
    """
    normalized = strip_markdown_fences(code)
    if not normalized:
        return "Groovy code cannot be empty"

    try:
        _parse_groovy_code(normalized)
    except ImportError:
        return "Groovy validation backend is unavailable. Install `groovy-parser`."
    except Exception as exc:
        return _clean_validation_message(str(exc)) or "groovy-parser rejected the Groovy code"

    return None


@lru_cache(maxsize=1)
def _load_groovy_parser_components() -> tuple[Any, type[Any]]:
    from groovy_parser.parser import create_groovy_parser
    from groovy_parser.tokenizer import GroovyRestrictedTokenizer

    return create_groovy_parser(), GroovyRestrictedTokenizer


def _parse_groovy_code(code: str) -> None:
    parser, tokenizer_cls = _load_groovy_parser_components()
    tokens = list(tokenizer_cls().get_tokens(code))
    parser.parse(tokens)


def groovy_names(code: str) -> frozenset[str]:
    """Names the Groovy code references, excluding comments and string literals."""
    # groovy-parser tokens are Pygments token types; Pygments ships no type stubs.
    from pygments.token import Name  # type: ignore[import-untyped]

    _, tokenizer_cls = _load_groovy_parser_components()
    return frozenset(value for token_type, value in tokenizer_cls().get_tokens(code) if token_type in Name)


def groovy_call_names(code: str) -> frozenset[str]:
    """Names the Groovy code calls: followed by `(` or, in command syntax, by a string literal."""
    from pygments.token import Name, String  # type: ignore[import-untyped]

    _, tokenizer_cls = _load_groovy_parser_components()
    tokens = [(token_type, value) for token_type, value in tokenizer_cls().get_tokens(code) if value.strip()]
    return frozenset(
        value
        for (token_type, value), (next_type, next_value) in zip(tokens, tokens[1:])
        if token_type is Name and (next_value == "(" or next_type in String)
    )


def _clean_validation_message(message: str) -> str:
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    if not lines:
        return ""
    return " ".join(lines[:3])
