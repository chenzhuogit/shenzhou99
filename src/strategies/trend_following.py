"""趋势追踪策略"""
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

from .base import BaseStrategy
from ..core.signal import Signal, SignalAction, InstrumentType


class TrendFollowingStrategy(BaseStrategy):
    """
    趋势追踪策略
    
    核心逻辑：
    - 多时间框架分析：大周期定方向，小周期找入场
    - EMA 金叉/死叉 + MACD 确认 + RSI 过滤
    - ATR 动态止损 + 移动止盈
    """

    def __init__(self, config: dict):
        super().__init__("trend_following", config)

        indicators = config.get("indicators", {})
        self.ema_fast = indicators.get("ema_fast", 20)
        self.ema_mid = indicators.get("ema_mid", 50)
        self.ema_slow = indicators.get("ema_slow", 200)
        self.macd_fast = indicators.get("macd_fast", 12)
        self.macd_slow = indicators.get("macd_slow", 26)
        self.macd_signal = indicators.get("macd_signal", 9)
        self.rsi_period = indicators.get("rsi_period", 14)
        self.atr_period = indicators.get("atr_period", 14)

        params = config.get("params", {})
        self.atr_stop_multiplier = params.get("atr_stop_multiplier", 2.0)
        self.trailing_stop_atr = params.get("trailing_stop_atr", 1.5)
        self.min_trend_strength = params.get("min_trend_strength", 0.6)

        # 存储各品种的K线数据
        self._kline_data: dict[str, pd.DataFrame] = {}

    def _ensure_dataframe(self, inst_id: str) -> pd.DataFrame:
        """确保有 DataFrame"""
        if inst_id not in self._kline_data:
            self._kline_data[inst_id] = pd.DataFrame(
                columns=["ts", "open", "high", "low", "close", "vol"]
            )
        return self._kline_data[inst_id]

    def _update_kline(self, inst_id: str, kline: dict):
        """更新K线数据"""
        df = self._ensure_dataframe(inst_id)

        new_row = pd.DataFrame([{
            "ts": int(kline.get("ts", 0)),
            "open": float(kline.get("o", 0)),
            "high": float(kline.get("h", 0)),
            "low": float(kline.get("l", 0)),
            "close": float(kline.get("c", 0)),
            "vol": float(kline.get("vol", 0)),
        }])

        self._kline_data[inst_id] = pd.concat([df, new_row], ignore_index=True).tail(300)

    def _calculate_ema(self, series: pd.Series, period: int) -> pd.Series:
        """计算 EMA"""
        return series.ewm(span=period, adjust=False).mean()

    def _calculate_macd(self, close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
        """计算 MACD"""
        ema_fast = self._calculate_ema(close, self.macd_fast)
        ema_slow = self._calculate_ema(close, self.macd_slow)
        macd_line = ema_fast - ema_slow
        signal_line = self._calculate_ema(macd_line, self.macd_signal)
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    def _calculate_rsi(self, close: pd.Series, period: int = 14) -> pd.Series:
        """计算 RSI"""
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.rolling(window=period).mean()
        avg_loss = loss.rolling(window=period).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """计算 ATR"""
        high = df["high"]
        low = df["low"]
        close = df["close"]

        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        return atr

    def _assess_trend(self, df: pd.DataFrame) -> tuple[str, float]:
        """
        评估趋势方向和强度
        
        Returns:
            (方向 "long"/"short"/"neutral", 强度 0-1)
        """
        close = df["close"]
        if len(close) < self.ema_slow:
            return "neutral", 0.0

        ema_fast = self._calculate_ema(close, self.ema_fast).iloc[-1]
        ema_mid = self._calculate_ema(close, self.ema_mid).iloc[-1]
        ema_slow = self._calculate_ema(close, self.ema_slow).iloc[-1]
        current_price = close.iloc[-1]

        macd_line, signal_line, histogram = self._calculate_macd(close)
        rsi = self._calculate_rsi(close, self.rsi_period)

        # 评分系统
        score = 0.0

        # EMA 排列（权重 40%）
        if ema_fast > ema_mid > ema_slow:
            score += 0.4  # 多头排列
        elif ema_fast < ema_mid < ema_slow:
            score -= 0.4  # 空头排列

        # MACD（权重 30%）
        if macd_line.iloc[-1] > signal_line.iloc[-1]:
            score += 0.15
        else:
            score -= 0.15
        if histogram.iloc[-1] > 0 and histogram.iloc[-1] > histogram.iloc[-2]:
            score += 0.15  # MACD 柱状图放大
        elif histogram.iloc[-1] < 0 and histogram.iloc[-1] < histogram.iloc[-2]:
            score -= 0.15

        # 价格相对 EMA 位置（权重 20%）
        if current_price > ema_mid:
            score += 0.2
        else:
            score -= 0.2

        # RSI 确认（权重 10%）
        current_rsi = rsi.iloc[-1]
        if 50 < current_rsi < 70:
            score += 0.1  # 偏多但未超买
        elif 30 < current_rsi < 50:
            score -= 0.1  # 偏空但未超卖

        if score > 0:
            return "long", min(abs(score), 1.0)
        elif score < 0:
            return "short", min(abs(score), 1.0)
        else:
            return "neutral", 0.0

    async def on_tick(self, inst_id: str, ticker: dict) -> Optional[Signal]:
        """Ticker 暂不处理，依赖K线触发"""
        return None

    async def on_kline(self, inst_id: str, kline: dict) -> Optional[Signal]:
        """
        K线闭合触发策略判断
        """
        if inst_id not in self.instruments:
            return None

        self._update_kline(inst_id, kline)
        df = self._kline_data[inst_id]

        if len(df) < self.ema_slow + 10:
            return None  # 数据不足

        # 评估趋势
        direction, strength = self._assess_trend(df)

        if strength < self.min_trend_strength:
            return None  # 趋势不够明确

        close = df["close"]
        current_price = close.iloc[-1]
        atr = self._calculate_atr(df, self.atr_period).iloc[-1]

        if atr <= 0 or np.isnan(atr):
            return None

        # 检测入场条件：价格回调到快速 EMA 附近
        ema_fast_val = self._calculate_ema(close, self.ema_fast).iloc[-1]
        pullback_distance = abs(current_price - ema_fast_val) / atr

        if pullback_distance > 1.0:
            return None  # 离均线太远，不是好的入场点

        # 判断产品类型
        inst_type = InstrumentType.SWAP if "SWAP" in inst_id else InstrumentType.SPOT

        if direction == "long":
            stop_loss = current_price - atr * self.atr_stop_multiplier
            take_profit = current_price + atr * self.atr_stop_multiplier * 2  # 盈亏比 2:1

            if inst_type == InstrumentType.SWAP:
                action = SignalAction.OPEN_LONG
            else:
                action = SignalAction.BUY_SPOT

            signal = Signal(
                strategy_name=self.name,
                inst_id=inst_id,
                inst_type=inst_type,
                action=action,
                price=current_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                reason=f"趋势做多 | 强度={strength:.2f} | ATR={atr:.2f}",
                confidence=strength,
            )

        elif direction == "short":
            if inst_type != InstrumentType.SWAP:
                return None  # 现货不能做空

            stop_loss = current_price + atr * self.atr_stop_multiplier
            take_profit = current_price - atr * self.atr_stop_multiplier * 2

            signal = Signal(
                strategy_name=self.name,
                inst_id=inst_id,
                inst_type=inst_type,
                action=SignalAction.OPEN_SHORT,
                price=current_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                reason=f"趋势做空 | 强度={strength:.2f} | ATR={atr:.2f}",
                confidence=strength,
            )
        else:
            return None

        self.log_signal(signal)
        return signal
