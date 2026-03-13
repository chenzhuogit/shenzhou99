"""
神州99 回测引擎 — 用历史 K 线验证趋势策略

用法:
    python3 -m src.backtest.backtest_engine --inst BTC-USDT-SWAP --days 30
    python3 -m src.backtest.backtest_engine --inst ETH-USDT-SWAP --days 60
    python3 -m src.backtest.backtest_engine --all --days 30
"""

import os
import sys
import asyncio
import argparse
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from dataclasses import dataclass, field

# 加入项目路径
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.exchange.auth import OKXAuth
from src.exchange.okx_client import OKXClient


# ═══ 配置 ═══

@dataclass
class BacktestConfig:
    initial_equity: float = 1500.0
    max_positions: int = 3
    fee_rate: float = 0.0005          # taker 0.05%
    max_leverage: int = 8
    min_leverage: int = 2
    max_margin_pct: float = 0.20       # 单品种最大保证金占比
    adx_threshold: float = 25.0
    min_confidence: float = 0.60
    cooldown_bars: int = 8             # 4H 的 8 根 = 32 小时
    min_rr: float = 1.5
    sl_atr_mult: float = 1.5          # 止损 ATR 倍数
    tp_atr_mult: float = 2.5          # 止盈 ATR 倍数
    trailing_enabled: bool = True
    trailing_activate_atr: float = 1.5  # 移动止盈激活（ATR倍数）
    trailing_distance_atr: float = 0.8  # 移动止盈跟随距离
    ema_fast: int = 20                  # 快速EMA周期
    ema_slow: int = 50                  # 慢速EMA周期
    max_distance_ema: float = 1.5       # 距EMA最大入场距离(ATR)


@dataclass
class Position:
    inst_id: str
    side: str          # "long" or "short"
    entry_price: float
    size: float        # 张数
    leverage: int
    sl: float
    tp: float
    entry_time: datetime
    entry_bar: int
    confidence: float
    margin: float
    peak_pnl: float = 0.0
    trailing_sl: float = 0.0


@dataclass
class Trade:
    inst_id: str
    side: str
    entry_price: float
    exit_price: float
    size: float
    leverage: int
    pnl: float         # 毛利
    fee: float
    net_pnl: float     # 净利
    entry_time: datetime
    exit_time: datetime
    bars_held: int
    exit_reason: str
    confidence: float


@dataclass
class BacktestResult:
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)
    initial_equity: float = 0
    final_equity: float = 0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0
    avg_win: float = 0
    avg_loss: float = 0
    profit_factor: float = 0
    max_drawdown_pct: float = 0
    max_drawdown_usd: float = 0
    sharpe_ratio: float = 0
    total_pnl: float = 0
    total_fee: float = 0
    net_pnl: float = 0
    avg_bars_held: float = 0
    expectancy: float = 0     # 每笔期望


# ═══ 指标计算 ═══

def calc_ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.DataFrame({
        "hl": high - low,
        "hc": abs(high - close.shift(1)),
        "lc": abs(low - close.shift(1)),
    }).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def calc_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    tr = pd.DataFrame({
        "hl": high - low,
        "hc": abs(high - close.shift(1)),
        "lc": abs(low - close.shift(1)),
    }).max(axis=1)

    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0), index=df.index)

    atr = tr.ewm(span=period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(span=period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(span=period, adjust=False).mean() / atr)
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
    return dx.ewm(span=period, adjust=False).mean()


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0).ewm(span=period, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(span=period, adjust=False).mean()
    rs = gain / (loss + 1e-10)
    return 100 - 100 / (1 + rs)


# ═══ 数据获取 ═══

async def fetch_klines(inst_id: str, bar: str, days: int) -> pd.DataFrame:
    """从 OKX 获取历史 K 线（直接 HTTP 请求，不依赖 OKXClient）"""
    import aiohttp

    all_data = []
    end_ts = ""
    base_url = "https://www.okx.com"

    total_bars_needed = days * (24 // {"1H": 1, "4H": 4, "1D": 24}.get(bar, 1))
    batches = (total_bars_needed // 300) + 1

    async with aiohttp.ClientSession() as session:
        for i in range(batches):
            url = f"{base_url}/api/v5/market/history-candles?instId={inst_id}&bar={bar}&limit=300"
            if end_ts:
                url += f"&after={end_ts}"

            async with session.get(url) as resp:
                data = await resp.json()

            if data.get("code") != "0" or not data.get("data"):
                break

            candles = data["data"]
            all_data.extend(candles)
            end_ts = candles[-1][0]

            if len(candles) < 300:
                break

            await asyncio.sleep(0.2)

    if not all_data:
        return pd.DataFrame()

    df = pd.DataFrame(all_data, columns=["ts", "open", "high", "low", "close", "vol", "volCcy", "volCcyQuote", "confirm"])
    for col in ["open", "high", "low", "close", "vol"]:
        df[col] = df[col].astype(float)
    df["ts"] = pd.to_datetime(df["ts"].astype(int), unit="ms")
    df = df.sort_values("ts").reset_index(drop=True)

    return df


# ═══ 回测核心 ═══

class BacktestEngine:
    def __init__(self, config: BacktestConfig = None):
        self.cfg = config or BacktestConfig()
        self.equity = self.cfg.initial_equity
        self.positions: list[Position] = []
        self.trades: list[Trade] = []
        self.equity_curve: list[float] = []
        self.cooldown_until: dict[str, int] = {}
        self.consecutive_losses = 0

    def _calc_leverage(self, confidence: float) -> int:
        """动态杠杆"""
        if confidence >= 0.85:
            lev = 7
        elif confidence >= 0.75:
            lev = 6
        elif confidence >= 0.65:
            lev = 5
        elif confidence >= 0.55:
            lev = 4
        else:
            lev = 3

        # 连亏惩罚
        if self.consecutive_losses >= 3:
            lev = max(self.cfg.min_leverage, lev - 2)
        elif self.consecutive_losses >= 2:
            lev = max(self.cfg.min_leverage, lev - 1)

        return min(lev, self.cfg.max_leverage)

    def _calc_margin_pct(self, confidence: float) -> float:
        """动态仓位"""
        if confidence >= 0.85:
            pct = 0.20
        elif confidence >= 0.70:
            pct = 0.15
        elif confidence >= 0.60:
            pct = 0.12
        else:
            pct = 0.08

        if self.consecutive_losses >= 3:
            pct *= 0.5
        elif self.consecutive_losses >= 2:
            pct *= 0.7

        return pct

    def _check_signal(self, df_4h: pd.DataFrame, i: int, inst_id: str, ct_val: float) -> dict | None:
        """
        在第 i 根 4H K 线上检查趋势信号

        返回 {"side", "confidence", "sl", "tp", "price"} 或 None
        """
        if i < 60:
            return None

        # 冷却
        if inst_id in self.cooldown_until and i < self.cooldown_until[inst_id]:
            return None

        # 已有同品种持仓
        for p in self.positions:
            if p.inst_id == inst_id:
                return None

        window = df_4h.iloc[:i+1]
        close = window["close"]
        current_price = float(close.iloc[-1])

        # ADX — ETH 门槛更高
        adx_series = calc_adx(window, 14)
        adx = float(adx_series.iloc[-1])
        adx_min = 30.0 if "ETH" in inst_id else self.cfg.adx_threshold
        if np.isnan(adx) or adx < adx_min:
            return None

        # EMA（可配置周期）
        ema_f = float(calc_ema(close, self.cfg.ema_fast).iloc[-1])
        ema_s = float(calc_ema(close, self.cfg.ema_slow).iloc[-1])

        # 趋势方向
        trend = "neutral"
        if ema_f > ema_s and current_price > ema_f:
            trend = "long"
        elif ema_f < ema_s and current_price < ema_f:
            trend = "short"

        if trend == "neutral":
            return None

        # ATR
        atr_series = calc_atr(window, 14)
        atr = float(atr_series.iloc[-1])
        if atr <= 0:
            return None

        # 距 EMA 距离
        distance = abs(current_price - ema_f) / atr
        if distance > self.cfg.max_distance_ema:
            return None  # 不追

        # RSI
        rsi_series = calc_rsi(close, 14)
        rsi = float(rsi_series.iloc[-1])

        # 信心
        adx_score = min((adx - 25) / 25, 1.0) * 0.35
        align_score = 0.35  # 4H 已确认
        entry_score = max(0, 1 - distance) * 0.30
        confidence = adx_score + align_score + entry_score

        min_conf = 0.65 if "ETH" in inst_id else self.cfg.min_confidence
        if confidence < min_conf:
            return None

        if trend == "long":
            if rsi > 75:
                return None
            sl = min(ema_s - atr * 0.5, current_price - atr * self.cfg.sl_atr_mult)
            tp = current_price + atr * self.cfg.tp_atr_mult
        else:
            if rsi < 25:
                return None
            sl = max(ema_s + atr * 0.5, current_price + atr * self.cfg.sl_atr_mult)
            tp = current_price - atr * self.cfg.tp_atr_mult

        rr = abs(tp - current_price) / abs(current_price - sl) if abs(current_price - sl) > 0 else 0
        if rr < self.cfg.min_rr:
            return None

        return {
            "side": trend,
            "confidence": confidence,
            "sl": sl,
            "tp": tp,
            "price": current_price,
            "atr": atr,
        }

    def _open_position(self, signal: dict, inst_id: str, ct_val: float, bar_idx: int, bar_time: datetime):
        leverage = self._calc_leverage(signal["confidence"])
        margin_pct = self._calc_margin_pct(signal["confidence"])
        margin = self.equity * margin_pct
        notional = margin * leverage
        size = notional / signal["price"] / ct_val  # 张数

        # 手续费（开仓）
        fee = notional * self.cfg.fee_rate
        self.equity -= fee

        pos = Position(
            inst_id=inst_id,
            side=signal["side"],
            entry_price=signal["price"],
            size=size,
            leverage=leverage,
            sl=signal["sl"],
            tp=signal["tp"],
            entry_time=bar_time,
            entry_bar=bar_idx,
            confidence=signal["confidence"],
            margin=margin,
        )
        self.positions.append(pos)
        self.cooldown_until[inst_id] = bar_idx + self.cfg.cooldown_bars

    def _check_exit(self, pos: Position, high: float, low: float, close: float, atr: float, bar_idx: int, bar_time: datetime) -> Trade | None:
        """检查是否触发出场"""
        exit_price = None
        exit_reason = ""

        ct_val = 0.01 if "BTC" in pos.inst_id else 0.1

        if pos.side == "long":
            # 止损
            if low <= pos.sl:
                exit_price = pos.sl
                exit_reason = "止损"
            # 止盈
            elif high >= pos.tp:
                exit_price = pos.tp
                exit_reason = "止盈"
            else:
                # 移动止盈
                current_pnl_atr = (close - pos.entry_price) / atr if atr > 0 else 0
                if current_pnl_atr > pos.peak_pnl:
                    pos.peak_pnl = current_pnl_atr

                if self.cfg.trailing_enabled and pos.peak_pnl >= self.cfg.trailing_activate_atr:
                    new_trailing = close - atr * self.cfg.trailing_distance_atr
                    if new_trailing > pos.trailing_sl:
                        pos.trailing_sl = new_trailing
                    if low <= pos.trailing_sl and pos.trailing_sl > pos.entry_price:
                        exit_price = pos.trailing_sl
                        exit_reason = "移动止盈"

        elif pos.side == "short":
            if high >= pos.sl:
                exit_price = pos.sl
                exit_reason = "止损"
            elif low <= pos.tp:
                exit_price = pos.tp
                exit_reason = "止盈"
            else:
                current_pnl_atr = (pos.entry_price - close) / atr if atr > 0 else 0
                if current_pnl_atr > pos.peak_pnl:
                    pos.peak_pnl = current_pnl_atr

                if self.cfg.trailing_enabled and pos.peak_pnl >= self.cfg.trailing_activate_atr:
                    new_trailing = close + atr * self.cfg.trailing_distance_atr
                    if pos.trailing_sl == 0 or new_trailing < pos.trailing_sl:
                        pos.trailing_sl = new_trailing
                    if high >= pos.trailing_sl and pos.trailing_sl < pos.entry_price:
                        exit_price = pos.trailing_sl
                        exit_reason = "移动止盈"

        if exit_price is None:
            return None

        # 计算盈亏
        if pos.side == "long":
            pnl = (exit_price - pos.entry_price) * pos.size * ct_val
        else:
            pnl = (pos.entry_price - exit_price) * pos.size * ct_val

        notional = exit_price * pos.size * ct_val
        fee = notional * self.cfg.fee_rate
        net = pnl - fee
        self.equity += net

        if net >= 0:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1

        return Trade(
            inst_id=pos.inst_id, side=pos.side,
            entry_price=pos.entry_price, exit_price=exit_price,
            size=pos.size, leverage=pos.leverage,
            pnl=pnl, fee=fee, net_pnl=net,
            entry_time=pos.entry_time, exit_time=bar_time,
            bars_held=bar_idx - pos.entry_bar,
            exit_reason=exit_reason,
            confidence=pos.confidence,
        )

    def run(self, df_4h: pd.DataFrame, inst_id: str) -> BacktestResult:
        """对单品种跑回测"""
        ct_val = 0.01 if "BTC" in inst_id else 0.1
        atr_series = calc_atr(df_4h, 14)

        self.equity = self.cfg.initial_equity
        self.positions = []
        self.trades = []
        self.equity_curve = []
        self.cooldown_until = {}
        self.consecutive_losses = 0

        for i in range(60, len(df_4h)):
            row = df_4h.iloc[i]
            bar_time = row["ts"] if isinstance(row["ts"], datetime) else pd.Timestamp(row["ts"])
            atr = float(atr_series.iloc[i]) if i < len(atr_series) else 0

            # 检查出场
            closed = []
            for pos in self.positions[:]:
                trade = self._check_exit(pos, float(row["high"]), float(row["low"]), float(row["close"]), atr, i, bar_time)
                if trade:
                    self.trades.append(trade)
                    closed.append(pos)
            for p in closed:
                self.positions.remove(p)

            # 检查入场
            if len(self.positions) < self.cfg.max_positions:
                signal = self._check_signal(df_4h, i, inst_id, ct_val)
                if signal:
                    self._open_position(signal, inst_id, ct_val, i, bar_time)

            # 记录净值（含浮盈）
            floating = 0
            for pos in self.positions:
                if pos.side == "long":
                    floating += (float(row["close"]) - pos.entry_price) * pos.size * ct_val
                else:
                    floating += (pos.entry_price - float(row["close"])) * pos.size * ct_val
            self.equity_curve.append(self.equity + floating)

        # 强制平掉未结束的仓位
        if self.positions and len(df_4h) > 0:
            last_row = df_4h.iloc[-1]
            last_price = float(last_row["close"])
            for pos in self.positions:
                if pos.side == "long":
                    pnl = (last_price - pos.entry_price) * pos.size * ct_val
                else:
                    pnl = (pos.entry_price - last_price) * pos.size * ct_val
                fee = last_price * pos.size * ct_val * self.cfg.fee_rate
                net = pnl - fee
                self.equity += net
                self.trades.append(Trade(
                    inst_id=pos.inst_id, side=pos.side,
                    entry_price=pos.entry_price, exit_price=last_price,
                    size=pos.size, leverage=pos.leverage,
                    pnl=pnl, fee=fee, net_pnl=net,
                    entry_time=pos.entry_time, exit_time=last_row["ts"],
                    bars_held=len(df_4h) - 1 - pos.entry_bar,
                    exit_reason="回测结束平仓",
                    confidence=pos.confidence,
                ))

        return self._calc_result()

    def _calc_result(self) -> BacktestResult:
        r = BacktestResult()
        r.initial_equity = self.cfg.initial_equity
        r.final_equity = self.equity
        r.equity_curve = self.equity_curve
        r.trades = self.trades
        r.total_trades = len(self.trades)

        if not self.trades:
            return r

        wins = [t for t in self.trades if t.net_pnl > 0]
        losses = [t for t in self.trades if t.net_pnl <= 0]
        r.wins = len(wins)
        r.losses = len(losses)
        r.win_rate = r.wins / r.total_trades if r.total_trades > 0 else 0
        r.avg_win = sum(t.net_pnl for t in wins) / len(wins) if wins else 0
        r.avg_loss = abs(sum(t.net_pnl for t in losses) / len(losses)) if losses else 0
        r.total_pnl = sum(t.pnl for t in self.trades)
        r.total_fee = sum(t.fee for t in self.trades)
        r.net_pnl = sum(t.net_pnl for t in self.trades)
        r.avg_bars_held = sum(t.bars_held for t in self.trades) / r.total_trades

        gross_profit = sum(t.net_pnl for t in wins) if wins else 0
        gross_loss = abs(sum(t.net_pnl for t in losses)) if losses else 0
        r.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        r.expectancy = r.net_pnl / r.total_trades

        # 最大回撤
        if self.equity_curve:
            peak = self.equity_curve[0]
            max_dd = 0
            max_dd_pct = 0
            for eq in self.equity_curve:
                if eq > peak:
                    peak = eq
                dd = peak - eq
                dd_pct = dd / peak if peak > 0 else 0
                if dd > max_dd:
                    max_dd = dd
                if dd_pct > max_dd_pct:
                    max_dd_pct = dd_pct
            r.max_drawdown_usd = max_dd
            r.max_drawdown_pct = max_dd_pct * 100

        # Sharpe
        if len(self.equity_curve) > 1:
            returns = pd.Series(self.equity_curve).pct_change().dropna()
            if returns.std() > 0:
                r.sharpe_ratio = (returns.mean() / returns.std()) * np.sqrt(365 * 6)  # 4H = 6根/天

        return r


def print_result(r: BacktestResult, inst_id: str, days: int):
    """打印回测结果"""
    print(f"\n{'='*60}")
    print(f"  神州99 回测报告 — {inst_id} ({days} 天)")
    print(f"{'='*60}")
    print(f"  初始资金:    ${r.initial_equity:,.2f}")
    print(f"  最终资金:    ${r.final_equity:,.2f}")
    print(f"  净利润:      ${r.net_pnl:+,.2f} ({r.net_pnl/r.initial_equity*100:+.1f}%)")
    print(f"  毛利:        ${r.total_pnl:+,.2f}")
    print(f"  手续费:      ${r.total_fee:,.2f}")
    print(f"{'─'*60}")
    print(f"  总交易:      {r.total_trades} 笔")
    print(f"  胜率:        {r.win_rate:.1%} ({r.wins}胜 / {r.losses}负)")
    print(f"  平均盈利:    ${r.avg_win:,.2f}")
    print(f"  平均亏损:    ${r.avg_loss:,.2f}")
    print(f"  盈亏比:      {r.avg_win/r.avg_loss:.2f}" if r.avg_loss > 0 else "  盈亏比:      ∞")
    print(f"  利润因子:    {r.profit_factor:.2f}" if r.profit_factor < 999 else "  利润因子:    ∞")
    print(f"  每笔期望:    ${r.expectancy:+,.2f}")
    print(f"{'─'*60}")
    print(f"  最大回撤:    ${r.max_drawdown_usd:,.2f} ({r.max_drawdown_pct:.1f}%)")
    print(f"  Sharpe:      {r.sharpe_ratio:.2f}")
    print(f"  平均持仓:    {r.avg_bars_held:.1f} 根4H ({r.avg_bars_held*4:.0f}小时)")
    print(f"{'='*60}")

    if r.trades:
        print(f"\n  最近 10 笔交易:")
        print(f"  {'方向':^4} {'入场':>10} {'出场':>10} {'盈亏':>8} {'原因':^8} {'信心':^5} {'持仓':^6}")
        print(f"  {'─'*56}")
        for t in r.trades[-10:]:
            d = "做多" if t.side == "long" else "做空"
            print(f"  {d:^4} {t.entry_price:>10,.1f} {t.exit_price:>10,.1f} "
                  f"${t.net_pnl:>+7.2f} {t.exit_reason:^8} {t.confidence:>4.0%} {t.bars_held*4:>3}h")


async def main():
    parser = argparse.ArgumentParser(description="神州99 趋势策略回测")
    parser.add_argument("--inst", type=str, default="BTC-USDT-SWAP", help="交易品种")
    parser.add_argument("--all", action="store_true", help="回测所有品种")
    parser.add_argument("--days", type=int, default=30, help="回测天数")
    parser.add_argument("--equity", type=float, default=1500.0, help="初始资金")
    args = parser.parse_args()

    instruments = ["BTC-USDT-SWAP", "ETH-USDT-SWAP"] if args.all else [args.inst]

    for inst_id in instruments:
        print(f"\n⏳ 正在获取 {inst_id} 4H K线 ({args.days} 天)...")
        df_4h = await fetch_klines(inst_id, "4H", args.days)

        if df_4h.empty:
            print(f"❌ 无法获取 {inst_id} K线数据")
            continue

        print(f"✅ 获取 {len(df_4h)} 根 4H K线 ({df_4h['ts'].iloc[0]} → {df_4h['ts'].iloc[-1]})")

        cfg = BacktestConfig(initial_equity=args.equity)
        engine = BacktestEngine(cfg)
        result = engine.run(df_4h, inst_id)
        print_result(result, inst_id, args.days)


if __name__ == "__main__":
    asyncio.run(main())
