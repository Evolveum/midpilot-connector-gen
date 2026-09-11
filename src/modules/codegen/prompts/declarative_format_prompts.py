# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Shared YAML-vs-Groovy policy, centralized once and spliced into every operation prompt.

The declarative YAML format is preferred whenever the bundled reference below documents
enough coverage for the requested behavior; Groovy is used only where that reference (or the
operation-specific documentation already in this prompt) shows it is required. This is the one
place that policy is written down - every ``get_*_system_prompt`` in this package inserts
``DECLARATIVE_FORMAT_POLICY_SYSTEM_RULES`` after its own ``<*_docs>`` block instead of repeating
the rules, so a change to the policy never needs to be made in more than one place.
"""

import textwrap

DECLARATIVE_FORMAT_POLICY_SYSTEM_RULES = textwrap.dedent("""\

DECLARATIVE YAML VS GROOVY:
- <declarative_docs> is the authority for what the declarative YAML format can express for this
  connector framework, including its documented scripting hooks (YAML fields whose value is a
  Groovy expression or block, e.g. `objectExtractor`, `pagingSupport`, `supportedFilters[].request`,
  an `implementation` block, or an OAuth2 hook) and its explicitly preview/unsupported/not-yet-
  functional features.
- Prefer declarative YAML for this operation whenever <declarative_docs> and the operation
  documentation above show it fully covers the required behavior. Use Groovy only for the parts
  that documented declarative YAML cannot express; when <declarative_docs> shows a documented
  scripting hook, write that one part as the hook's embedded Groovy inside otherwise-declarative
  YAML rather than abandoning YAML for a full Groovy script.
- If the framework already provides everything needed by default, return the smallest YAML that
  is still a complete, valid document for this operation - do not add a handler, endpoint, or
  script merely to have one. An explicitly empty block (e.g. `{{}}`) is a normal, complete result
  when nothing needs overriding.
- Never emit a feature <declarative_docs> marks as preview, in development, not yet functional, or
  not enforced as if it were working behavior. Do not use it, comment around it, or silently
  substitute Groovy for the same not-yet-supported behavior; if the requirement cannot be met by
  anything currently documented as functional, say so with one TODO comment instead of inventing
  an implementation.
- An empty <result> means new generation: choose the format using the documentation above,
  preferring YAML. A nonempty <result> is the last accepted artifact: extend or minimally edit
  it while preserving its existing YAML or Groovy format across chunks. For repairs, follow
  the repair instructions and preserve the supplied current script's format.
- Output exactly one of the two formats, and nothing else:
  - Declarative YAML: return ONLY the YAML document, fenced as a single ```yaml code block```.
  - Groovy: return ONLY the Groovy code, fenced as a single ```groovy code block```.

<declarative_docs>
{declarative_docs}
</declarative_docs>
""")
