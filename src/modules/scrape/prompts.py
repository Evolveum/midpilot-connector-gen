# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.


def get_irrelevant_filter_prompts(links: list[str], app: str, app_version: str) -> tuple[str, str]:
    """
    Returns developer and user messages for filtering irrelevant links.

    :return: Tuple of (developer_message, user_message)
    """
    developer_msg = f"""You are an expert documentation relevancy evaluator for {app} {app_version}.

Your mission: Identify links that are IRRELEVANT to a developer implementing IDM (Identity Management) integration for {app} {app_version}.

# CORE PRINCIPLE:
Only documentation directly supporting IDM integration, API development, or identity/access management is RELEVANT. Everything else is IRRELEVANT.

## ALWAYS RELEVANT (NEVER mark these as irrelevant):

1. API Documentation & Specifications:
   * REST API documentation, endpoints, references
   * OpenAPI/Swagger specifications (any format: JSON, YAML, HTML)
   * API landing pages, introductions, getting started guides
   * GraphQL schemas and documentation
   * SDK/library documentation for integration
   * API authentication & authorization documentation

2. IDM-Specific Content:
   * Provisioning (user/group lifecycle management)
   * User management, authentication, authorization
   * Roles, groups, permissions, access control
   * Identity providers, directory services
   * SSO (Single Sign-On), OAuth, SAML, OIDC
   * SCIM protocol documentation
   * Account management APIs

3. Technical Integration Resources:
   * Developer guides for integration
   * Data schemas (JSON/YAML), data models
   * Webhooks, events, callbacks related to user/identity events
   * Code examples, sample integrations
   * Migration guides for identity/user data

4. Key IDM Terminology Indicators (when present, link is likely RELEVANT):
   "provisioning", "user management", "authentication", "authorization", "roles", "organizations",
   "groups", "access control", "identity", "directory", "SSO", "OAuth", "SAML",  "admin",
   "API", "REST", "schema", "SCIM", "federation", "claims", "tokens"

5. Overview and Landing Pages:
   * High-level overviews of API capabilities
   * Landing pages for developer portals or API documentation hubs
   * Architecture diagrams related to identity management
   * It is important to NEVER mark these as irrelevant, so that we can get to relevant content later on.

## ALWAYS IRRELEVANT (ALWAYS mark these as irrelevant):

1. Legal & Compliance:
   * Privacy policies, terms of service, cookie policies
   * Legal notices, GDPR statements, compliance documents

2. Marketing & Sales:
   * Product marketing pages, feature comparisons
   * Pricing pages, sales collateral, case studies
   * Product announcements, press releases

3. End-User Content:
   * End-user help/support documentation
   * How-to guides for non-technical users
   * UI walkthroughs, feature tutorials for end users
   * Troubleshooting for end-user issues
   * Not admin or developer focused content

4. Community & Social:
   * Blog posts, articles, news
   * Community forums, discussion boards
   * Social media links, user-generated content
   * NO community content is relevant, except for specifications (.json or .yaml files) shared in community forums
   * We really don't want to mark spec community content as irrelevant. Never mark .yml, .yaml, .json files as irrelevant when in community forums or sites.

5. Non-Development Pages:
   * Login/signup pages
   * Contact us, about us pages
   * Company information, careers
   * Download pages for end-user applications
   * Postman collections (keyword: "postman")

6. Unrelated Technical Content:
   * Documentation for other products/integrations (unless IDM-related)
   * Niche features unrelated to identity/access management
   * Performance tuning, monitoring (unless IDM-specific)
   * Client-side UI/UX documentation

7. Release Notes & Change Logs:
   * Version histories, update logs
   * Release announcements
   * Software release notes

8. Content in other languages than English
   * Non-English documentation pages
   * For example Spanish, French, German, Chinese, Japanese, etc. - /fr/, /de/, /es/, /zh/, /jp/ etc.

9. Github Commits and Issues
   * Individual commit pages
   * Issue tracker pages
   * Pull request pages
   * Commit history pages

## Exceptions & Special Cases:

* If a link contains name of other product/service than {app}, always mark it IRRELEVANT

## DECISION GUIDELINES:

* When in doubt about REST API pages → KEEP (mark as relevant)
* When in doubt about IDM terminology → KEEP (mark as relevant)
* When in doubt about OpenAPI/Swagger → KEEP (mark as relevant)
* When in doubt about tutorials/guides/manuals → KEEP if even remotly technical (mark as relevant)
* When clearly for end users except admins, not developers → REMOVE (mark as irrelevant)
* When legal, marketing, or social content → REMOVE (mark as irrelevant)

Be aggressive in filtering out irrelevant content, but conservative with anything that might contain API specifications, IDM concepts, or integration documentation.
Also be very careful not to mark as irrelevant any pages that are general. Simple urls that do not have that much specific keywords but can lead to relevant content later on.
Main example are landing pages for REST API documentation or IDM topics, not complicated urls that are like intersections for more valuable content.
These must never be marked as irrelevant.

## Versioning:

When there are multiple versions of the application documented, focus ONLY on the version {app_version}.
Be VERY CAREFUL about removing links that are for the specific version {app_version}.
If the link is contains completely different version, for example much older version, then it can be marked as irrelevant.

## NOTES

When there is a lot of links, you can remove only the most obviously irrelevant ones.
Do not remove all the links in one go - leave some for potential future iterations.

## OUTPUT FORMAT:

Return ONLY a JSON object. No markdown formatting, no explanations, no code blocks.

Example:
{{"links": ["https://example.com/privacy-policy", "https://example.com/blog/post"]}}

If no links are irrelevant, return: {{"links": []}}
"""
    # It it neccessary to NOT remove more than one quarter of the links in one go, to avoid accidentally removing relevant content. You will be probably called again.

    user_msg = f"""Evaluate these links and return ONLY the irrelevant ones:

{links}"""

    return developer_msg, user_msg


# TODO: maybe regex pattern for getting relevant text around links.
# def get_relevant_filter_prompts() -> tuple[str, str]:
#    """
#    Returns developer and user messages for extracting relevant links from text.

#    :args:
#         text: str - the text content to analyze for relevant links
#         app: str - application name
#         app_version: str - application version
#    :return: Tuple of (developer_message, user_message)
#    """
#    developer_msg = """
#    You are an expert in getting links from text that are relevant for IDM integration of {app} {app_version}.
#    Our goal is to get to the API documentation, ideally specifications (OpenAPI/Swagger in JSON/YAML format) or REST API documentation, that is relevant for IDM integration.
#    It is also neccessary to get to the documentation about authentification methods, supported apiTypes, and data schemas, that are relevant for IDM integration.
#    Don't forget about documentation about SCIM, if there is any.

#    You will be given text from the webpage, they might or might not be relevant or contain relevant links.

#    Your mission: Identify links that are ideally API specifications / documentation, authentification methods, supported apiTypes, and data schemas that are RELEVANT to a developer implementing IDM (Identity Management) integration for {app} {app_version}.

#    Don't ever return links that are not directly about {app} {app_version}, or that describe integration with other products, or that are for completely different versions of {app}.
#    For example if the app is "Google" report only information about Google APIs, not about other products like "Google Analytics", "Google Ads" or "YouTube", and not about other versions of Google products that are not relevant for IDM integration.

#    {parser_instructions}
#    """

#    user_msg = """Evaluate this text and return ONLY the links that are relevant for IDM integration:

# {text}"""

#    return developer_msg, user_msg


def get_relevant_filter_prompts(text: str, app: str, app_version: str) -> tuple[str, str]:
    """
    Returns developer and user messages for extracting relevant links from text.

    :args:
       text: str - the text content to analyze for relevant links
       app: str - application name
       app_version: str - application version
    :return: Tuple of (developer_message, user_message)
    """
    developer_msg = f"""
**Situation**
The assistant is working with a developer who needs to identify relevant API documentation and specifications for implementing Identity Management (IDM) integration with a specific application and version. The input consists of markdown text extracted from webpages containing links and descriptions that may or may not be relevant to the integration task.

**Task**
Analyze the provided markdown text and identify links that are directly relevant for IDM integration of {app} {app_version}. The assistant should extract links that either directly contain the target documentation OR serve as pathways leading to API documentation, specifications, authentication methods, supported API types, data schemas, or SCIM documentation that would be useful for a developer implementing IDM integration.

**Objective**
Enable developers to quickly locate both the final technical documentation AND the intermediate navigation paths needed to reach that documentation for implementing IDM integration, without wasting time on irrelevant links or documentation for different products or versions.

**Knowledge**
Priority documentation types to identify (in order of importance):
1. API specifications in OpenAPI/Swagger format (JSON/YAML files)
2. REST API documentation specific to IDM integration
3. Authentication and authorization methods documentation
4. Supported API types and endpoints
5. Data schemas and object models
6. SCIM (System for Cross-domain Identity Management) protocol documentation

The assistant should identify two categories of links:
- **Direct links**: Links that point directly to the target documentation (API specs, authentication guides, schemas)
- **Pathway links**: Links that serve as navigation steps toward the target documentation (developer portals, API reference homepages, documentation hubs, getting started guides)

The assistant must apply strict filtering criteria:
- Links must be directly about {app} {app_version} specifically
- Links must not describe integrations with other products
- Links must not be for different versions of {app} unless explicitly relevant to {app_version}
- Links must not be for related but separate products (e.g., if {app} is "Google", exclude "Google Analytics", "Google Ads", "YouTube")

The markdown text provided is extracted from webpages and may contain irrelevant content, broken formatting, or links unrelated to IDM integration.

{{parser_instructions}}
   """

    user_msg = f"""Evaluate this text and return ONLY the links that are relevant for IDM integration:
   
{text}"""

    return developer_msg, user_msg
