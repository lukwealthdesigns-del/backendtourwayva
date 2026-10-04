"""
TrialService (Master Blueprint §49).

`TrialConfig` is effectively a singleton admin control panel: enabled/
disabled, duration, included features. `UserTrial` is a per-user,
one-time trial grant — the config is snapshotted onto the UserTrial
at start time, so a later admin change to duration/flags never
retroactively alters a trial already in progress (deliberate: a
trial that shrinks mid-flight because an admin edited the global
config would be a confusing, arguably unfair surprise).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import FeatureFlag
from app.core.exceptions import ConflictError, ForbiddenError
from app.db.models.monetization import TrialConfig, UserTrial
from app.modules.trials.schemas import TrialConfigUpdateRequest
from app.repositories.monetization_repository import MonetizationRepository

_DEFAULT_CONFIG_KWARGS = dict(
    is_enabled=True,
    duration_days=30,
    included_feature_flags=[f.value for f in FeatureFlag],  # launch-mode default: everything, per §49's own example
)


def _with_is_active(trial: UserTrial) -> UserTrial:
    trial.is_active = trial.expires_at > datetime.now(timezone.utc)  # type: ignore[attr-defined]
    return trial


class TrialService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = MonetizationRepository(db)

    async def get_config(self) -> TrialConfig:
        config = await self.repo.get_trial_config()
        if config is None:
            config = TrialConfig(**_DEFAULT_CONFIG_KWARGS)
            await self.repo.create_trial_config(config)
            await self.db.commit()
        return config

    async def update_config(self, payload: TrialConfigUpdateRequest) -> TrialConfig:
        config = await self.get_config()
        config.is_enabled = payload.is_enabled
        config.duration_days = payload.duration_days
        config.included_feature_flags = [f.value for f in payload.included_feature_flags]
        await self.repo.save_trial_config(config)
        await self.db.commit()
        return config

    async def get_my_trial(self, user_id: uuid.UUID) -> UserTrial | None:
        trial = await self.repo.get_user_trial(user_id)
        return _with_is_active(trial) if trial else None

    async def start_trial(self, user_id: uuid.UUID) -> UserTrial:
        config = await self.get_config()
        if not config.is_enabled:
            raise ForbiddenError("Trials are not currently available.")

        existing = await self.repo.get_user_trial(user_id)
        if existing is not None:
            raise ConflictError("You have already used your trial.")

        now = datetime.now(timezone.utc)
        trial = UserTrial(
            user_id=user_id,
            started_at=now,
            expires_at=now + timedelta(days=config.duration_days),
            included_feature_flags=list(config.included_feature_flags),
        )
        await self.repo.create_user_trial(trial)
        await self.db.commit()
        return _with_is_active(trial)

    async def start_trial_if_eligible(self, user_id: uuid.UUID) -> UserTrial | None:
        """Launch-mode behaviour (Master Blueprint §49): when trials are
        enabled, a new account gets its one-time trial automatically at
        activation. Returns None (never raises) when trials are disabled
        or the user already had one — callers use this on the
        account-activation path, where it must not block sign-in."""
        config = await self.get_config()
        if not config.is_enabled:
            return None
        if await self.repo.get_user_trial(user_id) is not None:
            return None
        return await self.start_trial(user_id)
