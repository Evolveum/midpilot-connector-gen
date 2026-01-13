#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

from typing import Any


def strip_markdown_fences(text: str) -> str:
    """
    Remove surrounding Markdown code fences from the given text, if present.
    Handles variations like ```groovy, ```java, or plain ```.
    Only strips the outermost pair at the start/end; leaves inner fences intact.
    """
    if not isinstance(text, str):
        return text

    t = text.strip()
    if not t.startswith("```"):
        return t

    lines = t.splitlines()
    if not lines:
        return t

    first = lines[0].strip()
    if first.startswith("```"):
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()

    return t


def _coerce_llm_text(output: Any) -> str:
    """Normalize different LLM return shapes to plain text."""
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    content = getattr(output, "content", None)
    if isinstance(content, str):
        return content
    return str(output)
