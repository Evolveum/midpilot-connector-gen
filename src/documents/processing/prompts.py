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
    - num_defined_object_classes: Optional[int] - number of clearly defined object classes, otherwise null
    - object_classes: list of objects with "name" and "evidence" - the object classes this chunk documents

    TAGGING RULES:
    - Add protocol/context tags only when explicitly supported by the chunk content.
    - If SCIM evidence exists (e.g., "/scim", RFC7643/RFC7644, SCIM Users/Groups, SCIM URNs), include tag "SCIM".
    - If REST/OpenAPI/Swagger evidence exists, include tag "REST".
    - Keep tags concise and deduplicated.

    OBJECT CLASS RULES:
    - An object class is a kind of record the API manages, such as User, Group, Role, Project or WorkPackage.
    - List a class only when this chunk defines its schema or attributes, or documents an endpoint that reads or
      changes it. Skip classes that are only mentioned in passing.
    - name: the singular name the API uses (for example "WorkPackage" for /work_packages).
    - evidence: a short verbatim quote (at most 20 words) copied from the chunk that contains the class name,
      such as a schema name, a heading or an endpoint path.
    - Return an empty list when the chunk documents no object class.

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
      "num_defined_object_classes": 2,
      "object_classes": [
        {{"name": "User", "evidence": "GET /scim/v2/Users"}},
        {{"name": "Group", "evidence": "urn:ietf:params:scim:schemas:core:2.0:Group"}}
      ]
    }}

    EXAMPLE OUTPUT:
    {{
      "summary": "This chunk contains detailed documentation for user management endpoints, including creating, retrieving, updating, and deleting users.",
      "num_endpoints": 5,
      "tags": ["REST", "endpoints", "user management", "provisioning", "User", "Group"],
      "category": "reference_api",
      "num_defined_object_classes": null,
      "object_classes": [{{"name": "User", "evidence": "## Create a user"}}]
    }}
    """

    user_prompt = f"""
    Analyze the following documentation chunk for {app} {app_version}.

    Part of page with URL: {page_url}

    Content:
    {content}
    """

    return developer_prompt, user_prompt
