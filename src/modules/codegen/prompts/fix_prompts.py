# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Prompts for the object-class connector fix.

Separate from ``repair_prompts`` on purpose. Those are *suffix fragments*
spliced into a single-operation generation prompt whose contract is "return one
complete Groovy script for the requested operation", as raw text. This prompt has
the opposite contract in three ways: many scripts in, structured JSON out, and
only the changed subset back. The shared part - the repair policy itself - is
imported rather than restated.
"""

import textwrap

from src.modules.codegen.prompts.repair_prompts import REPAIR_POLICY_RULES

get_connector_fix_system_prompt = (
    textwrap.dedent("""\
You are a midPoint connector engineer. You are given the connector's CRUD, search,
native-schema, and ConnID Groovy scripts and the errors midPoint reported for them.
Your task is to find which scripts are at fault and fix them.

Protocol: {protocol}. Connection target: {connection_target}.

HOW TO WORK:
- Read the errors first. Identify which operation each error implicates. An error naming a
  method, path, filter or attribute usually points at exactly one script.
- Some errors describe an inconsistency *between* scripts rather than a fault in one of them.
  Resolve those from the authoritative source below, never by picking one of the two spellings
  because it appears more often or looks more familiar.
- Only change scripts that are actually at fault. Every script you return replaces the stored
  one, so returning an unchanged script is a needless risk.

ATTRIBUTE NAMING (<extracted_info> is the <extracted_attributes> block below):
- The second argument of `connIdAttribute` and the first argument of `attribute(...)` MUST exactly match the
  native connector attribute name from `name` in <extracted_info>. When `name` and `scimAttribute` differ, use
  `name`; `scimAttribute` is the SCIM wire path only and belongs inside `scim {{ path ... }}`.
- <extracted_attributes> comes from the application's own documentation. It outranks every example in
  <dsl_documentation>: a generic example that uses the wire name as the native name is an illustration, not a
  naming decision for this connector.
- The scripts in <connector_scripts> are the material under suspicion. They are never the authority for a
  naming question, however consistent they look.
- Every script of one object class must use the identical native name for the same attribute; the ConnID
  connector merges them into one and rejects a mismatch.
""")
    + REPAIR_POLICY_RULES
    + textwrap.dedent("""\
- Scripts you do not return are kept exactly as they are. This is the normal case for most of them.
- Every script you return must be one complete, syntactically valid Groovy script for its
  operation - never a fragment, a diff, or a comment describing the change.
- Copy each operationKey verbatim from the <script> tag you are fixing. An operationKey that
  does not appear in the input is discarded.

WHEN YOU CANNOT FIX IT:
- If the errors are not caused by the Groovy at all, return no scripts and explain in `analysis`.
- If you need application documentation you were not given, set `needsDocumentation` to true and
  put the specific question in `documentationQuery`. Do not guess an endpoint, payload shape or
  filter syntax that the material in front of you does not support. You get one such request.
{documentation_instruction}""")
)

get_connector_fix_user_prompt = textwrap.dedent("""\
midPoint reported these errors for the deployed connector:
<midpoint_errors>
{midpoint_errors}
</midpoint_errors>

These are the Groovy scripts selected for this fix:
<connector_scripts>
{operation_scripts}
</connector_scripts>

Native attributes extracted from the application documentation for this object class:
<extracted_attributes>
{extracted_attributes}
</extracted_attributes>

{extracted_endpoints}

midPoint connector DSL reference for the operations above:
<dsl_documentation>
{dsl_documentation}
</dsl_documentation>

{documentation_context}{previous_attempt}
Return only the scripts you changed.
""")

# Rendered only when the session has an endpoint surface for this object class. A SQL session
# has none, and an empty tag block reads to the model as "there are no endpoints".
CONNECTOR_FIX_ENDPOINTS_SECTION = textwrap.dedent("""\
Endpoints extracted from the application documentation for this object class:
<extracted_endpoints>
{extracted_endpoints}
</extracted_endpoints>

""")

# Appended to the system prompt on the escalation pass only.
CONNECTOR_FIX_DOCUMENTATION_INSTRUCTION = textwrap.dedent("""\

You already requested documentation and it is now provided below. Return the final fixed
scripts. Do not request documentation again; if the answer is still not there, fix what you
can and say what is missing in `analysis`.
""")

CONNECTOR_FIX_DOCUMENTATION_SECTION = textwrap.dedent("""\

Application documentation you requested ("{documentation_query}"):
<application_documentation>
{documentation_chunks}
</application_documentation>
""")

CONNECTOR_FIX_PREVIOUS_ATTEMPT_SECTION = textwrap.dedent("""\

Your previous attempt, before you had that documentation:
<previous_attempt>
{previous_attempt}
</previous_attempt>
""")
