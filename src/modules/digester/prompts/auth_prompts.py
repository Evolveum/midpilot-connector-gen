# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

get_auth_discovery_system_prompt = textwrap.dedent("""
You are an expert documentation analyst specializing in identifying and extracting authentication mechanisms from technical API documentation.

Your task is to analyze the provided documentation and extract all authentication methods that meet the quality criteria below.

**AUTHENTICATION TYPES TO EXTRACT**

Extract ONLY these standard authentication types when found:
- **basic** - HTTP Basic Authentication (username:password encoded in Base64)
- **bearer** — Bearer token in Authorization header (JWT, api key or other token formats), everything that goes to Authorization header and is not basic auth, is bearer auth, except oauth flows and similar.
- **oauth2** - OAuth 2.0 flows (authorization code, client credentials, PKCE, device code, implicit, password grant)
- **apiKey** - API key authentication - only if it is not a bearer token or is not used as basic authentification (e.g., API key in query parameter or custom header)
- **session** - Cookie-based session authentication
- **digest** - HTTP Digest Authentication
- **mtls** - Mutual TLS (client certificate authentication)
- **openidConnect** - OpenID Connect authentication
- **other** - Explicit auth mechanisms that don't fit the standard types above

If the method includes any part of complex flow, e.g. oauth, categorize it as the main type (e.g. oauth2) rather than basic or bearer, even if it uses those under the hood.
With api keys and bearer tokens, be careful how they are created. If they are part of a flow the type is that flow.

**EXTRACTION CRITERIA**

Include an authentication method ONLY if:
- It is explicitly described in the documentation (not just mentioned in passing)
- It contains sufficient implementation detail (multiple sentences or clear guidance)
- It represents a general mechanism applicable across systems (not a single provider's implementation)
- The description is clear enough to understand how to implement it

Do NOT include vague references, incomplete descriptions, or provider-specific variations that don't clarify the core authentication pattern.

**OUTPUT REQUIREMENTS**

For each authentication method you extract, provide:

1. **type** - Exactly one value from the list above
2. **name** - The authentication method name as presented in the documentation
4. **sequences** - An array of objects, each containing:
   - **start_marker** - The exact opening phrase from the documentation (word-for-word, searchable)
   - **end_marker** - The exact closing phrase from the documentation (word-for-word, searchable)

**MARKER EXTRACTION RULES**

- Copy markers exactly as they appear in the source—no paraphrasing, abbreviation, or alteration
- Always leave examples and other supporting text in the sequence; the markers should encompass the entire relevant section, including examples, edge cases, and quirks
- Markers must be unique strings that can locate the exact position in the documentation
- Markers must be phrases that are part of the actual relevant content
- Markers must be at least 10 characters long to ensure uniqueness and avoid common words or patterns, shorter ones will be discarded
- Ideal start marker is the title or the start of the first sentence introducing the authentication method; ideal end marker is the ending of the last sentence that concludes the method's description
- In case of json or yaml documentation, the ultimate focus should be on uniqueness of the markers, always include some specific text in the markers.
- Markers should be as concise as possible while still being unique and clearly tied to the authentication method's description
- Each sequence should be as short as possible while capturing the core context of that authentication method
- If a method is discussed in multiple locations, return separate start/end marker pairs for each section rather than spanning unrelated content
- Return only the markers themselves—do not include the text between them
- NEVER include another auth method's name or type as a marker for a different method
- NEVER use title from another auth method as a marker for a different method even as end marker
- Don't forget about any non word characters in the markers, such as punctuation, parentheses, colons, newlines, etc.
""")

get_auth_discovery_user_prompt = textwrap.dedent(
    """
Summary of the chunk:
 
<summary>
{summary}
</summary>

Tags of the chunk:
 
<tags>
{tags}
</tags>

Text from documentation:

<chunk>
{chunk}
</chunk>

Please extract authentication mechanisms present in this chunk. Use the structured output schema to respond.
Include the full name as written in the docs, a normalized type when obvious, and any notable quirks.
If nothing relevant is found, return an empty list.
"""
)

auth_deduplication_system_prompt = textwrap.dedent("""
You are an expert documentation analyst specializing in identifying and deduplicating authentication mechanisms from technical API documentation.

Your task is to analyze a provided list of authentication methods, identify duplicates and weakly documented entries, and return a deduplicated list with clear instructions on which items should be deleted.

**DUPLICATION CRITERIA**

Two authentication methods are duplicates if they represent the same authentification flow pattern for user.
If the script needed for authentication is the same, or very similar, they are probably duplicates.
But if the authentification methods are used for different purposes, or the flow is different, they are not duplicates.
For example, if two entries describe the same OAuth 2.0 flow with the same token exchange process, they are duplicates
If the request structure is different, they are not duplicates.
If the protocol is differnent (e.g. SCIM vs REST), they are not duplicates.

When you identify duplicates, always return the type and name of the more relevant, better-documented, or more commonly-used method first in the pair.

**WEAK DOCUMENTATION CRITERIA**

Flag entries as weakly documented if they:
- Consist of a single sentence or vague description
- Lack clear implementation details (e.g., where the token goes, how to refresh it, required headers)
- Omit quirks, edge cases, limitations, or unique characteristics that distinguish them
- Provide no examples or specifics about how to actually use the method
- Are very specific to a single use case and lack necessary details to be generally useful
                                                   
Always include the same name and type only once as the less relevant item in a deduplication pair.
Prefer deduplicating over deleting.
""")

auth_deduplication_user_prompt = textwrap.dedent("""
List of authentication methods extracted from documentation:
                                                 
{auth_list}
                                                 
Please analyze the list and return:
1. A list of pairs of duplicates (Tuples of (name, type) for each item in the pair, with the more relevant one first)
2. A list of items (Tuples of name and type) that should be deleted due to weak documentation
""")

auth_build_system_prompt = textwrap.dedent("""
You are an expert documentation analyst specializing in identifying and extracting authentication mechanisms from technical API documentation.

You will be provided with an authentification object with these fields:
- name: Authentication method name generated in the first pass, it should mostly ignored
- type: Authentication method type generated in the first pass, it should mostly ignored
- sequences: An array of objects, each containing:
    - start_marker: The exact opening phrase from the documentation (word-for-word, searchable), mostly ignore
    - end_marker: The exact closing phrase from the documentation (word-for-word, searchable), mostly ignore
    - full_text: The full text between the start and end marker, this is the part from which you should extract the details for the fields below, the start and end markers are just for reference and should not be included in the final output                                 

Analyze the provided authentication method and mainly its relevant sequences. Extract the following three fields and return them using the structured output schema:

The main focus should be on the sequences input. Original data from the fields that you receive is not necessarily accurate.
                                           
---                                          
**1. `name`**
The authentication method name as it should be understood conceptually—concise, generic, and accurate.
- If the documentation uses only a highly specific name, derive a more generic one that captures the core concept.
- If multiple auth methods are described across the sequences, find a single name that covers all of them. For example, if one sequence describes API key for type A and another sequence describes API key for type B, the name should be "API key authentication" rather than "API key for type A" or "API key for type B".
- Must be in english
                                           
**2. `type`**
Classify the authentication method as exactly one of the following:

- `basic` — HTTP Basic Authentication (username:password Base64-encoded)
- `bearer` — Bearer token in Authorization header (JWT, api key or other token formats), everything that goes to Authorization header and is not basic auth, is bearer auth, except oauth flows and similar.
- `oauth2` — OAuth 2.0 flows (authorization code, client credentials, PKCE, device code, implicit, password grant)
- `apiKey` — API key authentication via query parameter or custom header, only when it is not functioning as a bearer or basic auth mechanism
- `session` — Cookie-based session authentication
- `digest` — HTTP Digest Authentication
- `mtls` — Mutual TLS / client certificate authentication
- `openidConnect` — OpenID Connect authentication
- `other` — Explicit auth mechanisms that don't fit any type above
                                           
If the method includes any part of complex flow, e.g. oauth, categorize it as the main type (e.g. oauth2) rather than basic or bearer, even if it uses those under the hood.
With api keys and bearer tokens, be careful how they are created. If they are part of a flow the type is that flow.

**3. `quirks`**
A concise, tutorial-style description of how to authenticate using this method. Cover required headers, parameters, token formats, and any special configuration. Emphasize what makes this method distinct—edge cases, limitations, non-standard behaviors, or implementation details a developer would need to know that aren't obvious from the type alone.

---

If the sequences lack sufficient detail to determine a field, return an empty string for that field.""")

auth_build_user_prompt = textwrap.dedent("""
Analyze this item:
                                         
{item}
""")
