#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

# from langchain_openai import OpenAIEmbeddings
#
# from ..config import config
#

# def get_default_embeddings() -> OpenAIEmbeddings:
#     """
#     Create and return a default OpenAIEmbeddings instance.
#
#     :return: Configured OpenAIEmbeddings instance.
#     """
#     # check_embedding_ctx_length=False is a must because otherwise the langchain_openai library
#     # will send tokenized text with tiktoken and not just raw text for some reason...
#     # tiktoken_enabled=False is also an option, but it uses transformers library
#     # which we don't want to install if it is not needed
#     return OpenAIEmbeddings(
#         openai_api_key=config.embeddings.openai_api_key,
#         openai_api_base=config.embeddings.openai_api_base,
#         model=config.embeddings.model_name,
#         check_embedding_ctx_length=False,
#     )
