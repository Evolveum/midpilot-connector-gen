# Copyright (C) 2010-2026 Evolveum and contributors
# Licensed under the EUPL-1.2 or later.

"""Common prompt envelope; protocol modules own only context and operation rules."""

from src.modules.codegen.prompts.declarative_format_prompts import DECLARATIVE_FORMAT_POLICY_SYSTEM_RULES


def build_operation_system_prompt(operation: str, *, rules: str, context_rules: str = "") -> str:
    return (
        "You are a midPoint ConnId connector engineer. Generate the requested "
        + operation
        + " artifact using the supplied framework reference and target-system evidence.\n\n"
        + f"<{operation}_docs>\n{{{operation}_docs}}\n</{operation}_docs>\n"
        + context_rules
        + DECLARATIVE_FORMAT_POLICY_SYSTEM_RULES
        + "{repair_system_suffix}\n\nOPERATION RULES:\n"
        + rules.strip()
        + "\n"
    )


def build_operation_user_prompt(context: str, *, intent: bool = False) -> str:
    return (
        "Chunk {idx}/{total}. Target object class: {object_class}.\n"
        + ("Requested search intent: {intent}.\n" if intent else "")
        + "\n<extracted_attributes>\n{attributes_json}\n</extracted_attributes>\n"
        + context
        + "{repair_user_suffix}\n"
        + "\nTarget-system documentation for this iteration:\n<chunk>\n{chunk}\n</chunk>\n"
        + "\nLast accepted artifact:\n<result>\n{result}\n</result>\n"
    )


REST_OPERATION_CONTEXT = """
<extracted_endpoints>
{endpoints_json}
</extracted_endpoints>
<preferred_endpoints>
{preferred_endpoints_json}
</preferred_endpoints>
<base_api_url>
{base_api_url}
</base_api_url>
"""

REST_ENDPOINT_RULES = """
- Use target paths, HTTP methods, payloads and filter capabilities from the supplied application
  evidence, scoped to the same endpoint and method. Framework examples illustrate syntax only.
- Prefer compatible <preferred_endpoints>. If they conflict with the application evidence, preserve
  the documented behavior and add a concise TODO explaining the discrepancy.
- Normalize API paths relative to <base_api_url>: remove the scheme, host and an existing base-path
  prefix; retain literal path placeholders. Use connector-relative paths without a leading slash.
- Search is a logical operation, not a restriction to HTTP GET. Honor a documented POST or other
  method. Select a documented endpoint configuration or custom implementation that supports its
  method and body; never change the target method to fit a framework example or invent a DSL key.
"""
