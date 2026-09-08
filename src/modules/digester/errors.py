# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Errors for extraction results produced by the digester.

Codegen consumes these results and therefore imports these errors, the same
way it imports the digester result schemas (the one allowed cross-module
dependency: codegen -> digester).
"""

from uuid import UUID

from src.core.errors import AppError


class InvalidDocumentationFilterError(AppError):
    status_code = 422
    code = "invalid_documentation_filter"

    def __init__(self):
        super().__init__("Provide both method and a non-empty path, or omit both for class documentation.")


class DocumentationEndpointNotFoundError(AppError):
    status_code = 404
    code = "documentation_endpoint_not_found"

    def __init__(self):
        super().__init__("The requested endpoint is not available for this object class.")


class InvalidEndpointsOutputError(AppError):
    status_code = 422
    code = "invalid_endpoints_output"

    def __init__(self):
        super().__init__("Stored endpoints data is invalid. Re-run endpoint extraction or override its result.")


class ObjectClassesNotFoundError(AppError):
    """Raised when a session has no object classes available for a requested operation."""

    status_code = 404
    code = "object_classes_not_found"

    def __init__(self, session_id: UUID | None = None):
        session_context = f" in session {session_id}" if session_id else " in session"
        super().__init__(f"No object classes found{session_context}. Please run /classes endpoint first.")


class InvalidObjectClassesOutputError(AppError):
    """Raised when objectClassesOutput exists but does not match the expected contract."""

    status_code = 422
    code = "invalid_object_classes_output"

    def __init__(self, session_id: UUID):
        super().__init__(f"Invalid object classes data in session {session_id}")


class ObjectClassNotFoundError(AppError):
    """Raised when a requested object class cannot be found in session data."""

    status_code = 404
    code = "object_class_not_found"

    def __init__(self, object_class: str, session_id: UUID):
        super().__init__(f"Object class '{object_class}' not found in session {session_id}")


class RelevantChunksNotFoundError(AppError):
    """Raised when an extraction cannot proceed because no relevant chunks were selected."""

    status_code = 400
    code = "relevant_chunks_not_found"

    def __init__(self, object_class: str, extraction_target: str):
        super().__init__(
            f"No relevant chunks found for object class '{object_class}'. Cannot extract {extraction_target}."
        )


class AttributesNotFoundError(AppError):
    """Raised when attributes for an object class have not been extracted yet."""

    status_code = 404
    code = "attributes_not_found"

    def __init__(self, object_class: str, session_id: UUID):
        super().__init__(
            f"No attributes found for {object_class} in session {session_id}. "
            f"Please run /classes/{object_class}/attributes endpoint first."
        )


class OperationSurfaceNotFoundError(AppError):
    """Raised when the endpoints needed to generate REST operations have not been extracted yet."""

    status_code = 404
    code = "operation_surface_not_found"

    def __init__(self, object_class: str, session_id: UUID):
        super().__init__(
            f"No endpoints found for {object_class} in session {session_id}. "
            f"Please run /classes/{object_class}/endpoints endpoint first."
        )


class EndpointExtractionNotSupportedError(AppError):
    """Raised when endpoint extraction is requested for a protocol that has no endpoints."""

    status_code = 422
    code = "endpoint_extraction_not_supported"

    def __init__(self, object_class: str, api_type: str):
        super().__init__(
            f"Endpoint extraction is not applicable to a '{api_type}' session ({object_class}). "
            "A database connector has no endpoints: its tables come from the uploaded schema and "
            "code generation reads the table and column of each attribute from /classes/"
            f"{object_class}/attributes."
        )


class SqlTableIdentityConflictError(AppError):
    """Raised when SQL schema sources cannot be paired without guessing a physical table."""

    status_code = 422
    code = "sql_table_identity_conflict"

    def __init__(self, table_name: str, identities: list[str]):
        rendered_identities = ", ".join(sorted(set(identities)))
        super().__init__(
            f"Conflicting SQL identities for table '{table_name}': {rendered_identities}. "
            "Catalog, schema and table metadata must identify one unambiguous physical table."
        )


class RelationsNotFoundError(AppError):
    """Raised when a session has no extracted relations."""

    status_code = 404
    code = "relations_not_found"

    def __init__(self, session_id: UUID):
        super().__init__(f"No relations found in session {session_id}. Please run /relations endpoint first.")


class RelationNotFoundError(AppError):
    """Raised when a specific relation cannot be found in a session."""

    status_code = 404
    code = "relation_not_found"

    def __init__(self, relation_name: str, session_id: UUID):
        super().__init__(f"Relation {relation_name} not found in session {session_id}.")


class InvalidRelationsOutputError(AppError):
    """Raised when stored relationsOutput exists but does not match the expected contract."""

    status_code = 422
    code = "invalid_relations_output"

    def __init__(self, session_id: UUID):
        super().__init__(
            f"Stored relationsOutput is invalid in session {session_id}. "
            "Re-run relations extraction or override the relations payload."
        )
