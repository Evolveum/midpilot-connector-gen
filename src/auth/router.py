# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""API key management endpoints. Accessible only with the master key."""

from uuid import UUID

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import require_master_key
from src.auth.errors import ApiKeyNotFoundError
from src.auth.keys import generate_api_key
from src.auth.schema import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyInfo,
    ApiKeyListResponse,
    ApiKeyRevokeResponse,
)
from src.core.db import get_db
from src.database.repositories.api_key_repository import ApiKeyRepository

router = APIRouter(dependencies=[Depends(require_master_key)])


@router.post(
    "",
    response_model=ApiKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Issue a new API key",
)
async def create_api_key(payload: ApiKeyCreateRequest, db: AsyncSession = Depends(get_db)) -> ApiKeyCreateResponse:
    """
    Generate and persist a new API key. The full key value is returned only in
    this response and cannot be retrieved again - only its hash is stored.
    """
    generated = generate_api_key()
    record = await ApiKeyRepository(db).create_api_key(
        name=payload.name,
        key_prefix=generated.prefix,
        key_hash=generated.hash,
    )
    return ApiKeyCreateResponse(
        api_key_id=record.api_key_id,
        name=record.name,
        key_prefix=record.key_prefix,
        created_at=record.created_at,
        revoked_at=None,
        api_key=generated.value,
        message="API key created. Store the key value securely - it cannot be retrieved again.",
    )


@router.get(
    "",
    response_model=ApiKeyListResponse,
    summary="List API keys",
)
async def list_api_keys(db: AsyncSession = Depends(get_db)) -> ApiKeyListResponse:
    """
    List all API key records (active and revoked), without key values.
    """
    records = await ApiKeyRepository(db).list_api_keys()
    return ApiKeyListResponse(
        api_keys=[
            ApiKeyInfo(
                api_key_id=record.api_key_id,
                name=record.name,
                key_prefix=record.key_prefix,
                created_at=record.created_at,
                revoked_at=record.revoked_at,
            )
            for record in records
        ],
        total=len(records),
    )


@router.delete(
    "/{api_key_id}",
    response_model=ApiKeyRevokeResponse,
    summary="Revoke an API key",
)
async def revoke_api_key(
    api_key_id: UUID = Path(..., description="API key ID"), db: AsyncSession = Depends(get_db)
) -> ApiKeyRevokeResponse:
    """
    Revoke an API key (soft delete). The key stops authenticating immediately;
    the record is kept for auditing. Sessions owned by the key become
    accessible only with the master key. Idempotent for already revoked keys.
    """
    record = await ApiKeyRepository(db).revoke_api_key(api_key_id)
    if record is None:
        raise ApiKeyNotFoundError(api_key_id)

    assert record.revoked_at is not None  # set by revoke_api_key
    return ApiKeyRevokeResponse(
        api_key_id=record.api_key_id,
        revoked_at=record.revoked_at,
        message="API key revoked successfully.",
    )
