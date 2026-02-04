# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.
from typing import Any, Dict, Iterable, List


def get_discovery_fetch_sys_prompt() -> str:
    return """
    <instruction>
        You are an expert of writing web search queries for a search engine.
        The queries you are asked about regard documentations of APIs for various applications.
        Your role is to find the best search query possible and to execute it using the web-search-tool given to you.
        A good search query is specific to the user request but not extremely specific - the user wants candidate links to choose from.
        The documentation does not have to be official and the search query is never limited to a specific site. Your queries are
        very generalistic.
    </instruction>
    """


def get_discovery_fetch_user_prompt(app: str = "APP", app_version: str = "VERSION") -> str:
    return """Generate EXACTLY 5 distinct web search queries to find API documentation for:
        - application: {app}
        - version: {app_version}
        
        Return ONLY a JSON object in this format:
        {{
          "searchPrompts": [
            "<prompt_1>",
            "<prompt_2>",
            "<prompt_3>",
            "<prompt_4>",
            "<prompt_5>"
          ]
        }}
        
        Rules:
        - Each query must be different (vary keywords: "developer docs", "API reference", "OpenAPI", "Swagger", "SCIM").
        - Do NOT include any text outside the JSON.
        """.format(app=app, app_version=app_version)


def get_irrelevant_filter_prompts(
    candidates: Iterable[Dict[str, Any]],
    app: str,
    app_version: str,
) -> tuple[str, str]:
    """
    Returns developer and user messages for filtering irrelevant links.

    :return: Tuple of (developer_message, user_message)
    """
    developer_msg = f"""You are an expert documentation relevancy evaluator for {app} {app_version}.

Your mission: Identify links that are clearly IRRELEVANT to a developer implementing IDM (Identity Management) integration for {app} {app_version}.

# CORE PRINCIPLE:
Prefer to keep links that might contain APIs, integration guidance, or identity/access management details. Only mark links as irrelevant when they are clearly off-topic.

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

## USUALLY IRRELEVANT (mark these as irrelevant only if clearly unrelated to developer integration):

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

4. Community & Social:
   * Blog posts, articles, news
   * Community forums, discussion boards
   * Social media links, user-generated content
   * KEEP community content if it includes API references, specs (.json/.yaml), SDKs, examples, or integration guidance.

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
   * KEEP if they mention API/SCIM changes or integration-relevant changes.

8. Content in other languages than English
   * Non-English documentation pages
   * For example Spanish, French, German, Chinese, Japanese, etc. - /fr/, /de/, /es/, /zh/, /jp/ etc.
   * KEEP if it is clearly official technical documentation and no English alternative is obvious.

9. Git Repos / Issues / PRs
   * Individual commit pages
   * Issue tracker pages
   * Pull request pages
   * Commit history pages
   * KEEP if they contain API specs, Swagger/OpenAPI files, or integration examples.

## Exceptions & Special Cases:

* If a link contains name of other product/service than {app}, always mark it IRRELEVANT

## DECISION GUIDELINES:

* When in doubt about REST API pages → KEEP (mark as relevant)
* When in doubt about IDM terminology → KEEP (mark as relevant)
* When in doubt about OpenAPI/Swagger → KEEP (mark as relevant)
* When a page could lead to developer docs (landing pages, hubs, overview) → KEEP
* When clearly for end users, not developers → REMOVE (mark as irrelevant)
* When legal, purely marketing, or purely social content → REMOVE (mark as irrelevant)

Be conservative in filtering: only remove links that are clearly off-topic. Favor keeping anything that might contain API specifications, IDM concepts, or integration documentation.

## NOTES

When there is a lot of links, you can remove only the most obviously irrelevant ones.
Do not remove all the links in one go - leave some for potential future iterations.

## OUTPUT FORMAT:

Return ONLY a JSON object. No markdown formatting, no explanations, no code blocks.

Example:
{{"links": ["https://example.com/privacy-policy", "https://example.com/blog/post"]}}

If no links are irrelevant, return: {{"links": []}}
"""

    lines: List[str] = []
    for item in candidates:
        url = str(item.get("url", "")).strip()
        title = str(item.get("title", "")).strip()
        snippet = str(item.get("snippet", "")).strip()
        lines.append(f"- url: {url}\n  title: {title}\n  snippet: {snippet}")

    user_msg = "Evaluate these links and return ONLY the irrelevant ones:\n\n" + "\n".join(lines)

    return developer_msg, user_msg


def get_rank_links_prompts(
    candidates: Iterable[Dict[str, Any]],
    app: str,
    app_version: str,
) -> tuple[str, str]:
    """
    Returns developer and user messages for ranking links by relevance.

    :return: Tuple of (developer_message, user_message)
    """
    developer_msg = f"""You are an expert documentation relevancy evaluator for {app} {app_version}.

Your task: Rank the provided links from MOST relevant to LEAST relevant for a developer implementing IDM (Identity Management) integration for {app} {app_version}.

Guidelines:
- Prefer API documentation, developer guides, SDKs, OpenAPI/Swagger, and SCIM/SSO/identity-related resources.
- Prefer official documentation over community posts, but keep community content if it includes concrete specs or examples.
- Landing pages or hubs that lead to developer docs are still relevant.
- Do NOT remove links; only reorder them.

Return ONLY a JSON object with this format:
{{"links": ["<url_1>", "<url_2>", "..."]}}
"""

    lines: List[str] = []
    for item in candidates:
        url = str(item.get("url", "")).strip()
        title = str(item.get("title", "")).strip()
        snippet = str(item.get("snippet", "")).strip()
        lines.append(f"- url: {url}\n  title: {title}\n  snippet: {snippet}")

    user_msg = "Rank these links from most relevant to least relevant:\n\n" + "\n".join(lines)
    return developer_msg, user_msg
