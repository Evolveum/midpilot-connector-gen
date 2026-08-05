# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.


def get_llm_chunk_process_prompt(content: str, page_url: str, app: str, app_version: str) -> tuple[str, str]:
    """
    Generate prompts for chunk processing LLM.
    inputs:
        content: str - the text content to process
        app: str - application name
        app_version: str - application version
    outputs:
        tuple - (developer_prompt, user_prompt)
    """

    developer_prompt = f"""
    You are an expert in processing technical documentation for {app} {app_version}.
    Analyze one documentation chunk and return exactly one JSON object.

    Output should be a JSON object with the following properties:
    - summary: string - a concise summary of the chunk
    - num_endpoints: int - number of documented endpoints in this chunk
    - tags: list of strings - relevant tags describing the content
    - category: string - one of "spec_yaml", "spec_json", "reference_api", "reference_other", "overview", "index", "tutorial", "non-technical", "other"
    - different_app_name: bool - true only if chunk is clearly about a different product than {app}
    - num_defined_object_classes: Optional[int] - number of clearly defined object classes, otherwise null

    TAGGING RULES:
    - Add protocol/context tags only when explicitly supported by the chunk content.
    - If SCIM evidence exists (e.g., "/scim", RFC7643/RFC7644, SCIM Users/Groups, SCIM URNs), include tag "SCIM".
    - If REST/OpenAPI/Swagger evidence exists, include tag "REST".
    - Keep tags concise and deduplicated.

    IMPORTANT:
    - Do not invent values.
    - If unsure, keep nullable fields as null.
    - Return JSON only.

    EXAMPLE OUTPUT:
    {{
      "summary": "This chunk documents SCIM user and group provisioning endpoints.",
      "num_endpoints": 2,
      "tags": ["SCIM", "provisioning", "User", "Group"],
      "category": "reference_api",
      "different_app_name": false,
      "num_defined_object_classes": 2
    }}

    EXAMPLE OUTPUT:
    {{
      "summary": "This chunk contains detailed documentation for user management endpoints, including creating, retrieving, updating, and deleting users.",
      "num_endpoints": 5,
      "tags": ["REST", "endpoints", "user management", "provisioning", "User", "Group"],
      "category": "reference_api",
      "different_app_name": false,
      "num_defined_object_classes": null
    }}
    """

    user_prompt = f"""
    Analyze the following documentation chunk for {app} {app_version}.

    Part of page with URL: {page_url}

    Content:
    {content}
    """

    return developer_prompt, user_prompt
