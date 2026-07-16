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
- <scim_resource_contract> is the resource binding. Use it for the resource endpoint, primary schema binding,
  attached extension schemas, and extension-to-resource relationship. `resource.primarySchema` and `extensions`
  are compact snapshots embedded in the ScimResource export and may differ from the standalone schema. It is not
  a runtime resource instance.
- <connid_object_class> is the connector-framework projection. Use it to understand which attributes and identifiers
  the ConnID layer currently exposes. It is normalized only from the ObjectClass export, not enriched from either
  schema view. Attributes omitted there are not automatically absent from the SCIM API.
- <scim_service_provider_config> is the session-wide capability contract. Treat its explicit flags and limits as
  authoritative: PATCH is available only when `patch.supported` is true; generate SCIM filtering or sorting only when
  their respective `supported` flags are true; respect `filter.maxResults`; and add ETag/If-Match behavior only when
  `etag.supported` is true. Bulk and change-password capabilities do not prove per-object-class CRUD endpoints.
- ServiceProviderConfig does not carry independent support flags for GET, POST, PUT or DELETE. Determine those methods
  from <extracted_endpoints> and the SCIM resource contract rather than inferring that they are disabled.
- When the views disagree, do not silently merge them or let one overwrite another. Use SCIM schema/resource data for
  target API semantics and the ConnID projection for framework visibility. Bridge a discrepancy only when extracted
  attributes or provider documentation provide enough evidence; otherwise preserve a short TODO instead of guessing.
""")

SCIM_CONTRACT_CONTEXT_USER_SECTION = textwrap.dedent("""\

Standalone SCIM protocol schema (protocol-level attribute and rule definition):

<scim_protocol_schema>
{scim_protocol_schema_json}
</scim_protocol_schema>

SCIM Resource export (endpoint plus its own embedded primary-schema and extension snapshots):

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

SCIM_OPERATION_ENDPOINTS_SYSTEM_RULES = textwrap.dedent("""\
- Treat <extracted_endpoints> as deterministic operation-surface evidence. Use endpoint paths from that section or
  the matching SCIM resource contract, and HTTP methods from extracted endpoints or documented SCIM semantics.
  Never infer a resource path only from the object-class name.
- Provider documentation may refine a matching endpoint's parameters or behavior, but it must not silently replace an
  explicit exported resource endpoint with an unrelated path.
- If both the exported resource endpoint and <extracted_endpoints> are empty, generate a custom operation only when
  <current_chunk> or user-provided preferred endpoints explicitly prove it. Otherwise keep the operation empty instead
  of manufacturing standard CRUD from the object-class name. A contract containing only `extensionOf` is an extension
  schema binding, not an independently addressable resource.
""")

SCIM_OPERATION_ENDPOINTS_USER_SECTION = textwrap.dedent("""\

Deterministically extracted endpoints for this object class:

<extracted_endpoints>
{endpoints_json}
</extracted_endpoints>
""")
