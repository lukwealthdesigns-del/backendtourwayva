"""Admin-controlled limits for itinerary generation (long trips, monthly caps, cost alert).

Effectively a singleton, like TrialConfig: the service always reads the most recently updated row and seeds the defaults on
first read. The rules that turn it into limits for one user live in modules/planning/policy_rules.py.
"""
from __future__ import annotations

from sqlalchemy import Boolean, Float, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class PlanningPolicy(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "planning_policy"

    # Growth mode: while ON, every user gets the growth limits. Switch it OFF and the plan-based limits apply.
    growth_mode: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    growth_max_days: Mapped[int] = mapped_column(Integer, default=120, nullable=False)
    premium_max_days: Mapped[int] = mapped_column(Integer, default=180, nullable=False)
    free_max_days: Mapped[int] = mapped_column(Integer, default=14, nullable=False)
    # Trips up to this many days are planned in detail in one go; longer ones get a route plus detail one chunk at a time.
    full_detail_max_days: Mapped[int] = mapped_column(Integer, default=14, nullable=False)
    chunk_days: Mapped[int] = mapped_column(Integer, default=7, nullable=False)
    growth_monthly_generations: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    premium_monthly_generations: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    free_monthly_generations: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    cost_alert_usd: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
