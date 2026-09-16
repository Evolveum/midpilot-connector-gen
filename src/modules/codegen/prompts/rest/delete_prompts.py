# Copyright (C) 2010-2026 Evolveum and contributors
# Licensed under the EUPL-1.2 or later.

from src.modules.codegen.prompts.operation_prompts import (
    REST_ENDPOINT_RULES,
    REST_OPERATION_CONTEXT,
    build_operation_system_prompt,
    build_operation_user_prompt,
)

get_delete_system_prompt = build_operation_system_prompt(
    "delete",
    context_rules=REST_ENDPOINT_RULES,
    rules=r"""
- Delete has the smallest configuration surface of every operation: a bare endpoint method+path
  (Groovy `endpoint("...")`, or declarative YAML `delete.endpoints: [{{method: DELETE, path: ...}}]`)
  is normally the entire artifact. Do not add request bodies, response parsing, or filters unless
  documentation explicitly requires them.
- The target object class is "{object_class}". In Groovy, keep `objectClass("{object_class}")` exactly; in declarative YAML, keep the `objectClasses.{object_class}` key exactly. Never switch to a different class name (e.g., "User").
- Treat <result> as the current working code in whichever format it is already in. Extend or minimally edit it; do not discard or rename previously correct parts.
- Do not fabricate endpoints, parameters, attributes, or fields. If documentation is unclear, leave a short TODO comment rather than guessing.
- Preserve the outer object-class and delete structure if already present in <result>.
- No extra commentary outside the fenced code block.
""",
)

get_delete_user_prompt = build_operation_user_prompt(REST_OPERATION_CONTEXT)
