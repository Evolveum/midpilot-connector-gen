# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from fastapi import APIRouter, Response, status

from src.core.readiness import ComponentStatus, ReadinessReport, check_readiness

router = APIRouter()


@router.get(
    "/ready",
    response_model=ReadinessReport,
    summary="Service readiness",
    description=(
        "Checks that the database is reachable and that the configured LLM endpoint answers its "
        "OpenAI-compatible model listing. Intended for clients such as midPoint to call before "
        "starting work. Infrastructure liveness probes use the public `/health` instead."
    ),
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ReadinessReport,
            "description": "At least one dependency is unavailable; `checks` shows which.",
        },
    },
)
async def ready(response: Response) -> ReadinessReport:
    report = await check_readiness()
    if report.status is not ComponentStatus.ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return report
