"""仓位计算模块"""
import math
from loguru import logger


class PositionSizer:
    """
    仓位计算器
    
    结合固定比例法和凯利公式，取较小值
    """

    def __init__(self, config: dict):
        self.default_risk_pct = config.get("default_risk_pct", 0.01)
        self.max_position_pct = config.get("max_position_pct", 0.20)
        self.kelly_fraction = config.get("kelly_fraction", 0.25)  # 凯利缩减因子

    def calculate_fixed_fraction(
        self,
        equity: float,
        entry_price: float,
        stop_loss: float,
        risk_pct: float = None,
    ) -> float:
        """
        固定比例法计算仓位
        
        仓位 = (净值 × 风险比例) / (入场价 - 止损价)
        
        Args:
            equity: 账户净值
            entry_price: 入场价
            stop_loss: 止损价
            risk_pct: 风险比例（默认使用配置值）
            
        Returns:
            建议仓位大小（以标的计价）
        """
        if risk_pct is None:
            risk_pct = self.default_risk_pct

        risk_per_unit = abs(entry_price - stop_loss)
        if risk_per_unit == 0:
            logger.warning("止损价等于入场价，无法计算仓位")
            return 0.0

        risk_amount = equity * risk_pct
        position = risk_amount / risk_per_unit

        # 限制最大仓位
        max_position = (equity * self.max_position_pct) / entry_price
        position = min(position, max_position)

        return position

    def calculate_kelly(
        self,
        equity: float,
        entry_price: float,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
    ) -> float:
        """
        凯利公式计算仓位
        
        Kelly = (胜率 × 盈亏比 - (1 - 胜率)) / 盈亏比
        实际使用 Kelly × fraction（保守系数）
        
        Args:
            equity: 账户净值
            entry_price: 入场价
            win_rate: 历史胜率
            avg_win: 平均盈利
            avg_loss: 平均亏损
            
        Returns:
            建议仓位大小
        """
        if avg_loss == 0 or win_rate <= 0:
            return 0.0

        rr_ratio = avg_win / avg_loss
        kelly = (win_rate * rr_ratio - (1 - win_rate)) / rr_ratio

        if kelly <= 0:
            logger.info(f"Kelly 值为负 ({kelly:.4f})，期望为负，不建议交易")
            return 0.0

        # 使用缩减后的 Kelly
        adjusted_kelly = kelly * self.kelly_fraction
        position_value = equity * adjusted_kelly
        position = position_value / entry_price

        # 限制最大仓位
        max_position = (equity * self.max_position_pct) / entry_price
        position = min(position, max_position)

        logger.debug(
            f"Kelly: raw={kelly:.4f} adjusted={adjusted_kelly:.4f} "
            f"position={position:.6f}"
        )
        return position

    def calculate(
        self,
        equity: float,
        entry_price: float,
        stop_loss: float,
        risk_multiplier: float = 1.0,
        win_rate: float = 0.0,
        avg_win: float = 0.0,
        avg_loss: float = 0.0,
    ) -> float:
        """
        综合仓位计算（取固定比例和凯利的较小值）
        
        Args:
            equity: 账户净值
            entry_price: 入场价
            stop_loss: 止损价
            risk_multiplier: 风控乘数（连续亏损/回撤时缩减）
            win_rate: 历史胜率
            avg_win: 平均盈利
            avg_loss: 平均亏损
            
        Returns:
            最终仓位大小
        """
        # 固定比例法
        fixed_pos = self.calculate_fixed_fraction(equity, entry_price, stop_loss)

        # 如果有足够历史数据，使用凯利公式
        kelly_pos = float('inf')
        if win_rate > 0 and avg_win > 0 and avg_loss > 0:
            kelly_pos = self.calculate_kelly(equity, entry_price, win_rate, avg_win, avg_loss)
            if kelly_pos == 0:
                return 0.0

        # 取较小值
        position = min(fixed_pos, kelly_pos)

        # 应用风控乘数
        position *= risk_multiplier

        logger.info(
            f"仓位计算: fixed={fixed_pos:.6f} kelly={kelly_pos:.6f} "
            f"multiplier={risk_multiplier:.2f} → final={position:.6f}"
        )
        return position
