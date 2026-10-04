"""
EntitlementService (Master Blueprint §48, §50): "Do not hard-code
`if premium:`. Instead use EntitlementService."

This is the ONE place in the codebase that decides whether a user has access to
a feature. The decision itself is the pure `rules.decide` (kill switch → per-user
override → open-to-all → plan/trial); this service gathers its inputs:

  * global admin settings   (`feature_flags`: kill switch / open to all) — cached in
                            Redis for a few seconds and invalidated the moment an
                            admin changes one, so a kill switch takes effect at once
  * per-user overrides      (`feature_flag_overrides`) — never cached
  * plan / trial            an active (non-expired) UserTrial's snapshotted flags,
                            else the active Subscription's plan flags, else the free
                            tier. Trial and subscription flags are NOT unioned.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import FREE_TIER_DEFAULT_FLAGS, FeatureFlag
from app.core.exceptions import FeatureUnavailableError, ForbiddenError
from app.db.models.monetization import FeatureFlagOverride, FeatureFlagSetting
from app.modules.entitlements.rules import GLOBALLY_DISABLED, Decision, GlobalFlagState, decide
from app.repositories.monetization_repository import MonetizationRepository
from app.services.cache_service import CacheService

GLOBAL_FLAGS_CACHE_KEY = "entitlements:global_flags"
GLOBAL_FLAGS_CACHE_TTL_SECONDS = 15


class EntitlementService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = MonetizationRepository(db)

    # ------------------------------------------------------------------
    # Inputs
    # ------------------------------------------------------------------
    async def global_states(self) -> dict[str, GlobalFlagState]:
        cached = await CacheService.get_json(GLOBAL_FLAGS_CACHE_KEY)
        if cached is None:
            rows = await self.repo.list_flag_settings()
            cached = {r.flag.value: {"killed": r.is_killed, "open_to_all": r.is_open_to_all} for r in rows}
            await CacheService.set_json(GLOBAL_FLAGS_CACHE_KEY, cached, GLOBAL_FLAGS_CACHE_TTL_SECONDS)
        return {flag: GlobalFlagState(**state) for flag, state in cached.items()}

    async def _base_granted_flags(self, user_id: uuid.UUID) -> tuple[set[str], str]:
        """Returns (granted_flag_values, source_label) before overrides and global settings."""
        trial = await self.repo.get_user_trial(user_id)
        if trial is not None and trial.expires_at > datetime.now(timezone.utc):
            return set(trial.included_feature_flags), "trial"

        subscription = await self.repo.get_active_subscription(user_id)
        if subscription is not None:
            plan = await self.repo.get_plan(subscription.plan_id)
            if plan is not None and plan.is_active:
                return set(plan.included_feature_flags), "subscription"

        return {f.value for f in FREE_TIER_DEFAULT_FLAGS}, "free"

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------
    async def decision(self, user_id: uuid.UUID, flag: FeatureFlag) -> Decision:
        state = (await self.global_states()).get(flag.value, GlobalFlagState())
        if state.killed:                                   # nothing else can matter: skip the queries
            return decide(global_state=state, override=None, granted_by_plan=False)

        override_row = await self.repo.get_override(user_id, flag.value)
        override = override_row.is_enabled if override_row is not None else None
        granted = False
        if override is None and not state.open_to_all:
            granted = flag.value in (await self._base_granted_flags(user_id))[0]
        return decide(global_state=state, override=override, granted_by_plan=granted)

    async def has_feature(self, user_id: uuid.UUID, flag: FeatureFlag) -> bool:
        return (await self.decision(user_id, flag)).allowed

    async def require(self, user_id: uuid.UUID, flag: FeatureFlag) -> None:
        """Raise unless the user may use `flag`:
          kill switch   -> 503 feature_unavailable (temporary; not an upsell)
          otherwise     -> 403 with details.reason ("not_in_plan" / "user_override_revoked")
        Used by the `require_feature` endpoint dependency AND by background work that
        re-checks entitlement when it actually runs."""
        decision = await self.decision(user_id, flag)
        if decision.allowed:
            return
        details = {"required_feature": flag.value, "reason": decision.reason}
        if decision.reason == GLOBALLY_DISABLED:
            raise FeatureUnavailableError("This feature is temporarily unavailable. Please try again later.", details=details)
        raise ForbiddenError(f"Your current plan does not include the '{flag.value}' feature.", details=details)

    async def resolve_all(self, user_id: uuid.UUID) -> tuple[dict[str, bool], str]:
        states = await self.global_states()
        granted, source = await self._base_granted_flags(user_id)
        overrides = {o.flag.value: o.is_enabled for o in await self.repo.list_overrides_for_user(user_id)}
        flags = {
            flag.value: decide(
                global_state=states.get(flag.value, GlobalFlagState()),
                override=overrides.get(flag.value),
                granted_by_plan=flag.value in granted,
            ).allowed
            for flag in FeatureFlag
        }
        return flags, source

    async def globally_disabled(self) -> list[str]:
        return sorted(flag for flag, state in (await self.global_states()).items() if state.killed)

    # ------------------------------------------------------------------
    # Admin writes (authorization is the CALLER's job — see the admin endpoints)
    # ------------------------------------------------------------------
    async def set_override(self, *, user_id: uuid.UUID, flag: FeatureFlag, is_enabled: bool) -> FeatureFlagOverride:
        """Grant/revoke a per-user feature override. Only the admin endpoint calls this
        (`plans:manage`, audit-logged). Never expose it to a user for their own account."""
        existing = await self.repo.get_override(user_id, flag.value)
        if existing is not None:
            existing.is_enabled = is_enabled
            await self.repo.save_override(existing)
            await self.db.commit()
            return existing

        override = FeatureFlagOverride(user_id=user_id, flag=flag, is_enabled=is_enabled)
        await self.repo.create_override(override)
        await self.db.commit()
        return override

    async def list_global_settings(self) -> dict[str, FeatureFlagSetting]:
        return {s.flag.value: s for s in await self.repo.list_flag_settings()}

    async def set_global_state(
        self,
        *,
        flag: FeatureFlag,
        actor_id: uuid.UUID,
        is_killed: bool | None = None,
        is_open_to_all: bool | None = None,
        note: str | None = None,
        note_provided: bool = False,
    ) -> FeatureFlagSetting:
        """Update the global toggle (only the fields provided) and invalidate the
        cache so the change is felt immediately by every API replica. Authorization
        and audit logging are the CALLER's job (`flags:manage`)."""
        setting = await self.repo.get_flag_setting(flag.value)
        if setting is None:
            setting = await self.repo.create_flag_setting(
                FeatureFlagSetting(flag=flag, is_killed=False, is_open_to_all=False)
            )
        if is_killed is not None:
            setting.is_killed = is_killed
        if is_open_to_all is not None:
            setting.is_open_to_all = is_open_to_all
        if note_provided:
            setting.note = note
        setting.updated_by = actor_id
        await self.repo.save_flag_setting(setting)
        await self.db.commit()
        await CacheService.delete(GLOBAL_FLAGS_CACHE_KEY)
        return setting
