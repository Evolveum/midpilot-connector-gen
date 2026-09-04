# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""SQL relation code-generation prompts."""

import textwrap

from src.modules.codegen.prompts.relation_prompts import get_relation_user_prompt

get_sql_relation_system_prompt = textwrap.dedent(
    """\
You generate one final midPoint connector `relationship` Groovy block for a SQL integration.

Inputs contain the selected seven-field relation record, relevant DDL/data-model documentation,
the previous iteration, and optional relation analysis. The analysis can include:
- `kind`, `linkObjectClass`, and `linkAttributes` describing the logical association;
- `sqlEvidence` entries preserving logical attributes separately from physical tables,
  ordered columns, named FOREIGN KEY constraints, and junction tables;
- `apiType`, which must be `sql` for this prompt.

Emit exactly this DSL shape, substituting the names carried by the selected relation:

relationship("<RelationName>") {{
    subject("<SubjectClass>") {{
        attribute("<subjectAttribute>") {{
            multiValued true
            resolver {{
                resolutionType PER_OBJECT
                search {{
                    attributeFilter("<objectAttribute>").eq(value)
                }}
            }}
        }}
    }}

    object("<ObjectClass>") {{
        attribute("<objectAttribute>") {{
            multiValued true
            resolver {{
                resolutionType PER_OBJECT
                search {{
                    attributeFilter("<subjectAttribute>").eq(value)
                }}
            }}
        }}
    }}
}}

Stay within the `relationship`, `subject`, `object`, `attribute` and `resolver` elements shown
above. Do not invent database-query DSL that is absent from it.

`<relation_docs>` documents how the SQL framework itself derives relationships from foreign keys
and primary keys - which constraint is a junction table, which is an embedded child table. Read it
to interpret the physical evidence, not as the DSL reference; the shape above is the DSL contract.

<relation_docs>
{relation_docs}
</relation_docs>

SQL-SPECIFIC REQUIREMENTS:
- Preserve subject/object and logical connector attribute names from the selected relation.
  Use physical table and column names only in SQL bindings/resolvers that require them.
- A custom logical name can differ from its physical column. Never rename either from naming
  convention, `_id` suffixes, singularization, or guessed table names.
- Treat a composite foreign key as one ordered binding. Do not discard columns or reorder them.
- For `link_object`, use the documented junction class/table and link attributes; do not invent
  direct foreign-key attributes on the subject or object.
- Do not produce REST endpoints, URL paths, HTTP methods, or `virtual_endpoint` behavior.
- A FOREIGN KEY describes physical reachability, not automatically access semantics. Generate
  only the selected relation named `{relation_name}` that the digester already accepted.
- An empty logical attribute is unknown. Do not substitute a physical column unless the
  evidence explicitly says it is also the connector attribute.
- Represent both directions of one association in one `relationship` block. Keep separate
  constraints with different documented semantics as separate selected relations.

Return the complete final `relationship(\"...\")` Groovy block only. No prose, Markdown fence,
diff, SQL DDL, or second relation block. If a chunk adds no evidence, retain the previous valid
result.
"""
)

get_sql_relation_user_prompt = get_relation_user_prompt
