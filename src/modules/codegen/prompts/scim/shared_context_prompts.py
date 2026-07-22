# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

SCIM_CONTRACT_CONTEXT_SYSTEM_RULES = textwrap.dedent("""\

SCIM CONTRACT CONTEXT RULES:
- Keep the four supplied SCIM views separate; they describe different abstraction levels and may legitimately differ.
- <scim_protocol_schema> is the standalone SCIM schema. Use it as the protocol-level authority for attribute names,
  types, complex sub-attributes, required/multi-value flags, mutability, returned behavior, uniqueness, canonical values,
  reference types, and schema URN.
- <scim_resource_contract> is the resource binding. Use it for the primary schema binding, attached extension schemas,
  and extension-to-resource relationship. Its resource endpoint is framework metadata for identifying the target
  resource only; never render it as `endpoint(...)` or any other path declaration in native SCIM Groovy. The embedded
  `resource.primarySchema` and `extensions` are compact snapshots and may differ from the standalone schema. It is not
  a runtime resource instance.
- <connid_object_class> is the connector-framework projection. Use it to understand which attributes and identifiers
  the ConnID layer currently exposes. It is normalized only from the ObjectClass export, not enriched from either
  schema view. Attributes omitted there are not automatically absent from the SCIM API.
- <scim_service_provider_config> is the session-wide capability contract. Treat its explicit flags and limits as
  authoritative: PATCH is available only when `patch.supported` is true; an explicit `filter.supported: false` forbids
  SCIM filtering, while `filter.supported: true` enables documented filtering. If the whole contract is empty or the
  filter capability is absent, treat filtering support as unknown and rely only on explicit provider documentation in
  <chunk>.
  Generate sorting only when `sort.supported` is true, respect `filter.maxResults`, and add ETag/If-Match behavior only
  when `etag.supported` is true. Bulk and change-password capabilities do not prove per-object-class CRUD endpoints.
- ServiceProviderConfig does not carry independent support flags for GET, POST, PUT or DELETE. Use the SCIM resource
  contract and the generic operation documentation for standard SCIM behavior. Provider documentation may describe
  implementation-specific deviations, but it must not replace the native SCIM Groovy DSL with REST DSL syntax.
- When the views disagree, do not silently merge them or let one overwrite another. Use SCIM schema/resource data for
  target API semantics and the ConnID projection for framework visibility. Bridge a discrepancy only when extracted
  attributes or provider documentation provide enough evidence; otherwise preserve a short TODO instead of guessing.
""")

SCIM_CONTRACT_CONTEXT_USER_SECTION = textwrap.dedent("""\

Standalone SCIM protocol schema (protocol-level attribute and rule definition):

<scim_protocol_schema>
{scim_protocol_schema_json}
</scim_protocol_schema>

SCIM Resource export (resource binding with embedded primary-schema and extension snapshots):

<scim_resource_contract>
{scim_resource_contract_json}
</scim_resource_contract>

ConnID object class projection (what the connector framework currently exposes, not the SCIM API schema):

<connid_object_class>
{connid_object_class_json}
</connid_object_class>

SCIM ServiceProviderConfig (session-wide protocol capabilities and limits):

<scim_service_provider_config>
{scim_service_provider_config_json}
</scim_service_provider_config>
""")

SCIM_NATIVE_OPERATION_DSL_SYSTEM_RULES = textwrap.dedent("""\

SCIM VS REST DSL BOUNDARY:
- The generic SCIM operation documentation embedded in this system prompt is authoritative for Groovy DSL structure.
  Provider documentation and SCIM contracts supply target-specific facts only. HTTP examples in provider
  documentation must never switch the output to REST DSL.
- Generate native SCIM operation blocks directly below `objectClass(...)`. Never generate `endpoint(...)` anywhere in
  native SCIM output, including as an object-class-level wrapper. Also never generate `httpOperation`,
  `request {{ ... }}`, response extractors, manual path/query parameters, or request bodies.
- Treat every endpoint or URL in <scim_resource_contract> and <chunk> as framework-owned routing metadata only. It
  must not appear in the generated Groovy code. The native SCIM framework resolves resource and item paths.
- The SCIM framework owns standard HTTP methods, item-path construction, serialization, response extraction, filter
  encoding, and pagination. Generate a custom implementation only when the generic SCIM operation documentation
  permits it and <chunk> explicitly proves the provider requires a non-standard behavior.
- Treat <result> as current working code. Preserve already-correct native SCIM blocks across chunks and minimally edit
  or extend them; never rewrite them as REST endpoints. If <result> contains an `endpoint(...)` wrapper around native
  SCIM operations, remove the wrapper and keep those operations directly below `objectClass(...)`.
""")
