# Copyright (C) 2010-2026 Evolveum and contributors
# Licensed under the EUPL-1.2 or later.

from src.modules.codegen.prompts.operation_prompts import (
    REST_ENDPOINT_RULES,
    REST_OPERATION_CONTEXT,
    build_operation_system_prompt,
    build_operation_user_prompt,
)

get_update_system_prompt = build_operation_system_prompt(
    "update",
    context_rules=REST_ENDPOINT_RULES,
    rules=r"""
- The target object class is "{object_class}". In Groovy, keep `objectClass("{object_class}")` exactly; in declarative YAML, keep the `objectClasses.{object_class}` key exactly. Never switch to a different class name (e.g., "User").
- If an update artifact contains multiple endpoints, each fully implemented endpoint MUST narrow its attributes: `supportedAttributes ...`/`supportedAttribute("...") {{ ... }}` in Groovy, or the endpoint's `supportedAttributes` list in declarative YAML.
- Treat endpoint lifecycle actions as high-risk and strictly gated. Infer lifecycle intent from endpoint path, endpoint description, and endpoint `suggestedUse` (e.g., activate, deactivate, enable, disable, lock, unlock, suspend, unsuspend, block, unblock).
- When lifecycle attribute and target value are clearly documented, the lifecycle endpoint MUST fix that attribute to the target value: `supportedAttribute("<attr>") {{ value <targetValue> }}` in Groovy, or `supportedAttributes: [{{name: <attr>, value: <targetValue>}}]` in declarative YAML, where `<attr>` and `<targetValue>` match documentation/extracted data. Do not use an unenforced `transition` filter as the sole gate for a lifecycle change - see <declarative_docs> for its current limitation.
- For lifecycle endpoints with incomplete evidence, you MAY keep an incomplete endpoint scaffold and TODO comments so later chunks can finish it. In that temporary scaffold, avoid guessing attribute names/values.
- If using an incomplete lifecycle scaffold, include clear TODO comments describing what is missing (attribute name, target value, request mapping). This scaffold may temporarily omit the attribute-value mapping until evidence appears in a later chunk.
- Merge rule for iterative chunks: treat `<result>` as persistent memory from previous chunks. Information missing in the current chunk is NOT a reason to remove already-resolved configuration from `<result>`.
- No-regression rule: NEVER downgrade concrete working code to placeholders. If `<result>` already contains a concrete lifecycle mapping, do not replace it with TODO comments or commented-out values.
- You may change an existing concrete lifecycle value only when the current chunk provides explicit contradictory evidence. If evidence is ambiguous, keep the existing concrete value and add a short TODO about the conflict.
- Treat <result> as the current working code in whichever format it is already in. Extend or minimally edit it; do not discard or rename previously correct parts.
- Do not fabricate endpoints, parameters, attributes, or fields. If documentation is unclear, leave a short TODO comment rather than guessing.
- Preserve the outer object-class and update structure if already present in <result>.
- No extra commentary outside the fenced code block.
""",
)

get_update_user_prompt = build_operation_user_prompt(REST_OPERATION_CONTEXT)
