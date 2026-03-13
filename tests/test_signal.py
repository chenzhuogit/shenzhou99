"""信号模块测试"""
import pytest
from src.core.signal import Signal, SignalAction, InstrumentType


class TestSignal:
    """信号测试"""

    def test_valid_long_signal(self):
        """测试有效的做多信号"""
        signal = Signal(
            strategy_name="test",
            inst_id="BTC-USDT-SWAP",
            inst_type=InstrumentType.SWAP,
            action=SignalAction.OPEN_LONG,
            price=50000.0,
            stop_loss=49000.0,
            take_profit=52000.0,
        )
        assert signal.is_valid()
        assert signal.reward_risk_ratio == 2.0
        assert signal.risk_pct == pytest.approx(0.02)

    def test_valid_short_signal(self):
        """测试有效的做空信号"""
        signal = Signal(
            strategy_name="test",
            inst_id="BTC-USDT-SWAP",
            inst_type=InstrumentType.SWAP,
            action=SignalAction.OPEN_SHORT,
            price=50000.0,
            stop_loss=51000.0,
            take_profit=48000.0,
        )
        assert signal.is_valid()
        assert signal.reward_risk_ratio == 2.0

    def test_invalid_long_stop_above_entry(self):
        """做多信号止损高于入场价应无效"""
        signal = Signal(
            strategy_name="test",
            inst_id="BTC-USDT-SWAP",
            inst_type=InstrumentType.SWAP,
            action=SignalAction.OPEN_LONG,
            price=50000.0,
            stop_loss=51000.0,  # 止损高于入场价
        )
        assert not signal.is_valid()

    def test_invalid_short_stop_below_entry(self):
        """做空信号止损低于入场价应无效"""
        signal = Signal(
            strategy_name="test",
            inst_id="BTC-USDT-SWAP",
            inst_type=InstrumentType.SWAP,
            action=SignalAction.OPEN_SHORT,
            price=50000.0,
            stop_loss=49000.0,  # 止损低于入场价
        )
        assert not signal.is_valid()

    def test_zero_price_invalid(self):
        """价格为零应无效"""
        signal = Signal(
            strategy_name="test",
            inst_id="BTC-USDT",
            inst_type=InstrumentType.SPOT,
            action=SignalAction.BUY_SPOT,
            price=0.0,
            stop_loss=1000.0,
        )
        assert not signal.is_valid()

    def test_reward_risk_no_take_profit(self):
        """无止盈时盈亏比为0"""
        signal = Signal(
            strategy_name="test",
            inst_id="BTC-USDT-SWAP",
            inst_type=InstrumentType.SWAP,
            action=SignalAction.OPEN_LONG,
            price=50000.0,
            stop_loss=49000.0,
        )
        assert signal.reward_risk_ratio == 0.0

    def test_signal_str(self):
        """信号字符串表示"""
        signal = Signal(
            strategy_name="trend",
            inst_id="ETH-USDT-SWAP",
            inst_type=InstrumentType.SWAP,
            action=SignalAction.OPEN_LONG,
            price=3000.0,
            stop_loss=2900.0,
            take_profit=3200.0,
            reason="test signal",
        )
        s = str(signal)
        assert "trend" in s
        assert "ETH-USDT-SWAP" in s
        assert "open_long" in s
