import pytest

from app.risk.risk_manager import RiskManager


def test_build_trade_plan_with_target_buy():
    plan = RiskManager().build_trade_plan_with_target(
        "US100", "BUY", entry_price=100.0, stop_loss=98.0, take_profit=103.0
    )

    assert plan.entry_price == 100.0
    assert plan.stop_loss == 98.0
    assert plan.take_profit == 103.0
    assert plan.risk_reward == pytest.approx(1.5)


def test_build_trade_plan_with_target_sell():
    plan = RiskManager().build_trade_plan_with_target(
        "US100", "SELL", entry_price=100.0, stop_loss=102.0, take_profit=97.0
    )

    assert plan.risk_reward == pytest.approx(1.5)


def test_build_trade_plan_with_target_rejects_non_positive_risk():
    with pytest.raises(ValueError):
        RiskManager().build_trade_plan_with_target(
            "US100", "BUY", entry_price=100.0, stop_loss=100.0, take_profit=103.0
        )


def test_build_trade_plan_with_target_rejects_non_positive_reward():
    with pytest.raises(ValueError):
        RiskManager().build_trade_plan_with_target(
            "US100", "BUY", entry_price=100.0, stop_loss=98.0, take_profit=100.0
        )
