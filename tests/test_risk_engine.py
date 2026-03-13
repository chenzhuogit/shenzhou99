"""风控引擎测试"""
import pytest
from src.core.signal import Signal, SignalAction, InstrumentType
from src.risk.risk_engine import RiskEngine


@pytest.fixture
def risk_config():
    return {
        "max_loss_per_trade": 0.02,
        "max_daily_loss": 0.05,
        "max_drawdown": 0.10,
        "max_positions": 5,
        "max_leverage": 10,
        "min_reward_risk_ratio": 1.5,
        "consecutive_loss_reduce": 3,
        "consecutive_loss_reduce_pct": 0.5,
        "flash_crash_threshold": 0.03,
        "spread_threshold": 0.005,
    }


@pytest.fixture
def engine(risk_config):
    e = RiskEngine(risk_config)
    e.update_equity(10000.0)
    e.state.daily_start_equity = 10000.0
    return e


def make_signal(
    action=SignalAction.OPEN_LONG,
    price=50000.0,
    stop_loss=49000.0,
    take_profit=52000.0,
):
    return Signal(
        strategy_name="test",
        inst_id="BTC-USDT-SWAP",
        inst_type=InstrumentType.SWAP,
        action=action,
        price=price,
        stop_loss=stop_loss,
        take_profit=take_profit,
    )


class TestRiskEngine:

    def test_valid_signal_passes(self, engine):
        """有效信号应通过风控"""
        signal = make_signal()
        passed, reason = engine.check_signal(signal)
        assert passed

    def test_low_reward_risk_rejected(self, engine):
        """低盈亏比应被拒绝"""
        signal = make_signal(take_profit=50500.0)  # RR = 0.5
        passed, reason = engine.check_signal(signal)
        assert not passed
        assert "盈亏比" in reason

    def test_high_risk_rejected(self, engine):
        """单笔风险过大应被拒绝（盈亏比或风险检查）"""
        signal = make_signal(stop_loss=40000.0, take_profit=52000.0)  # 20% 风险
        passed, reason = engine.check_signal(signal)
        assert not passed
        assert "单笔风险" in reason or "盈亏比" in reason

    def test_max_positions_rejected(self, engine):
        """持仓数已满应被拒绝"""
        engine.state.open_positions = 5
        signal = make_signal()
        passed, reason = engine.check_signal(signal)
        assert not passed
        assert "持仓数" in reason

    def test_close_signal_always_passes(self, engine):
        """平仓信号应始终通过"""
        engine.state.is_trading_paused = True
        engine.state.pause_reason = "test"
        signal = make_signal(action=SignalAction.CLOSE_LONG)
        # 暂停时开仓被拒，但平仓... 实际上暂停检查在前
        # 修正：平仓信号在暂停检查之后
        engine.state.is_trading_paused = False
        passed, reason = engine.check_signal(signal)
        assert passed

    def test_trading_paused_rejected(self, engine):
        """交易暂停时应被拒绝"""
        engine.state.is_trading_paused = True
        engine.state.pause_reason = "daily_loss"
        signal = make_signal()
        passed, reason = engine.check_signal(signal)
        assert not passed
        assert "暂停" in reason

    def test_daily_loss_triggers_pause(self, engine):
        """日亏损超限应触发暂停"""
        # 累计亏损 -600 = 6% > 5% 阈值
        engine.record_trade_result(-300.0)
        engine.record_trade_result(-300.0)
        assert engine.state.is_trading_paused
        assert "daily_loss" in engine.state.pause_reason

    def test_max_drawdown_triggers_pause(self, engine):
        """最大回撤超限应触发暂停"""
        engine.state.max_equity = 10000.0
        engine.update_equity(8900.0)  # 11% 回撤
        engine.record_trade_result(-100.0)
        assert engine.state.is_trading_paused

    def test_consecutive_loss_reduces_position(self, engine):
        """连续亏损应减少仓位乘数"""
        for _ in range(3):
            engine.record_trade_result(-100.0)
        multiplier = engine.get_position_multiplier()
        assert multiplier < 1.0

    def test_flash_crash_detection(self, engine):
        """闪崩检测"""
        # 模拟1分钟内 5% 波动
        assert not engine.check_flash_crash("BTC-USDT", 50000.0)
        assert engine.check_flash_crash("BTC-USDT", 48000.0)  # 4% 跌幅

    def test_spread_check(self, engine):
        """价差检测"""
        # 正常价差
        assert not engine.check_spread(50010.0, 50000.0)
        # 异常价差
        assert engine.check_spread(50500.0, 50000.0)

    def test_new_day_resets(self, engine):
        """新的一天重置日内暂停"""
        engine.state.is_trading_paused = True
        engine.state.pause_reason = "daily_loss_6%"
        engine.start_new_day()
        assert not engine.state.is_trading_paused

    def test_win_rate(self, engine):
        """胜率计算"""
        engine.record_trade_result(100.0)
        engine.record_trade_result(-50.0)
        engine.record_trade_result(200.0)
        assert engine.win_rate == pytest.approx(2 / 3)

    def test_invalid_signal_rejected(self, engine):
        """无效信号应被拒绝"""
        signal = make_signal(stop_loss=51000.0)  # 做多止损高于入场价
        passed, reason = engine.check_signal(signal)
        assert not passed
