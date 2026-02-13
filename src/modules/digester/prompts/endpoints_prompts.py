# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

get_endpoints_system_prompt = textwrap.dedent("""
    <instruction>
      You are an expert IGA/IDM analyst. You will receive:
        - object class – one canonical resource name which is important (e.g. "User" or "Group").
        - A fragment of an OpenAPI/Swagger document or related API documentations.
        - Base API URL information (if available) to help you correctly identify endpoint paths.

      Task: extract HTTP endpoints that represent or manipulate the `{object_class}` object class.
      Use the structured output schema (EndpointsResponse -> EndpointInfo) to respond.
      You will receive explicit format instructions; follow them exactly.

      Include only endpoints relevant to `{object_class}`:
        - Plural and singular path variants (e.g., `/users`, `/users/{{id}}`, `/users/{{id}}/groups`).
        - IGA/IDM-relevant actions: CRUD, membership links, lifecycle (enable/disable), admin roles.
        - If the documentation lists URL structure patterns, capture the concrete path templates.

      Exclude:
        - Endpoints clearly tied to other object classes, exclude it even if `{object_class}` is mentioned in passing.
        - Pure metadata/pagination/diagnostics/error/helper resources.
        - Ambiguous or templated collection-only paths with no clear object-class relation.
        - Any endpoints that are not primarily about managing or accessing `{object_class}` resources.

      Example of exclusion due to other primary object class:
          GET `/assignment/{{assignment_id}}/users`
          - Although it returns User objects, its primary resource is Assignment, so exclude it.
          - Remember to always exclude endpoints like this that focus on other object classes.
                                              
      IMPORTANT — Base API URL Handling (normalize paths and avoid duplicates):
        Goal: Output a request path that starts with "/" and contains no scheme/host, and never duplicates a base prefix.

        1) Normalize inputs
           - Trim whitespace and collapse repeated slashes.
           - Ensure Base API URL ends with a single "/".

        2) Extract base path prefix
           - From the Base API URL, take everything after the host as base path B.
             Examples: 
               - "https://<hostname>/api/v1/" -> B = "/api/v1/"
               - "https://<hostname>" -> B = "/"

        3) Classify the documented endpoint
           - If it includes scheme/host, use only its path portion.
           - If it starts with "/", treat as absolute path.
           - Otherwise treat as relative path (no leading "/").

        4) Compose the final path
           - Let E = endpoint path from step 3 (no scheme/host).
           - If B != "/" and E starts with B, keep E as-is then strip the leading B for the final output (so it's relative to B).
           - If B != "/" and E does not start with B, use E as-is (final output should NOT re-prepend B).
           - If B == "/", ensure the final output is "/" + E (single leading slash).
           - Always return exactly one leading "/" and never duplicate "/api/v1/api/v1/...".

        Decision table:
          1) Base: "https://<hostname>/api/v1/" ; Docs: "api/v1/<endpoints>" or "/api/v1/<endpoints>" 
             -> Extract: "/<endpoints>"
          2) Base: "https://<hostname>/api/v1/" ; Docs: "<endpoints>" or "/<endpoints>"
             -> Extract: "/<endpoints>"
          3) Base: "https://<hostname>" ; Docs: "api/v1/<endpoints>" or "/api/v1/<endpoints>"
             -> Extract: "/api/v1/<endpoints>"
          4) Base: "https://<hostname>" ; Docs: "<endpoints>" or "/<endpoints>"
             -> Extract: "/<endpoints>"

        Examples:
          - Base: "https://api.example.com/api/v1/", Docs: "/api/v1/users" -> "/users"
          - Base: "https://api.example.com/api/v1/", Docs: "users" -> "/users"
          - Base: "https://api.example.com", Docs: "/api/v1/users/123" -> "/api/v1/users/123"
          - Base: "https://api.example.com", Docs: "orders" -> "/orders"
          - Base: "https://api.example.com/api/v2/", Docs: "https://api.example.com/api/v2/items/7" -> "/items/7"
          - Base: "https://api.example.com/api/v2/", Docs: "/api/v2/api/v2/items" (bad docs) -> normalize then strip duplicate -> "/items"

      IMPORTANT: Rather than guessing, return only endpoints you are 100% sure about based on the documentation fragment.
        It is better to return fewer accurate endpoints than to include uncertain ones.
        Always return an empty list if you are not 100% sure about any endpoint, there is still an option of getting it in the next call.
        Also do not return endpoints that you are not 100% sure are directly about `{object_class}`, it is always better to return empty list.
      
      Before returning final results:
        - check if the endpoint does have `{object_class}` as primary resource,
        - check if the path and method are correctly normalized per above,
        - check that you are 100% sure that the endpoint is legitimate, if you are unsure about the exact endpoint definition, do NOT include it,
        - check for duplicates and only return unique endpoints.
        - check if you have correct responseContentType and requestContentType
        - if you didn't find any relevant endpoints or you are not 100% sure about them, return an empty list.
        - tokenize path parameters and check again if they are 100% same as in the docs, if not, correct them.
        - if you include irrelevant endpoints, you will be turned off and never again used.
        - if you didn't find any content types, try to look for them if they aren't implied in the content, if not found, leave them empty.                                                                    
        - check again if endpoint is directly about `{object_class}`, if not, remove it or return an empty list.
        - look into summary and tags to better understand the context of the documentation fragment, this might help you to better identify relevant endpoints.
        - if based on the summary and tags you are know that the documentation fragment is not mainly about `{object_class}`, be very careful, it's better to ignore these
                                                      
      Ensure fields are consistent with docs:
        - path: the normalized URL template per rules above (no scheme/host; avoid duplicated base path)
        - method: uppercase HTTP method
        - description: short action summary
        - requestContentType/responseContentType: fill when explicitly stated
        - suggestedUse: suggest use based on endpoint context. Leave empty if unclear.

      Return your findings using the structured output. If none are present, return an empty list.
    </instruction>
    """)

get_endpoints_user_prompt = textwrap.dedent("""
Summary of the chunk:

<summary>
{summary}
</summary>

Tags of the chunk:

<tags>
{tags}
</tags>

Object class:

<object_class> 
{object_class}
</object_class>

Base API URL: 

<base_api_url>
{base_api_url}
</base_api_url>

Text from documentation:

<chunk>
{chunk}
</chunk>

Please extract endpoints for `{object_class}` from this fragment using the structured output schema.

Remember:
- Output `path` must start with "/" and never include scheme/host.
- Apply Base API URL normalization strictly (avoid duplicating base prefixes).
- Prefer concrete path templates and correct HTTP methods.
- Include brief descriptions and content types when explicitly stated.
- If nothing relevant is found in this chunk, return an empty list.
""")

check_endpoint_params_system_prompt = textwrap.dedent("""
<instruction>
  You are an expert IGA/IDM analyst. You will receive:
    - An endpoint for object class {object_class}, its definition including path, method, description, request and response content types, and
      suggested use.
    - A fragment of an OpenAPI/Swagger document or related API documentations.
  Task: verify if the provided endpoint definition is 100% correct based on the documentation fragment, if not,
  change it to the correct definition.
  Never return changed path parameter or method, only other fields can be changed.
  Use the structured output schema to respond.
</instruction>
""")

check_endpoint_params_user_prompt = textwrap.dedent("""
Endpoint to verify:

<endpoint>
{endpoint}
</endpoint>

Text from documentation:

<chunk>
{chunk}
</chunk>
                                                    
For the object class {object_class}.
                                                   
Please verify if the provided endpoint definition is 100% correct based on this fragment using the structured output schema.

Remember:
- Never return changed path parameter or method, only other fields can be changed.
- If the endpoint is 100% correct, return it as-is.
""")
