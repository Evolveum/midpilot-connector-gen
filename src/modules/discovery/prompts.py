#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.


def get_discovery_fetch_sys_prompt(prompt_version=0):
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
        </instruction>
        """


def get_discovery_fetch_user_prompt(app="APP", app_version="VERSION", prompt_version=0):
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
        return "Generate me a search query for search engine pointing to the api documentation for {}, for {} version. Return the search query only and nothing else.".format(
            app, app_version
        )


def get_discovery_eval_sys_prompt(prompt_version=0):
    if prompt_version == 0:
        return """
        <instruction>
            You are an expert in evaluation of which of the provided search results are most relevant to the user's request.
        </instruction>
        """


def get_discovery_eval_user_prompt(app="APP", app_version="VERSION", prompt_version=0):
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
        I am looking for links pointing to the api documentations for {}, for {} version.
        You are provided with a results of a web search for this.
        Out of the provided data, return the most relevant results for the specific application and version.
        I only want the general documentations, stuff about endpoints, possibly swagger or openapi documentations.
        Order the outputs you selection starting with the most relevant to the least relevant.
        Give reasoning about your decisions and make your selection only from the data provided to you.
        </instruction>
        """.format(app, app_version)
