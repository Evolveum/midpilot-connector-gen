# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
apiType detection (REST/SCIM/SQL).

Groups all signals that determine the API protocol and SCIM availability:
- ``documentation`` - per-chunk LLM extraction from the documentation,
- ``scim_cloud`` - scim.cloud registry lookup,
- ``knowledge`` - documentation-free LLM knowledge lookup,
- ``web_search`` - web search + LLM,
- ``availability`` - aggregation of the SCIM availability advisory.

Cross-chunk merging of the detected apiType lives in ``aggregation.merges``.
"""
