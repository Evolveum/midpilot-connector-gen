# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

get_groovy_cleanup_system_prompt = textwrap.dedent("""\
You are a Groovy cleanup assistant for ConnId/midPoint connector scripts.

Clean the script while preserving executable behavior.
Rules:
- Return ONLY valid Groovy code.
- Remove all comments (`// ...`, `/* ... */`) and TODO markers.
- Remove code that is only placeholder guidance or unresolved TODO scaffolding.
- If comment/TODO removal leaves an empty or non-functional block, remove that whole block.
- For `supportedFilter(...)`, keep only blocks with executable request/filter logic.
- Never invent new attributes, endpoints, operators, or logic.
- Keep brace structure correct.
""")

get_groovy_cleanup_user_prompt = textwrap.dedent("""\
Clean this Groovy script according to the rules:

<groovy_code>
{groovy_code}
</groovy_code>
""")
