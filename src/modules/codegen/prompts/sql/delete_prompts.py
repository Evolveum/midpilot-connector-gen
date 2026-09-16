# Copyright (C) 2010-2026 Evolveum and contributors
# Licensed under the EUPL-1.2 or later.

from src.modules.codegen.prompts.operation_prompts import build_operation_system_prompt, build_operation_user_prompt
from src.modules.codegen.prompts.sql.shared_context_prompts import (
    SQL_NATIVE_OPERATION_DSL_SYSTEM_RULES,
    SQL_SCHEMA_CONTEXT_SYSTEM_RULES,
    SQL_SCHEMA_CONTEXT_USER_SECTION,
)

get_sql_delete_system_prompt = build_operation_system_prompt(
    "delete",
    context_rules=SQL_SCHEMA_CONTEXT_SYSTEM_RULES + SQL_NATIVE_OPERATION_DSL_SYSTEM_RULES,
    rules=r"""
- <delete_docs> is the authoritative source for Groovy DSL structure, and <declarative_docs> for its
  declarative-YAML equivalent. The schema context and the documentation chunk supply target-specific facts
  only; they must not replace that structure.
- The output is native SQL, never REST or SCIM DSL. In Groovy, place the native delete operation directly
  below the object class: `objectClass("{object_class}") {{ delete {{ enabled true }} }}`.
  In declarative YAML, the equivalent is `objectClasses.{object_class}.delete: {{enabled: true}}` - and since
  delete is enabled by default, an object class needing no delete customization needs no `delete` key at all.
- The target object class is "{object_class}". In Groovy, keep `objectClass("{object_class}")` exactly; in
  declarative YAML, keep the `objectClasses.{object_class}` key exactly.
- The framework deletes the row identified by the primary key. The declarative reference documents no
  fixed-predicate or cascade-override key for delete, so do not write statements, predicates, or cascade
  handling into the delete configuration in either format; a requirement beyond `enabled`/`enabled: false`
  needs a fully custom Groovy operation registration, which this artifact does not cover - add one TODO
  comment naming the gap instead of inventing keys.
- Never invent cascade deletes. Related-table (child/junction) row cleanup on delete, when documented, is
  handled by the framework itself, not by anything written into this operation's configuration.
- When the schema or the documentation shows a soft-delete column, do not switch the operation to an
  update; add one TODO comment stating that deactivation may be required instead.
- If no attribute is marked `primaryKey`, add one TODO comment stating that the identity column has to be
  confirmed - never guess it from a column name.
- No extra commentary outside the fenced code block.
""",
)

get_sql_delete_user_prompt = build_operation_user_prompt(SQL_SCHEMA_CONTEXT_USER_SECTION)
