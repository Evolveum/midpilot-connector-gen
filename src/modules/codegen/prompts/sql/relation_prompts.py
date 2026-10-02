# Copyright (C) 2010-2026 Evolveum and contributors
# Licensed under the EUPL-1.2 or later.

"""SQL relation analysis constrained by the framework's discovery-only support."""

from src.modules.codegen.prompts.operation_prompts import build_operation_system_prompt
from src.modules.codegen.prompts.relation_prompts import RELATION_RULES, get_relation_user_prompt

get_sql_relation_system_prompt = build_operation_system_prompt(
    "relation",
    rules=RELATION_RULES
    + r"""

SQL-SPECIFIC REQUIREMENTS:
- `sqlEvidence` preserves logical connector attributes separately from physical tables,
  ordered columns, named FOREIGN KEY constraints and junction tables. Interpret these using
  the framework's documented relationship discovery; they do not enable scripted overrides.
- Preserve the selected subject/object and logical attribute names. Never infer a physical
  binding from casing, `_id` suffixes or guessed table names. An empty logical attribute is
  unknown; a physical column is not a logical attribute without an explicit mapping.
- Treat a composite FOREIGN KEY as one ordered binding: do not discard or reorder columns.
- For `link_object`, inspect the documented junction class/table and link attributes; do not
  invent direct foreign-key attributes on the subject or object. Keep distinct constraints
  with different semantics separate, and address only the selected relation.
- Physical reachability does not prove access semantics. If automatic discovery cannot meet
  the selected relation's requirement, return {{}} with a concise YAML TODO; never fabricate
  a Groovy relationship block, SQL query resolver, REST endpoint or virtual_endpoint behavior.
""",
)

get_sql_relation_user_prompt = get_relation_user_prompt
