# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.


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
