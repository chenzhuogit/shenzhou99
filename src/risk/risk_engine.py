"""风控引擎 — 神州99的安全卫士"""
import time
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

from ..core.signal import Signal, SignalAction


@dataclass
class RiskState:
    """风控状态"""
    equity: float = 0.0                    # 账户净值
    daily_pnl: float = 0.0                 # 当日盈亏
    daily_start_equity: float = 0.0        # 当日起始净值
    max_equity: float = 0.0                # 历史最高净值
    current_drawdown: float = 0.0          # 当前回撤
    open_positions: int = 0                # 当前持仓数
    consecutive_losses: int = 0            # 连续亏损次数
    total_trades: int = 0                  # 总交易次数
    winning_trades: int = 0                # 盈利交易次数
    is_trading_paused: bool = False        # 是否暂停交易
    pause_reason: str = ""                 # 暂停原因
    last_flash_check: float = 0.0          # 上次闪崩检测时间
    recent_prices: dict[str, list[tuple[float, float]]] = field(default_factory=dict)


class RiskEngine:
    """
    风控引擎
    
    铁律：活着比赚钱重要
    风控引擎拥有最高权限，可以否决任何交易信号
    """

    def __init__(self, config: dict):
        self.config = config
        self.state = RiskState()

        # 从配置加载参数
        self.max_loss_per_trade = config.get("max_loss_per_trade", 0.02)
        self.max_daily_loss = config.get("max_daily_loss", 0.05)
        self.max_drawdown = config.get("max_drawdown", 0.10)
        self.max_positions = config.get("max_positions", 5)
        self.max_leverage = config.get("max_leverage", 10)
        self.min_reward_risk = config.get("min_reward_risk_ratio", 2.0)
        self.consecutive_loss_limit = config.get("consecutive_loss_reduce", 3)
        self.consecutive_loss_reduce_pct = config.get("consecutive_loss_reduce_pct", 0.5)
        self.flash_crash_threshold = config.get("flash_crash_threshold", 0.03)
        self.spread_threshold = config.get("spread_threshold", 0.005)

    def update_equity(self, equity: float):
        """更新账户净值"""
        self.state.equity = equity
        if equity > self.state.max_equity:
            self.state.max_equity = equity
        if self.state.max_equity > 0:
            self.state.current_drawdown = (self.state.max_equity - equity) / self.state.max_equity

    def start_new_day(self):
        """新的一天，重置日内统计"""
        self.state.daily_pnl = 0.0
        self.state.daily_start_equity = self.state.equity
        if self.state.is_trading_paused and "daily" in self.state.pause_reason:
            self.state.is_trading_paused = False
            self.state.pause_reason = ""
            logger.info("📗 新的一天，解除日内暂停")

    def record_trade_result(self, pnl: float, fee: float = 0):
        """
        记录交易结果（含手续费）

        pnl: 已实现盈亏（不含手续费）
        fee: 手续费（负数）
        """
        net_pnl = pnl + fee  # 净盈亏 = 毛利 + 手续费（手续费为负）
        self.state.total_trades += 1
        self.state.daily_pnl += net_pnl

        if net_pnl > 0:
            self.state.winning_trades += 1
            self.state.consecutive_losses = 0
        else:
            self.state.consecutive_losses += 1

        # ── 熔断检查1: 绝对金额熔断（$50） ──
        circuit_limit = self.config.get("circuit_breaker_usd", 50.0)
        if self.state.daily_pnl <= -circuit_limit:
            self.state.is_trading_paused = True
            self.state.pause_reason = f"circuit_breaker_${abs(self.state.daily_pnl):.1f}"
            logger.critical(
                f"🚨 熔断触发！当日亏损 ${abs(self.state.daily_pnl):.2f} ≥ ${circuit_limit:.0f}，禁止交易"
            )

        # ── 熔断检查2: 百分比日亏损 ──
        if self.state.daily_start_equity > 0:
            daily_loss_pct = -self.state.daily_pnl / self.state.daily_start_equity
            if daily_loss_pct >= self.max_daily_loss:
                self.state.is_trading_paused = True
                self.state.pause_reason = f"daily_loss_{daily_loss_pct:.1%}"
                logger.warning(f"🛑 日亏损达到 {daily_loss_pct:.1%}，暂停交易至次日")

        # ── 熔断检查3: 最大回撤 ──
        if self.state.current_drawdown >= self.max_drawdown:
            self.state.is_trading_paused = True
            self.state.pause_reason = f"max_drawdown_{self.state.current_drawdown:.1%}"
            logger.critical(f"🚨 最大回撤 {self.state.current_drawdown:.1%}，全部平仓并暂停！")

    def manual_resume(self) -> str:
        """人工解除熔断"""
        if not self.state.is_trading_paused:
            return "当前未处于熔断状态"
        old_reason = self.state.pause_reason
        self.state.is_trading_paused = False
        self.state.pause_reason = ""
        logger.warning(f"⚠️ 人工解除熔断: {old_reason}")
        return f"已解除熔断 (原因: {old_reason})"

    def check_signal(self, signal: Signal) -> tuple[bool, str]:
        """
        审核交易信号
        
        Returns:
            (是否通过, 原因)
        """
        # ── 基本有效性 ──
        if not signal.is_valid():
            return False, "信号无效：价格或止损设置错误"

        # ── 交易暂停检查 ──
        if self.state.is_trading_paused:
            return False, f"交易已暂停：{self.state.pause_reason}"

        # ── 只审核开仓信号 ──
        if signal.action in (SignalAction.CLOSE_LONG, SignalAction.CLOSE_SHORT):
            return True, "平仓信号直接通过"

        # ── 盈亏比检查 ──
        if signal.take_profit is not None:
            rr = signal.reward_risk_ratio
            if rr < self.min_reward_risk - 0.01:  # 浮点容差
                return False, f"盈亏比不足：{rr:.2f} < {self.min_reward_risk}"

        # ── 单笔风险检查 ──
        risk_pct = signal.risk_pct
        if risk_pct > self.max_loss_per_trade:
            return False, f"单笔风险过大：{risk_pct:.2%} > {self.max_loss_per_trade:.2%}"

        # ── 持仓数量检查 ──
        if self.state.open_positions >= self.max_positions:
            return False, f"持仓数已满：{self.state.open_positions}/{self.max_positions}"

        # ── 连续亏损检查 ──
        if self.state.consecutive_losses >= self.consecutive_loss_limit:
            logger.warning(
                f"⚠️ 连续亏损 {self.state.consecutive_losses} 笔，"
                f"仓位将缩减 {self.consecutive_loss_reduce_pct:.0%}"
            )
            # 不拒绝，但会在仓位计算时缩减

        # ── 回撤检查 ──
        if self.state.current_drawdown >= self.max_drawdown * 0.8:
            logger.warning(f"⚠️ 回撤接近阈值：{self.state.current_drawdown:.1%}")

        logger.info(f"✅ 风控通过：{signal}")
        return True, "通过"

    def check_flash_crash(self, inst_id: str, price: float) -> bool:
        """
        闪崩检测
        
        Returns:
            True = 检测到异常，应暂停开仓
        """
        now = time.time()

        if inst_id not in self.state.recent_prices:
            self.state.recent_prices[inst_id] = []

        prices = self.state.recent_prices[inst_id]
        prices.append((now, price))

        # 只保留最近 60 秒的数据
        prices[:] = [(t, p) for t, p in prices if now - t <= 60]

        if len(prices) < 2:
            return False

        oldest_price = prices[0][1]
        change = abs(price - oldest_price) / oldest_price

        if change >= self.flash_crash_threshold:
            logger.warning(
                f"⚡ 闪崩检测：{inst_id} 1分钟内波动 {change:.2%}，暂停开仓"
            )
            return True

        return False

    def check_spread(self, best_ask: float, best_bid: float) -> bool:
        """
        价差检测
        
        Returns:
            True = 价差过大，不宜交易
        """
        if best_bid <= 0:
            return True
        spread = (best_ask - best_bid) / best_bid
        if spread > self.spread_threshold:
            logger.warning(f"⚠️ 价差过大：{spread:.4%} > {self.spread_threshold:.4%}")
            return True
        return False

    def get_position_multiplier(self) -> float:
        """
        获取仓位调整乘数
        
        根据连续亏损、回撤等动态调整
        """
        multiplier = 1.0

        # 连续亏损减仓
        if self.state.consecutive_losses >= self.consecutive_loss_limit:
            multiplier *= self.consecutive_loss_reduce_pct

        # 回撤越大，仓位越小
        if self.state.current_drawdown > 0.05:
            dd_factor = 1.0 - (self.state.current_drawdown / self.max_drawdown)
            multiplier *= max(dd_factor, 0.25)  # 最少保留 25%

        return multiplier

    @property
    def win_rate(self) -> float:
        """胜率"""
        if self.state.total_trades == 0:
            return 0.0
        return self.state.winning_trades / self.state.total_trades

    def status_report(self) -> str:
        """风控状态报告"""
        return (
            f"═══ 风控状态 ═══\n"
            f"净值: {self.state.equity:.2f}\n"
            f"日PnL: {self.state.daily_pnl:+.2f}\n"
            f"回撤: {self.state.current_drawdown:.2%}\n"
            f"持仓: {self.state.open_positions}/{self.max_positions}\n"
            f"连亏: {self.state.consecutive_losses}\n"
            f"胜率: {self.win_rate:.1%} ({self.state.winning_trades}/{self.state.total_trades})\n"
            f"暂停: {'是 — ' + self.state.pause_reason if self.state.is_trading_paused else '否'}\n"
            f"乘数: {self.get_position_multiplier():.2f}"
        )
