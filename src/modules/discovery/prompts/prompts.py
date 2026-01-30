# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.


def get_discovery_fetch_sys_prompt(prompt_version: int = 0) -> str:
    if prompt_version == 0:
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

    if prompt_version == 1:
        return """
        <instruction>
            You are an expert of writing web search queries for a search engine.
            Generate multiple distinct queries that increase recall (official docs, developer portal, OpenAPI/Swagger, SCIM, etc.).
        </instruction>
        """

    return """<instruction>You are an expert of writing web search queries for a search engine.</instruction>"""


def get_discovery_fetch_user_prompt(app: str = "APP", app_version: str = "VERSION", prompt_version: int = 0) -> str:
    if prompt_version == 0:
        return (
            "Find me references pointing to the api documentations for {}, for {} version. "
            "When you call the search tool, just return the output of it as is, do not alter it in any way.".format(
                app, app_version
            )
        )

    if prompt_version == 1:
        return """
        Find me references pointing to the api documentations for {}, for {} version.
        When you call the search tool, just return the output of it as is, do not alter it in any way.
        """.format(app, app_version)

    if prompt_version == 2:
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

    return "Generate a search query for API documentation for {app} {app_version}.".format(
        app=app, app_version=app_version
    )


def get_discovery_eval_sys_prompt(prompt_version: int = 0) -> str:
    if prompt_version == 0:
        return """
        <instruction>
            You are an expert in evaluation of which of the provided search results are most relevant to the user's request.
        </instruction>
        """
    return """<instruction>You are an expert evaluator of search results.</instruction>"""


def get_discovery_eval_user_prompt(app: str = "APP", app_version: str = "VERSION", prompt_version: int = 0) -> str:
    if prompt_version == 0:
        return (
            "I am looking for links pointing to the api documentations for {}, for {} version."
            "You are provided with a results of a web search for this."
            "Out of the provided data, return the most relevant results for the specific application and version."
            "I only want the general documentations, stuff about endpoints, possibly swagger or openapi documentations."
            "Order the outputs you selection starting with the most relevant to the least relevant."
            "Give reasoning about your decisions and make your selection only from the data provided to you.".format(
                app, app_version
            )
        )

    if prompt_version == 1:
        return """
        <instruction>
        I am looking for links pointing to the api documentations for {app}, for {app_version} version.
        You are provided with results of a web search for this.
        Out of the provided data, return the most relevant results for the specific application and version.
        Prefer: official docs, developer portals, API reference pages, OpenAPI/Swagger endpoints, SCIM docs.
        Avoid: generic blog posts, job postings, marketing-only pages, and unrelated products.
        Order the output starting with the most relevant to the least relevant.
        Make your selection only from the data provided to you.
        </instruction>
        """.format(app=app, app_version=app_version)

    return "I am looking for API docs for {app} {app_version}.".format(app=app, app_version=app_version)
