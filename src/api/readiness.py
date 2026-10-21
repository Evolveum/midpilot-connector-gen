#  Copyright (C) 2010-2026 Evolveum and contributors
#  #
#  Licensed under the EUPL-1.2 or later.
#
# Licensed under the EUPL-1.2 or later.

from fastapi import APIRouter, Response, status

from src.core.readiness import ReadinessReport, ReadinessStatus, check_readiness

router = APIRouter()


@router.get(
    "/ready",
    response_model=ReadinessReport,
    summary="Service readiness",
    description=(
        "Reports whether the service can accept work: `ok` when the database is reachable and the "
        "configured LLM endpoint answers its OpenAI-compatible model listing. The response does not "
        "say which dependency failed; that is only logged. Intended for clients such as midPoint to "
        "call before starting work. Infrastructure liveness probes use the public `/health` instead."
    ),
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ReadinessReport,
            "description": "The service is not ready: at least one dependency is unavailable.",
        },
    },
)
async def ready(response: Response) -> ReadinessReport:
    report = await check_readiness()
    if report.status is not ReadinessStatus.ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return report
