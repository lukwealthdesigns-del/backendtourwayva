from app.modules.analytics.service import _estimate_cost_usd


def test_known_model_cost_calculation():
    cost = _estimate_cost_usd("gpt-4.1", prompt_tokens=1000, completion_tokens=1000)
    assert cost == 0.002 + 0.008


def test_unknown_model_falls_back_to_default_rates():
    cost = _estimate_cost_usd("some-future-model", prompt_tokens=1000, completion_tokens=1000)
    assert cost == 0.001 + 0.003


def test_zero_tokens_cost_zero():
    assert _estimate_cost_usd("gpt-4.1", prompt_tokens=0, completion_tokens=0) == 0.0


def test_cost_scales_linearly_with_tokens():
    cost_1k = _estimate_cost_usd("gpt-4.1-mini", prompt_tokens=1000, completion_tokens=0)
    cost_2k = _estimate_cost_usd("gpt-4.1-mini", prompt_tokens=2000, completion_tokens=0)
    assert abs(cost_2k - (cost_1k * 2)) < 1e-9
