"""
Pure feature-access decision (Master Blueprint §48, §50).

Precedence, highest first:

  1. GLOBAL KILL SWITCH   an admin switched the feature off for EVERYONE
                          (incident / maintenance) — beats everything, including
                          a per-user grant
  2. PER-USER OVERRIDE    an explicit admin grant or revocation for one user
                          (a revocation also beats "open to all", so abusive
                          accounts can be cut off individually)
  3. OPEN TO ALL          an admin opened the feature to every user regardless
                          of plan (launch mode, promotions)
  4. PLAN / TRIAL         what the user's trial, subscription or the free tier includes

The `reason` is returned so an API error can tell the client WHY (upsell vs
temporary outage) and so analytics can see how access was decided.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

GLOBALLY_DISABLED = "globally_disabled"
USER_OVERRIDE_GRANTED = "user_override_granted"
USER_OVERRIDE_REVOKED = "user_override_revoked"
OPEN_TO_ALL = "open_to_all"
IN_PLAN = "plan"
NOT_IN_PLAN = "not_in_plan"


@dataclass(frozen=True)
class GlobalFlagState:
    killed: bool = False
    open_to_all: bool = False


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str


def decide(*, global_state: GlobalFlagState, override: Optional[bool], granted_by_plan: bool) -> Decision:
    if global_state.killed:
        return Decision(False, GLOBALLY_DISABLED)
    if override is not None:
        return Decision(override, USER_OVERRIDE_GRANTED if override else USER_OVERRIDE_REVOKED)
    if global_state.open_to_all:
        return Decision(True, OPEN_TO_ALL)
    return Decision(granted_by_plan, IN_PLAN if granted_by_plan else NOT_IN_PLAN)
