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

DOCUMENTATION AND EVIDENCE:
- The bundled domain-expert .adoc reference is authoritative for framework syntax, defaults and
  capability limits. Application documentation and extracted contracts define target-specific
  names, methods, paths, payloads and requirements; they do not define connector DSL syntax.
- Examples illustrate syntax. Do not copy their object names, HTTP methods, response envelopes or
  attribute types unless the target evidence agrees. Explicit capability restrictions in the
  reference apply to the particular handler, not to every possible implementation of an operation.
- Generate only the requested operation and object class (or selected relationship/authentication
  namespace). A search artifact must not redeclare the native schema.
- Preserve working behavior across chunks. Missing details in a later chunk are not contradictory
  evidence. If it adds nothing relevant, return the last accepted artifact unchanged.
- Use only documented keys. Attribute wire types belong in json.type/scim.type/sql.type, and ConnId
  types in connId.type; reference attributes belong in references, not attributes.<name>.type.
- The same only-documented rule applies to objects and methods inside a Groovy scripting hook or a
  full Groovy script, not only to YAML keys: call only what the bundled reference documents for
  that hook or operation (for example the objects and methods it lists for a custom-implementation
  block). Never invent a client, request builder, query, or other API surface the reference does
  not document for that context; if the documented surface cannot express the requirement, say so
  with one TODO comment instead of guessing an API.
- Code inside a YAML scripting hook is Groovy: use // comments, not YAML # comments inside its body.

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
- A documented scripting hook's value is one Groovy expression or block scoped to a single
  endpoint, request, or field - it cannot declare a helper function or loop over another object
  class's results. If satisfying the requirement needs iterating over multiple objects,
  coordinating more than one HTTP call or object-class lookup, or a locally-defined helper
  function, no single hook value (for example `objectExtractor`, `pagingSupport`, or a
  `supportedFilters[].request`) can express it. Use the operation's documented full
  custom-implementation block where the reference documents one, or a complete Groovy script for
  the whole operation otherwise - even though this means leaving pure declarative YAML. This
  applies to every operation (search, create, update, delete), not only search.
- If the framework already provides everything needed by default, return the smallest YAML that
  is still a complete, valid document for this operation - do not add a handler, endpoint, or
  script merely to have one. An explicitly empty block (e.g. `{{}}`) is a normal, complete result
  when nothing needs overriding. When the operation is scoped to an object class, keep that scope
  in the empty result too (e.g. `objectClasses: {{ <the target class>: {{}} }}`) rather than a bare
  `{{}}` naming no object class at all - see the operation rules below for the exact shape.
- Never emit a feature <declarative_docs> marks as preview, in development, not yet functional, or
  not enforced as if it were working behavior. Do not use it, comment around it, or silently
  substitute Groovy for the same not-yet-supported behavior; if the requirement cannot be met by
  anything currently documented as functional, say so with one TODO comment instead of inventing
  an implementation.
- An empty <result> means new generation: choose the format using the documentation above,
  preferring YAML. A nonempty <result> is the last accepted artifact: extend or minimally edit
  it while preserving its existing format unless new requirements cannot be expressed in that
  format using documented, functional features. If a later chunk requires Groovy-only behavior
  (e.g. a custom SQL WHERE predicate unavailable in YAML), convert the complete accumulated
  artifact from YAML to Groovy, preserving all existing behavior and incorporating the new
  requirement. Never omit a requirement merely to retain YAML, or output only the converted
  fragment. For repairs, follow the repair instructions, including their rules for when a
  format switch is necessary.
- Output exactly one of the two formats, and nothing else:
  - Declarative YAML: return ONLY the YAML document, fenced as a single ```yaml code block```.
  - Groovy: return ONLY the Groovy code, fenced as a single ```groovy code block```.

<declarative_docs>
{declarative_docs}
</declarative_docs>
""")
