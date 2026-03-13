"""交易信号定义"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class SignalSide(Enum):
    """交易方向"""
    BUY = "buy"
    SELL = "sell"


class SignalAction(Enum):
    """信号动作"""
    OPEN_LONG = "open_long"      # 开多
    CLOSE_LONG = "close_long"    # 平多
    OPEN_SHORT = "open_short"    # 开空
    CLOSE_SHORT = "close_short"  # 平空
    BUY_SPOT = "buy_spot"        # 现货买入
    SELL_SPOT = "sell_spot"      # 现货卖出


class InstrumentType(Enum):
    """产品类型"""
    SPOT = "SPOT"
    SWAP = "SWAP"        # 永续合约
    FUTURES = "FUTURES"  # 交割合约


@dataclass
class Signal:
    """交易信号"""
    strategy_name: str           # 策略名称
    inst_id: str                 # 产品ID (如 BTC-USDT-SWAP)
    inst_type: InstrumentType    # 产品类型
    action: SignalAction         # 动作
    price: float                 # 建议价格
    stop_loss: float             # 止损价
    take_profit: Optional[float] = None  # 止盈价
    size: Optional[float] = None  # 建议数量（由仓位管理覆盖）
    reason: str = ""             # 开仓理由
    confidence: float = 0.5      # 信心度 0-1
    timestamp: float = field(default_factory=time.time)

    @property
    def reward_risk_ratio(self) -> float:
        """计算盈亏比"""
        if self.take_profit is None:
            return 0.0
        risk = abs(self.price - self.stop_loss)
        if risk == 0:
            return 0.0
        reward = abs(self.take_profit - self.price)
        return reward / risk

    @property
    def risk_pct(self) -> float:
        """计算风险百分比"""
        if self.price == 0:
            return 0.0
        return abs(self.price - self.stop_loss) / self.price

    def is_valid(self) -> bool:
        """基础有效性检查"""
        if self.stop_loss <= 0 or self.price <= 0:
            return False
        # 多头：止损必须低于入场价
        if self.action in (SignalAction.OPEN_LONG, SignalAction.BUY_SPOT):
            if self.stop_loss >= self.price:
                return False
        # 空头：止损必须高于入场价
        if self.action == SignalAction.OPEN_SHORT:
            if self.stop_loss <= self.price:
                return False
        return True

    def __str__(self) -> str:
        rr = f"RR={self.reward_risk_ratio:.1f}" if self.take_profit else "RR=N/A"
        return (
            f"Signal({self.strategy_name} | {self.inst_id} | "
            f"{self.action.value} @ {self.price} | "
            f"SL={self.stop_loss} TP={self.take_profit} | {rr} | "
            f"reason: {self.reason})"
        )
