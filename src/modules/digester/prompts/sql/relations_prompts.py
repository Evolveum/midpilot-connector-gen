# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""SQL-specific system prompts for the staged relation pipeline."""

import textwrap

from src.modules.digester.prompts.relation_ontology import RELATION_ONTOLOGY

SQL_EVIDENCE_RULES = textwrap.dedent(
    """
<sql_evidence_rules>
The documentation describes a relational database integration: DDL, table/column catalogs,
constraints, views, examples, and narrative data-model documentation.

Strong evidence includes declared FOREIGN KEY constraints (including composite keys), column
comments or schema descriptions that explicitly identify a target table/object class, and
junction tables whose foreign keys connect two independently manageable classes. Preserve the
logical object-class and attribute names exposed to the connector as well as different physical
table/column/constraint names in `sqlEvidence` and `quote`.

A foreign key is a candidate link, not automatically an IGA association. Reject audit,
history, staging, transport, tenant-partition, and implementation-only dependencies unless the
documentation shows a manageable access relationship. Primary keys, similar column names, and
common suffixes such as `_id` are not evidence without a constraint, mapping, or explicit
description. Custom and opaque names remain valid when their definitions prove the link.

A pure or qualified junction table can be a `link_object`; its payload columns may describe
the association. A table that merely references two others is not automatically a link object.
SQL has no endpoint-path or virtual-endpoint relation.
</sql_evidence_rules>
"""
)


def _system(task: str) -> str:
    return RELATION_ONTOLOGY + SQL_EVIDENCE_RULES + textwrap.dedent(task)


get_relation_harvest_system_prompt = _system(
    """
<task>
Read one SQL documentation fragment and report every supported link between extracted object
classes. Gather evidence without deciding final IGA semantics. Copy logical class/attribute
names exactly; populate `sqlEvidence` with different physical tables, ordered columns, named
constraints, and a junction table. Include foreign-key direction, all composite columns,
nullability/cardinality, and junction-table evidence when stated. Use a short verbatim quote.

Do not manufacture a link from `_id` naming, co-occurrence, or a primary key alone. Report
possible audit/embedded/inheritance cases so adjudication can reject them. Return an empty list
when the fragment provides no actual cross-class evidence.
</task>
"""
)

get_relation_class_sweep_system_prompt = _system(
    """
<task>
Review all SQL documentation collected for one focus object class. Find its outgoing and
incoming foreign keys, explicit logical mappings, and association/junction tables. Every
observation must involve the focus class. Preserve logical attributes and physical identifiers
separately, include every column of composite constraints, and report different relationships
between the same tables separately. Do not treat an audit or implementation dependency as an
IGA association merely because a foreign key exists. Return an empty list when no evidence
exists.
</task>
"""
)

get_relation_pair_focus_system_prompt = _system(
    """
<task>
Re-read the SQL evidence for one class pair. Recover the exact logical attributes, physical
tables/columns, named constraints, composite-column order, uniqueness/nullability/cardinality,
and any junction table with association payload. Check whether multiple constraints express
different associations between the same classes. Naming conventions alone are insufficient;
custom identifiers are valid when DDL or documentation grounds them. Explicitly record why an
audit, staging, inheritance, or implementation-only dependency is not a manageable relation.
</task>
"""
)

get_relation_adjudication_system_prompt = _system(
    """
<task>
Judge all observations for one SQL object-class pair. Emit one verdict per distinct manageable
association, not one per repeated DDL mention. Reject name-only guesses and foreign keys whose
documented purpose is audit, history, staging, transport, partitioning, or another purely
implementation dependency.

Orient subject and object from the documented access semantics, never from table or column
names. Attribute values in the verdict are logical connector attributes grounded in known
attributes or observations; physical column names belong in the evidence/rationale unless they
are explicitly also the logical names. Use `link_object` only when a documented junction class
really carries the association. Never emit `virtual_endpoint` for SQL. Leave unknown attributes
empty and base confidence on explicit constraints/mappings plus semantic corroboration.
</task>
"""
)

get_relation_verification_system_prompt = _system(
    """
<task>
Try to refute one proposed SQL association. Refute it when support is only naming convention,
an unrelated foreign key, a technical/audit dependency, or fabricated logical attributes.
Verify composite constraints as a unit and evaluate only the proposed association when the pair
has several. Do not replace a documented logical connector attribute with a different physical
column name. Correct attributes only from explicit evidence for this association. SQL cannot
justify `virtual_endpoint`. Default to refuted when no concrete constraint, mapping, or semantic
description supports the proposal.
</task>
"""
)
