"""
BroadcastService (Master Blueprint §53): "Use background jobs. Do not
send thousands of emails inside an API request."

`create_broadcast()` only writes a `BroadcastJob` row (status=pending)
and enqueues a Celery task — it returns immediately. The actual
segment resolution and per-recipient sending happens in
app/workers/broadcast_tasks.py, which runs in the separate `worker`
container (see docker-compose.yml), never inside the request/response
cycle of the API server.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import AuditResult, BroadcastStatus
from app.core.exceptions import NotFoundError
from app.db.models.admin import BroadcastJob
from app.modules.admin.admin_service import AdminService
from app.modules.admin.schemas import CreateBroadcastRequest
from app.repositories.admin_repository import AdminRepository


class BroadcastService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = AdminRepository(db)
        self.admin_service = AdminService(db)

    async def create_broadcast(self, *, actor_id: uuid.UUID, payload: CreateBroadcastRequest) -> BroadcastJob:
        await self.admin_service.require_permission(user_id=actor_id, permission="broadcast:send")

        job = BroadcastJob(
            sender_admin_id=actor_id, segment=payload.segment, message=payload.message,
            channel=payload.channel, status=BroadcastStatus.PENDING,
        )
        await self.repo.create_broadcast(job)

        await self.admin_service.log(
            admin_user_id=actor_id, action="broadcast.create", target_type="broadcast_job",
            target_id=str(job.id), result=AuditResult.SUCCESS,
            metadata={"segment": payload.segment.value, "channel": payload.channel.value},
        )
        await self.db.commit()

        # Enqueue the actual send as a background job — see
        # app/workers/broadcast_tasks.py. Import kept local to avoid
        # importing Celery machinery on every request that merely
        # imports this service.
        from app.workers.broadcast_tasks import send_broadcast_task

        send_broadcast_task.delay(str(job.id))

        return job

    async def get_broadcast(self, *, actor_id: uuid.UUID, broadcast_id: uuid.UUID) -> BroadcastJob:
        await self.admin_service.require_permission(user_id=actor_id, permission="broadcast:send")

        job = await self.repo.get_broadcast(broadcast_id)
        if job is None:
            raise NotFoundError("Broadcast not found.")
        return job
