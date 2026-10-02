# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

# The policy that governs any repair of already-generated connector code. Shared with the
# object-class fix, which applies the same rules to many artifacts at once and
# therefore cannot reuse REPAIR_SYSTEM_SUFFIX itself: that suffix ends by demanding
# one complete artifact for one requested operation.
REPAIR_POLICY_RULES = textwrap.dedent("""\
- Preserve correct user edits, endpoint choices, objectClass, and operation blocks unless the errors or documentation prove they are wrong.
- Make the smallest necessary changes that directly address the reported errors. Do not regenerate unrelated working code.
- If an error identifies unsupported DSL, request mutation, endpoint path, filter, attribute, or parameter usage, replace it with syntax supported by the provided DSL docs and same-endpoint evidence.
- If a script conflicts with extracted data or documentation, repair the conflict and keep a short TODO comment only when the required evidence is still missing.
- Preserve the script's existing format (declarative YAML or Groovy) unless fixing the reported error genuinely
  requires the other format - for example, the error names a capability only Groovy can express per the
  bundled declarative-format reference. Never switch format merely for style.
""")

REPAIR_SYSTEM_SUFFIX = (
    textwrap.dedent("""\

REPAIR MODE:
- <current_script> is the user's edited connector code (declarative YAML or Groovy) and <midpoint_errors> are
  midPoint runtime or validation errors.
- Use <current_script> as the primary artifact to fix.
""")
    + REPAIR_POLICY_RULES
    + (
        "- Always return one complete, syntactically valid artifact, in its existing format unless this repair "
        "requires switching it, for the requested object class and operation.\n"
    )
)

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
