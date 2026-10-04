"""User memory endpoints (Master Blueprint §37)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, require_feature
from app.core.constants import FeatureFlag
from app.db.models.user import User
from app.db.session import get_db
from app.modules.memory.schemas import MemoryCreateRequest, MemoryResponse, MemoryUpdateRequest
from app.modules.memory.service import MemoryService

router = APIRouter(prefix="/memory", tags=["Memory"])


@router.post("", response_model=MemoryResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature(FeatureFlag.MEMORY))])
async def create_memory(
    payload: MemoryCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    memory = await MemoryService(db).create(user_id=current_user.id, payload=payload)
    return MemoryResponse.model_validate(memory)


@router.get("", response_model=list[MemoryResponse])
async def list_memories(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    memories = await MemoryService(db).list_for_user(current_user.id)
    return [MemoryResponse.model_validate(m) for m in memories]


@router.patch("/{memory_id}", response_model=MemoryResponse)
async def update_memory(
    memory_id: uuid.UUID,
    payload: MemoryUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Also used to disable/re-enable a memory: PATCH { "is_enabled": false }."""
    memory = await MemoryService(db).update(memory_id=memory_id, user_id=current_user.id, payload=payload)
    return MemoryResponse.model_validate(memory)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await MemoryService(db).delete(memory_id=memory_id, user_id=current_user.id)
