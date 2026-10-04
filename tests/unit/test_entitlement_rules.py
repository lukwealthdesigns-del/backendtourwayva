"""Pure feature-access decision: kill switch > per-user override > open-to-all > plan."""
import itertools

from app.modules.entitlements.rules import (
    Decision,
    GLOBALLY_DISABLED,
    IN_PLAN,
    NOT_IN_PLAN,
    OPEN_TO_ALL,
    USER_OVERRIDE_GRANTED,
    USER_OVERRIDE_REVOKED,
    GlobalFlagState,
    decide,
)

NORMAL = GlobalFlagState()


def test_plan_decides_when_nothing_else_applies():
    assert decide(global_state=NORMAL, override=None, granted_by_plan=True) == Decision(True, IN_PLAN)
    assert decide(global_state=NORMAL, override=None, granted_by_plan=False) == Decision(False, NOT_IN_PLAN)


def test_a_user_override_beats_the_plan_in_both_directions():
    assert decide(global_state=NORMAL, override=True, granted_by_plan=False) == Decision(True, USER_OVERRIDE_GRANTED)
    assert decide(global_state=NORMAL, override=False, granted_by_plan=True) == Decision(False, USER_OVERRIDE_REVOKED)


def test_open_to_all_grants_everyone_without_a_plan():
    state = GlobalFlagState(open_to_all=True)
    assert decide(global_state=state, override=None, granted_by_plan=False) == Decision(True, OPEN_TO_ALL)


def test_a_per_user_revocation_still_beats_open_to_all_so_abusive_accounts_can_be_cut_off():
    state = GlobalFlagState(open_to_all=True)
    assert decide(global_state=state, override=False, granted_by_plan=False) == Decision(False, USER_OVERRIDE_REVOKED)


def test_the_kill_switch_beats_everything():
    killed = GlobalFlagState(killed=True, open_to_all=True)
    for override, plan in itertools.product((None, True, False), (True, False)):
        assert decide(global_state=killed, override=override, granted_by_plan=plan) == Decision(False, GLOBALLY_DISABLED)


def test_exhaustive_table_matches_the_documented_precedence():
    for killed, open_all, override, plan in itertools.product((False, True), (False, True), (None, True, False), (False, True)):
        result = decide(global_state=GlobalFlagState(killed, open_all), override=override, granted_by_plan=plan)
        if killed:
            expected = False
        elif override is not None:
            expected = override
        elif open_all:
            expected = True
        else:
            expected = plan
        assert result.allowed is expected, (killed, open_all, override, plan)
