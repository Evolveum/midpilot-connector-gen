# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

REPAIR_SYSTEM_SUFFIX = textwrap.dedent("""\

REPAIR MODE:
- <current_script> is the user's edited Groovy script and <midpoint_errors> are midPoint runtime or validation errors.
- Use <current_script> as the primary script to fix. Preserve correct user edits, endpoint choices, objectClass, and operation blocks unless the errors or documentation prove they are wrong.
- Make the smallest necessary changes that directly address <midpoint_errors>. Do not regenerate unrelated working code.
- If an error identifies unsupported DSL, request mutation, endpoint path, filter, attribute, or parameter usage, replace it with syntax supported by the provided DSL docs and same-endpoint evidence.
- If the current script conflicts with extracted data or documentation, repair the conflict and keep a short TODO comment only when the required evidence is still missing.
- Always return one complete, syntactically valid Groovy script for the requested object class and operation.
""")

REPAIR_USER_SUFFIX = textwrap.dedent("""\

Current user-edited script:
<current_script>
{current_script}
</current_script>

midPoint errors for the current script:
<midpoint_errors>
{midpoint_errors_json}
</midpoint_errors>
""")
