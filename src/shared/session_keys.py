# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Session-data key vocabulary.

``session_data`` is the key/value store that carries state between pipeline stages.
Every key a module reads or writes is built here, so a key cannot be misspelled at
one call site and silently read as missing at another. Names are unchanged from the
original inline strings; object-class and relation names are used exactly as the
caller passes them (callers keep normalizing them as before).

A job-backed stage owns one family of three keys (:class:`JobSessionKeys`):

- ``{prefix}JobId`` - the latest scheduled job; a finished job publishes its result
  only while this pointer still names it.
- ``{prefix}Input`` - identifiers and caller-supplied metadata of that request. The
  complete job input lives in ``jobs.input``; this key never copies stored outputs.
- ``{prefix}Output`` - the published result.

Families, owners and value models (payload validation stays with the owning domain;
the database layer stores opaque JSON):

======================================  ==================  =========================================
Family                                  Owner               ``Output`` value model
======================================  ==================  =========================================
``discovery``                           discovery           ``CandidateLinksOutput``
``scrape``                              scrape              ``ScrapeResult``
``objectClasses``                       digester            ``ObjectClassesResponse``
``{objectClass}Attributes``             digester            ``AttributeResponse``
``{objectClass}Endpoints``              digester            ``EndpointResponse``
``relations``                           digester            ``RelationsResponse``
``connectivityEndpoint``                digester            ``ConnectivityEndpointResponse``
``auth``                                digester            ``AuthResponse``
``metadata``                            digester            ``InfoResponse`` (``infoMetadata``)
``authorization``                       codegen             ``ConnectorCodeOutput``
``{operationKey}`` (CRUD, search,       codegen             ``ConnectorCodeOutput``
native schema; see ``codegen.enums``)
``{relationName}Code``                  codegen             ``ConnectorCodeOutput``
``{objectClass}ConnectorFix``           codegen             no ``Output``: the fix republishes the
                                                            ``{operationKey}Output`` rows it changed
======================================  ==================  =========================================

``relationsAnalysisOutput`` (:data:`RELATIONS_ANALYSIS_OUTPUT`) is a companion of the
``relations`` family: the relation worker publishes it in the same transaction as
``relationsOutput``, guarded by ``relationsJobId``. It holds the relation pipeline's
working state (``RelationsAnalysis``), is read by relation codegen and is never
returned by an endpoint.

``discoveryInput`` and ``scrapeInput`` are read back (application name/version for
uploads and scraping); every other ``*Input`` is diagnostic metadata only.

An upload has no stage family: ``documentation.processUpload_{docId}_job_id`` points
to the upload job that alone may publish document ``docId``
(:func:`upload_job_pointer_key`).
"""

from dataclasses import dataclass

_INPUT_SUFFIX = "Input"
_JOB_ID_SUFFIX = "JobId"
_OUTPUT_SUFFIX = "Output"
_ATTRIBUTES = "Attributes"
_ENDPOINTS = "Endpoints"
_CONNECTOR_FIX = "ConnectorFix"
_RELATION_CODE = "Code"


@dataclass(frozen=True)
class JobSessionKeys:
    """The ``Input`` / ``JobId`` / ``Output`` keys of one job-backed session family."""

    prefix: str

    def __post_init__(self) -> None:
        if not self.prefix:
            raise ValueError("A session key family needs a non-empty prefix")

    @property
    def input(self) -> str:
        return f"{self.prefix}{_INPUT_SUFFIX}"

    @property
    def job_id(self) -> str:
        return f"{self.prefix}{_JOB_ID_SUFFIX}"

    @property
    def output(self) -> str:
        return f"{self.prefix}{_OUTPUT_SUFFIX}"


DISCOVERY = JobSessionKeys("discovery")
SCRAPE = JobSessionKeys("scrape")
OBJECT_CLASSES = JobSessionKeys("objectClasses")
RELATIONS = JobSessionKeys("relations")
CONNECTIVITY_ENDPOINT = JobSessionKeys("connectivityEndpoint")
AUTH = JobSessionKeys("auth")
METADATA = JobSessionKeys("metadata")
AUTHORIZATION = JobSessionKeys("authorization")

RELATIONS_ANALYSIS_OUTPUT = "relationsAnalysisOutput"
"""Companion of :data:`RELATIONS`, published atomically with ``relationsOutput``."""


def attributes_keys(object_class: str) -> JobSessionKeys:
    """Attribute extraction of one (normalized) object class."""
    return JobSessionKeys(f"{object_class}{_ATTRIBUTES}")


def endpoints_keys(object_class: str) -> JobSessionKeys:
    """Endpoint extraction of one (normalized) object class."""
    return JobSessionKeys(f"{object_class}{_ENDPOINTS}")


def codegen_operation_keys(operation_key: str) -> JobSessionKeys:
    """Generated code of one connector operation, e.g. ``userCreate`` or ``userSearchAll``."""
    return JobSessionKeys(operation_key)


def relation_code_keys(relation_name: str) -> JobSessionKeys:
    """Generated code of one relation."""
    return JobSessionKeys(f"{relation_name}{_RELATION_CODE}")


def connector_fix_keys(object_class: str) -> JobSessionKeys:
    """Connector fix of one (normalized) object class."""
    return JobSessionKeys(f"{object_class}{_CONNECTOR_FIX}")


def upload_job_pointer_key(doc_id: object) -> str:
    """Pointer to the upload job that alone may publish document ``doc_id``."""
    return f"documentation.processUpload_{doc_id}_job_id"


def is_output_key(key: str) -> bool:
    """True for a ``{prefix}Output`` key with a non-empty prefix."""
    return key.endswith(_OUTPUT_SUFFIX) and key != _OUTPUT_SUFFIX


def job_pointer_key_for_output(output_key: str) -> str:
    """Return the ``JobId`` pointer guarding an ``Output`` key of the same family."""
    if not is_output_key(output_key):
        raise ValueError(f"Session result key {output_key!r} does not follow the *Output convention")
    return f"{output_key[: -len(_OUTPUT_SUFFIX)]}{_JOB_ID_SUFFIX}"


def is_attributes_output_key(key: str) -> bool:
    """True for an ``{objectClass}AttributesOutput`` key."""
    return key.endswith(f"{_ATTRIBUTES}{_OUTPUT_SUFFIX}")


def is_endpoints_output_key(key: str) -> bool:
    """True for an ``{objectClass}EndpointsOutput`` key."""
    return key.endswith(f"{_ENDPOINTS}{_OUTPUT_SUFFIX}")
