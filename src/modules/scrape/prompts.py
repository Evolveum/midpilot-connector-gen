#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.


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
* When clearly for end users, not developers → REMOVE (mark as irrelevant)
* When legal, marketing, or social content → REMOVE (mark as irrelevant)

Be aggressive in filtering out irrelevant content, but conservative with anything that might contain API specifications, IDM concepts, or integration documentation.
Also be very careful not to mark as irrelevant any pages that are general. Simple urls that do not have that much specific keywords but can lead to relevant content later on.
Main example are landing pages for REST API documentation or IDM topics, not complicated urls that are like intersections for more valuable content.
These must never be marked as irrelevant.

## NOTES

When there is a lot of links, you can remove only the most obviously irrelevant ones.
Do not remove all the links in one go - leave some for potential future iterations.

## OUTPUT FORMAT:

Return ONLY a JSON object. No markdown formatting, no explanations, no code blocks.

Example:
{{"links": ["https://example.com/privacy-policy", "https://example.com/blog/post"]}}

If no links are irrelevant, return: {{"links": []}}
"""

    user_msg = f"""Evaluate these links and return ONLY the irrelevant ones:

{links}"""

    return developer_msg, user_msg
