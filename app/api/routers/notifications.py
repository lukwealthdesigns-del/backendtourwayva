"""Notification endpoints (Master Blueprint §46-47)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.models.user import User
from app.db.session import get_db
from app.modules.notifications.schemas import (
    NotificationPreferencesResponse,
    NotificationPreferencesUpdateRequest,
    NotificationResponse,
)
from app.modules.notifications.service import NotificationService

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("", response_model=list[NotificationResponse])
async def list_my_notifications(
    unread_only: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    notifications = await NotificationService(db).list_my_notifications(current_user.id, unread_only=unread_only)
    return [NotificationResponse.model_validate(n) for n in notifications]


@router.post("/read-all")
async def mark_all_notifications_read(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Marks every unread notification as read. Returns `{updated: n}`."""
    return {"updated": await NotificationService(db).mark_all_read(current_user.id)}


@router.delete("", status_code=status.HTTP_200_OK)
async def clear_notifications(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Deletes ALL of the caller's notifications. Returns `{deleted: n}`."""
    return {"deleted": await NotificationService(db).clear_all(current_user.id)}


@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification(
    notification_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Deletes one of the caller's notifications (404 for anyone else's or an unknown id)."""
    await NotificationService(db).delete(notification_id=notification_id, user_id=current_user.id)


@router.post("/{notification_id}/read", response_model=NotificationResponse)
async def mark_notification_read(
    notification_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    notification = await NotificationService(db).mark_read(notification_id=notification_id, user_id=current_user.id)
    return NotificationResponse.model_validate(notification)


@router.get("/preferences", response_model=NotificationPreferencesResponse)
async def get_preferences(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    prefs = await NotificationService(db).get_preferences(current_user.id)
    return NotificationPreferencesResponse.model_validate(prefs)


@router.put("/preferences", response_model=NotificationPreferencesResponse)
async def update_preferences(
    payload: NotificationPreferencesUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    prefs = await NotificationService(db).update_preferences(
        user_id=current_user.id, email_enabled=payload.email_enabled, in_app_enabled=payload.in_app_enabled
    )
    return NotificationPreferencesResponse.model_validate(prefs)
