"""仓位计算模块测试"""
import pytest
from src.risk.position_sizing import PositionSizer


@pytest.fixture
def sizer():
    return PositionSizer({
        "default_risk_pct": 0.01,
        "max_position_pct": 0.20,
        "kelly_fraction": 0.25,
    })


class TestPositionSizer:

    def test_fixed_fraction_basic(self, sizer):
        """基本固定比例计算"""
        pos = sizer.calculate_fixed_fraction(
            equity=10000.0,
            entry_price=50000.0,
            stop_loss=49000.0,
        )
        # 风险金额 = 10000 * 0.01 = 100
        # 每单位风险 = 50000 - 49000 = 1000
        # 仓位 = 100 / 1000 = 0.1
        # 但受 max_position_pct 限制: (10000 * 0.20) / 50000 = 0.04
        assert pos == pytest.approx(0.04)

    def test_fixed_fraction_capped(self, sizer):
        """仓位不超过最大限制"""
        pos = sizer.calculate_fixed_fraction(
            equity=10000.0,
            entry_price=100.0,
            stop_loss=99.0,
            risk_pct=0.5,  # 故意设很大
        )
        max_pos = (10000 * 0.20) / 100  # = 20
        assert pos <= max_pos

    def test_zero_risk_returns_zero(self, sizer):
        """止损等于入场价返回0"""
        pos = sizer.calculate_fixed_fraction(
            equity=10000.0,
            entry_price=50000.0,
            stop_loss=50000.0,
        )
        assert pos == 0.0

    def test_kelly_positive_edge(self, sizer):
        """正期望的凯利计算"""
        pos = sizer.calculate_kelly(
            equity=10000.0,
            entry_price=50000.0,
            win_rate=0.5,
            avg_win=200.0,
            avg_loss=100.0,
        )
        # Kelly = (0.5 * 2 - 0.5) / 2 = 0.25
        # Adjusted = 0.25 * 0.25 = 0.0625
        # Position value = 10000 * 0.0625 = 625
        # Position = 625 / 50000 = 0.0125
        assert pos > 0
        assert pos == pytest.approx(0.0125)

    def test_kelly_negative_edge(self, sizer):
        """负期望的凯利应返回0"""
        pos = sizer.calculate_kelly(
            equity=10000.0,
            entry_price=50000.0,
            win_rate=0.3,
            avg_win=100.0,
            avg_loss=100.0,
        )
        assert pos == 0.0

    def test_combined_calculation(self, sizer):
        """综合计算取较小值"""
        pos = sizer.calculate(
            equity=10000.0,
            entry_price=50000.0,
            stop_loss=49000.0,
            risk_multiplier=1.0,
            win_rate=0.5,
            avg_win=200.0,
            avg_loss=100.0,
        )
        assert pos > 0

    def test_risk_multiplier_reduces(self, sizer):
        """风控乘数应减少仓位"""
        pos_full = sizer.calculate(
            equity=10000.0,
            entry_price=50000.0,
            stop_loss=49000.0,
            risk_multiplier=1.0,
        )
        pos_reduced = sizer.calculate(
            equity=10000.0,
            entry_price=50000.0,
            stop_loss=49000.0,
            risk_multiplier=0.5,
        )
        assert pos_reduced == pytest.approx(pos_full * 0.5)
