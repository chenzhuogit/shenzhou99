"""策略基类"""
from abc import ABC, abstractmethod
from typing import Optional

from loguru import logger

from ..core.signal import Signal


class BaseStrategy(ABC):
    """
    策略基类
    
    所有策略必须继承此类并实现相应方法
    """

    def __init__(self, name: str, config: dict):
        self.name = name
        self.config = config
        self.enabled = config.get("enabled", True)
        self.instruments = config.get("instruments", [])
        self._last_signal: Optional[Signal] = None

    @abstractmethod
    async def on_tick(self, inst_id: str, ticker: dict) -> Optional[Signal]:
        """
        Ticker 更新时触发
        
        Args:
            inst_id: 产品ID
            ticker: Ticker 数据
            
        Returns:
            交易信号或 None
        """
        pass

    @abstractmethod
    async def on_kline(self, inst_id: str, kline: dict) -> Optional[Signal]:
        """
        K线闭合时触发
        
        Args:
            inst_id: 产品ID
            kline: K线数据
            
        Returns:
            交易信号或 None
        """
        pass

    async def on_depth(self, inst_id: str, orderbook: dict) -> Optional[Signal]:
        """深度更新触发（可选覆盖）"""
        return None

    async def on_trade(self, inst_id: str, trade: dict) -> Optional[Signal]:
        """成交推送触发（可选覆盖）"""
        return None

    async def on_order(self, order: dict):
        """订单状态变化触发（可选覆盖）"""
        pass

    async def on_position(self, position: dict):
        """持仓变化触发（可选覆盖）"""
        pass

    def log_signal(self, signal: Signal):
        """记录信号"""
        logger.info(f"[{self.name}] 📡 {signal}")

    def __repr__(self) -> str:
        status = "ON" if self.enabled else "OFF"
        return f"Strategy({self.name} [{status}] instruments={self.instruments})"
