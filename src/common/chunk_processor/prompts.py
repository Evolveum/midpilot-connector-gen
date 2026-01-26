# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.


def get_summary_prompts(content: str, type: str = "partial") -> tuple[str, str]:
    """
    Generate a summary prompt for the LLM based on the content and type.

    inputs:
    - content: the text content to summarize
    - type: "partial" for partial summary, "full" for full summary
    returns:
    - tuple of (developer_prompt, user_prompt)
    """
    if type == "partial":
        devel_prompt = """You are an expert technical documentation summarizer. The content provided is a partial section of larger documentation.

Write a concise summary starting with "This page contains..." that describes:
1. Content type (e.g., API endpoints, authentication methods, data models, configuration, guides)
2. Main topics covered (e.g., user management, payment processing, data storage)
3. Depth of coverage (e.g., overview, detailed specification, complete reference)
4. Format (e.g., specification, reference guide, tutorial, partial reference)

**IMPORTANT**: When describing endpoints, COUNT them first and state the exact number. Do not use vague terms.

CRITICAL RULES - BE PRECISE ABOUT SCOPE AND FORMAT:

**For Specifications (OpenAPI/Swagger/JSON Schema):**
- If content is a raw specification document (OpenAPI, Swagger, JSON Schema format), say: "[OpenAPI/Swagger/JSON Schema] specification for [API/module name] defining..."
- ALWAYS include the API/module name from the title, info section, or description (e.g., "Organization API", "User Management API", "User Provisioning API")
- Describe what endpoints/schemas this part of the spec covers
- Example: "This page contains an OpenAPI 3.0 specification for the User Provisioning API, defining user and group management endpoints with detailed schemas for SCIM provisioning."
- Example: "This page contains an OpenAPI specification for the Organizations REST API, defining endpoints for managing users, groups, and policies."

**For Detailed Reference Pages (>15,000 tokens with many detailed endpoints):**
- If content has 6+ endpoints with full details (parameters, request/response examples, schemas), say: "a detailed reference page for..."
- Use keywords: "detailed reference", "comprehensive documentation", "complete endpoint documentation"
- If the description is located in another file, do not count that endpoint
- Example: "This page contains a detailed reference page for user management endpoints, providing comprehensive documentation for creating, retrieving, updating, and deleting users with complete request/response schemas and examples."

**For Single/Few Endpoints:**
- ONE endpoint: "documentation for a single endpoint that..."
- 2-5 endpoints: "documentation for [number] endpoints that..." (be specific: "2 endpoints", "3 endpoints", etc.)
- 6+ endpoints: "documentation for [number] endpoints that..." (be specific with the count)
- NEVER use vague terms like "detailed reference", "comprehensive", or "multiple" - always state the exact number
- Include the functional section/area if clearly mentioned (e.g., "6 endpoints for user management", "4 endpoints for the Groups API", "3 Manage API endpoints")

**For Overviews:**
- Content <10,000 tokens or limited depth: always include "overview"
- Example: "This page contains an overview of REST API authentication methods, covering OAuth 2.0, API keys, and basic auth concepts."

CRITICAL:
- Include ONLY what is actually documented with details (descriptions, parameters, responses)
- Ignore navigation panels, sidebars, table of contents, and repeated header/footer sections
- If a page starts with long lists of links/endpoints before actual content, ignore those lists
- Some pages are only navigation/index pages. These could be identified by very short content with many links. We definitely don't want to add summaries from navigation here, so dont be shy and mark them in the summary as navigational or index pages.
- Only count endpoints that have full documentation (not just listed in navigation)
- Endpoints mentioned with only a title/name and no description are navigation elements - ignore them
- Real endpoint documentation has multiple sentences explaining functionality, parameters, responses, etc.
- If one endpoint is detailed while others are just mentioned, only include the detailed one

EXCLUDE:
- Specific parameter/field names or endpoint URLs
- Code examples or JSON structures  
- Specific values, IDs, or configuration details
- Step-by-step instructions
- UI elements or navigation structure
- References to other documentation sections

IMPORTANT: If the content does contain a lot of relative links, with two sentences of text or less,
it is probably not detailed content or specification. If it is like that do not count these endpoint as endpoints!
Also mention that in your summary.
It can also be in a format of "/api/v1/endpoint/": ./api/docs/endpoint.yml or $ref/api/docs/endpoint.yaml
If this is the case ignore that endpoint.
This means that is not a detailed description of that endpoint because the description is elsewhere.

CRITICAL:
- Always include in the summary the fact if the content is from navigation or index page!

Be natural and objective. Don't force domain terminology that doesn't fit.

Output should be a JSON object with the following properties:
- summary: string - the generated summary of the content
- num_endpoints: int - the number of endpoints defined in the content
- has_authentication: bool - indicates if the content contains detailed authentication methods
- is_overview: bool - indicates if the content is an overview/introduction page
- is_index: bool - indicates if the content is a navigational/index page

EXAMPLE OUTPUTS:

{
    "summary": "This page contains a detailed reference page for 8 user management endpoints, providing comprehensive documentation for creating, retrieving, updating, and deleting users with complete request/response schemas and examples.",
    "num_endpoints": 8,
    "has_authentication": false,
    "is_overview": false,
    "is_index": false
}

{
    "summary": "This page contains a detailed reference of REST API authentication methods, covering OAuth 2.0, API keys, and basic auth concepts.",
    "num_endpoints": 0,
    "has_authentication": true,
    "is_overview": false,
    "is_index": false
}

{
    "summary": "This page contains an overview of the User Management API, etc etc",
    "num_endpoints": 0,
    "has_authentication": false,
    "is_overview": true,
    "is_index": false
}

{
    "summary": "This page is a navigational/index page with links to various API documentation sections. It does not contain detailed endpoint information.",
    "num_endpoints": 0,
    "has_authentication": false,
    "is_overview": false,
    "is_index": true
}

{
    "summary": "This page contains a part of OpenAPI 3.0 specification for the User Provisioning API, defining user and group management endpoints with detailed schemas for SCIM provisioning.",
    "num_endpoints": 35,
    "has_authentication": true,
    "is_overview": false,
    "is_index": false
}

        """
        user_prompt = f"Write a descriptive summary starting with 'This page contains...' for the following documentation:\n\n{content}\n\nSummary:"
    else:  # full summary
        devel_prompt = """You are an expert technical documentation summarizer. The content provided contains summaries from all parts of the documentation.

Write a comprehensive summary starting with "This documentation contains..." that describes:
1. Types of content present (e.g., API references, guides, schemas, configuration docs)
2. Main topics and functional areas covered (e.g., user management, authentication, data processing)
3. Overall depth of coverage (e.g., mix of overviews and detailed specs, comprehensive reference)
4. Overall format (e.g., specification, reference guide, tutorial collection)

CRITICAL RULES - IDENTIFY COMPLETE SPECIFICATIONS:

**For Complete Specifications:**
- If the full documentation is a specification (OpenAPI/Swagger/JSON Schema), say: "a complete [OpenAPI/Swagger/JSON Schema] specification for [API/module name]..."
- ALWAYS include the API/module name from the summaries (e.g., "Organization API", "User Management API", "User Provisioning API")
- Example: "This documentation contains a complete OpenAPI 3.0 specification for the User Provisioning API, covering users, groups, schemas, and service provider configuration endpoints."
- Example: "This documentation contains a complete OpenAPI specification for the Organizations REST API, defining user management, groups, domains, events, policies, and directory operations."

**For Other Documentation:**
- Include ONLY what is actually documented with details, not what is merely listed in navigation
- Ignore navigation panels, sidebars, table of contents, and repeated sections
- Only count content that has full documentation (not just mentioned in menus/links)

EXCLUDE:
- Specific parameter/field names or endpoint URLs
- Code examples or JSON structures
- Specific values, IDs, or configuration details
- Step-by-step instructions
- UI elements or navigation structure

If specification contains not only json or yaml but a lot of html, css or js or navigational features,
always loudly mention that, not only in a one simple sentence.

IMPORTANT: If there is a lot of relative links of $ref decriptions, it is not a detailed specification/guide.
If the endpoint decsriptions are in other files the specification/guide is not detailed or complete.

EXTREMLY IMPORTANT: If you have a json or yaml spec and it contains navigational or other non spec elements,
never forget to metion that, because it is really important to know if the spec contains irrelevant content!

Be natural and objective. Don't force domain terminology that doesn't fit.

Example for complete spec: "This documentation contains a complete OpenAPI 3.0 specification for the User Provisioning API, covering user and group management, schemas, and SCIM provisioning with detailed request/response schemas."
Example for mixed content: "This documentation contains comprehensive REST API references for organizational management, covering user management, authentication, authorization policies, and event tracking with a mix of high-level overviews and detailed API specifications."

OUTPUT FORMAT:
Return ONLY a JSON object with a these properties:
- summary: string - the generated summary of the complete documentation
- num_endpoints: int - total number of endpoints defined across all parts
- has_authentication: bool - indicates if any part contains detailed authentication methods
- is_overview: bool - indicates if it is primarily an overview/introduction documentation
- is_index: bool - indicates if it is primarily a navigational/index documentation

EXAMPLE OUTPUTS:

{
    "summary": "This documentation contains a complete OpenAPI 3.0 specification for the User Provisioning API, covering user and group management, schemas, and SCIM provisioning with detailed request/response schemas.",
    "num_endpoints": 45,
    "has_authentication": true,
    "is_overview": false,
    "is_index": false
}

{
    "summary": "This documentation contains comprehensive REST API references for organizational management, covering user management, authentication, authorization policies, and event tracking with a mix of high-level overviews and detailed API specifications.",
    "num_endpoints": 30,
    "has_authentication": true,
    "is_overview": false,
    "is_index": false
}

        """
        user_prompt = f"Write a comprehensive summary starting with 'This documentation contains...' for the complete documentation:\n\n{content}\n\nSummary:"

    return devel_prompt, user_prompt


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
    You are an expert in processing technical documentation for {app} {app_version}. Your task is to analyze the provided content chunk and extract relevant information.

    Please provide:
    1. A concise summary of the chunk.
    2. Number of endpoints defined in the chunk.
    3. Relevant tags that describe the content (e.g., "endpoints", "authorization", "user management", object classes that are referenced, each as a "<class_name>", you may also include specific endpoints if there are not more than 10).
    4. A category for the chunk from the following options: "spec_yaml", "spec_json", "reference_api", "reference_other", "overview", "index", "tutorial", "non-technical", "other".
    5. Tags for the chunk that you think best describe its content, this should be in seperate field from point 3
    6. Category that you think best describes the content in the chunk, not from the predefined list, this should be in seperate field from point 4

    Be precise and objective in your analysis.

    Output should be a JSON object with the following properties:
    - summary: string - a concise summary of the chunk
    - num_endpoints: int - number of endpoints defined in the chunk
    - tags: list of strings - relevant tags describing the content
    - category: string - one of the predefined categories
    - llm_tags: list of strings - tags that best describe the content according to only you
    - llm_category: string - category that best describes the content according to only you, not from the predefined list

    EXAMPLE OUTPUT:
    {{
        "summary": "This chunk contains detailed documentation for user management endpoints, including creating, retrieving, updating, and deleting users.",
        "num_endpoints": 5,
        "tags": ["endpoints", "user management", "provisioning", "User", "Group"],
        "category": "reference_api",
        "llm_tags": ["user management", "API reference", "provisioning"],
        "llm_category": "API reference"
    }}
    """

    user_prompt = f"""
    Analyze the following documentation chunk for {app} {app_version}:

    Part of page with URL: {page_url}

    Content: {content}
    """

    return developer_prompt, user_prompt
