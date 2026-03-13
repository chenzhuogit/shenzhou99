"""神州99 核心交易引擎 — 实盘交易"""
import os
import sys
import asyncio
import time
import json
from typing import Optional
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

import numpy as np
import pandas as pd
from loguru import logger

from src.exchange.auth import OKXAuth
from src.exchange.okx_client import OKXClient
from src.exchange.okx_websocket import OKXWebSocket
from src.data.database import Database
from src.data.dao import (
    AccountDAO, PositionDAO, OrderDAO, TradeDAO,
    SignalDAO, RiskLogDAO, SystemLogDAO, ModuleStatusDAO,
    FundingRateDAO, ConfigDAO,
)
from src.core.signal import Signal, SignalAction, InstrumentType
from src.risk.risk_engine import RiskEngine
from src.risk.position_sizing import PositionSizer
from src.advisor.strategy_advisor import StrategyAdvisor


class TradingEngine:
    """
    神州99 核心交易引擎
    
    核心目标：抓住每一个交易机会，利润最大化，损失最小化
    """

    def __init__(self):
        self.api_key = os.getenv("OKX_API_KEY", "")
        self.secret_key = os.getenv("OKX_SECRET_KEY", "")
        self.passphrase = os.getenv("OKX_PASSPHRASE", "")

        self.auth = OKXAuth(self.api_key, self.secret_key, self.passphrase, simulated=False)
        self.client = OKXClient(self.auth, simulated=False)

        # WebSocket
        self.ws_public = OKXWebSocket("wss://ws.okx.com:8443/ws/v5/public", name="pub")
        self.ws_business = OKXWebSocket("wss://ws.okx.com:8443/ws/v5/business", name="biz")
        self.ws_private = OKXWebSocket("wss://ws.okx.com:8443/ws/v5/private", auth=self.auth, name="pri")

        # 风控
        self.risk = RiskEngine({
            "max_loss_per_trade": 0.03,     # 单笔最大亏损 3%（小资金适当放宽）
            "max_daily_loss": 0.08,         # 日最大亏损 8%
            "max_drawdown": 0.15,           # 最大回撤 15%
            "circuit_breaker_usd": 50.0,    # 熔断线: 当日亏损$50禁止交易
            "max_positions": 5,             # 最多5个持仓（15品种分散风险）
            "max_leverage": 10,             # 最大杠杆 10x
            "min_reward_risk_ratio": 1.5,
            "consecutive_loss_reduce": 3,
            "consecutive_loss_reduce_pct": 0.5,
            "flash_crash_threshold": 0.03,
            "spread_threshold": 0.005,
        })

        # 仓位计算（保守稳健）
        self.sizer = PositionSizer({
            "default_risk_pct": 0.02,     # 单笔风险 2%
            "max_position_pct": 0.15,     # 单品种最大 15% 保证金
            "kelly_fraction": 0.35,
        })

        # 合约面值
        # 成交额 Top 100 USDT 永续合约 ctVal 映射（自动从 OKX API 获取）
        self._ct_val = {
            "ETH-USDT-SWAP": 0.1, "BTC-USDT-SWAP": 0.01, "SOL-USDT-SWAP": 1.0,
            "TRUMP-USDT-SWAP": 0.1, "DOGE-USDT-SWAP": 1000.0, "RIVER-USDT-SWAP": 0.1,
            "XRP-USDT-SWAP": 100.0, "XAU-USDT-SWAP": 0.001, "PEPE-USDT-SWAP": 10000000.0,
            "HYPE-USDT-SWAP": 0.1, "TURBO-USDT-SWAP": 10000.0, "PI-USDT-SWAP": 1.0,
            "SUI-USDT-SWAP": 1.0, "ENSO-USDT-SWAP": 1.0, "ZEC-USDT-SWAP": 0.01,
            "ADA-USDT-SWAP": 100.0, "BNB-USDT-SWAP": 0.01, "RESOLV-USDT-SWAP": 10.0,
            "TAO-USDT-SWAP": 0.01, "TRIA-USDT-SWAP": 100.0, "CL-USDT-SWAP": 0.1,
            "PUMP-USDT-SWAP": 1000.0, "FIL-USDT-SWAP": 0.1, "XAG-USDT-SWAP": 0.01,
            "LINK-USDT-SWAP": 1.0, "BCH-USDT-SWAP": 0.1, "PIPPIN-USDT-SWAP": 10.0,
            "XPL-USDT-SWAP": 10.0, "AVAX-USDT-SWAP": 1.0, "AAVE-USDT-SWAP": 0.1,
            "WLFI-USDT-SWAP": 10.0, "RENDER-USDT-SWAP": 1.0, "WLD-USDT-SWAP": 1.0,
            "UNI-USDT-SWAP": 1.0, "AVNT-USDT-SWAP": 1.0, "PENGU-USDT-SWAP": 100.0,
            "NEAR-USDT-SWAP": 10.0, "DOT-USDT-SWAP": 1.0, "LTC-USDT-SWAP": 1.0,
            "ROBO-USDT-SWAP": 100.0, "WIF-USDT-SWAP": 1.0, "SHIB-USDT-SWAP": 1000000.0,
            "FLOW-USDT-SWAP": 10.0, "MOODENG-USDT-SWAP": 10.0, "GALA-USDT-SWAP": 10.0,
            "HUMA-USDT-SWAP": 100.0, "NIGHT-USDT-SWAP": 100.0, "ICP-USDT-SWAP": 0.01,
            "ASTER-USDT-SWAP": 1.0, "OKB-USDT-SWAP": 0.01, "CRV-USDT-SWAP": 1.0,
            "BARD-USDT-SWAP": 1.0, "RAVE-USDT-SWAP": 10.0, "OP-USDT-SWAP": 1.0,
            "BEAT-USDT-SWAP": 10.0, "ARB-USDT-SWAP": 10.0, "OPN-USDT-SWAP": 10.0,
            "APT-USDT-SWAP": 1.0, "ETC-USDT-SWAP": 10.0, "SAHARA-USDT-SWAP": 10.0,
            "LIT-USDT-SWAP": 1.0, "MMT-USDT-SWAP": 10.0, "IP-USDT-SWAP": 1.0,
            "VIRTUAL-USDT-SWAP": 1.0, "XLM-USDT-SWAP": 100.0, "ENA-USDT-SWAP": 10.0,
            "F-USDT-SWAP": 100.0, "KAT-USDT-SWAP": 100.0, "BONK-USDT-SWAP": 100000.0,
            "ORDI-USDT-SWAP": 0.1, "TRX-USDT-SWAP": 1000.0, "HBAR-USDT-SWAP": 100.0,
            "CFX-USDT-SWAP": 10.0, "FARTCOIN-USDT-SWAP": 1.0, "CHZ-USDT-SWAP": 10.0,
            "BERA-USDT-SWAP": 0.1, "DOOD-USDT-SWAP": 1000.0, "SATS-USDT-SWAP": 10000000.0,
            "SIGN-USDT-SWAP": 100.0, "AR-USDT-SWAP": 0.1, "ZRO-USDT-SWAP": 1.0,
            "MEME-USDT-SWAP": 100.0, "ARKM-USDT-SWAP": 1.0, "ESP-USDT-SWAP": 100.0,
            "ONDO-USDT-SWAP": 10.0, "JELLYJELLY-USDT-SWAP": 100.0, "AXS-USDT-SWAP": 0.1,
            "PNUT-USDT-SWAP": 10.0, "DYDX-USDT-SWAP": 1.0, "GRT-USDT-SWAP": 10.0,
            "ATOM-USDT-SWAP": 1.0, "GRASS-USDT-SWAP": 1.0, "ALGO-USDT-SWAP": 10.0,
            "PEOPLE-USDT-SWAP": 100.0, "H-USDT-SWAP": 100.0, "BABY-USDT-SWAP": 10.0,
            "POL-USDT-SWAP": 10.0, "SPACE-USDT-SWAP": 100.0, "CRCL-USDT-SWAP": 1.0,
            "ZAMA-USDT-SWAP": 10.0,
            # 保留旧品种（不在 Top100 但可能有历史持仓）
            "HMSTR-USDT-SWAP": 100.0, "W-USDT-SWAP": 1.0, "STRK-USDT-SWAP": 1.0,
            "TON-USDT-SWAP": 1.0, "SEI-USDT-SWAP": 10.0, "MANA-USDT-SWAP": 10.0,
            "INJ-USDT-SWAP": 0.1,
        }

        # 成交额 Top 100 USDT 永续合约
        self.swap_instruments = [
            "ETH-USDT-SWAP", "BTC-USDT-SWAP", "SOL-USDT-SWAP", "TRUMP-USDT-SWAP",
            "DOGE-USDT-SWAP", "RIVER-USDT-SWAP", "XRP-USDT-SWAP", "XAU-USDT-SWAP",
            "PEPE-USDT-SWAP", "HYPE-USDT-SWAP", "TURBO-USDT-SWAP", "PI-USDT-SWAP",
            "SUI-USDT-SWAP", "ENSO-USDT-SWAP", "ZEC-USDT-SWAP", "ADA-USDT-SWAP",
            "BNB-USDT-SWAP", "RESOLV-USDT-SWAP", "TAO-USDT-SWAP", "TRIA-USDT-SWAP",
            "CL-USDT-SWAP", "PUMP-USDT-SWAP", "FIL-USDT-SWAP", "XAG-USDT-SWAP",
            "LINK-USDT-SWAP", "BCH-USDT-SWAP", "PIPPIN-USDT-SWAP", "XPL-USDT-SWAP",
            "AVAX-USDT-SWAP", "AAVE-USDT-SWAP", "WLFI-USDT-SWAP", "RENDER-USDT-SWAP",
            "WLD-USDT-SWAP", "UNI-USDT-SWAP", "AVNT-USDT-SWAP", "PENGU-USDT-SWAP",
            "NEAR-USDT-SWAP", "DOT-USDT-SWAP", "LTC-USDT-SWAP", "ROBO-USDT-SWAP",
            "WIF-USDT-SWAP", "SHIB-USDT-SWAP", "FLOW-USDT-SWAP", "MOODENG-USDT-SWAP",
            "GALA-USDT-SWAP", "HUMA-USDT-SWAP", "NIGHT-USDT-SWAP", "ICP-USDT-SWAP",
            "ASTER-USDT-SWAP", "OKB-USDT-SWAP", "CRV-USDT-SWAP", "BARD-USDT-SWAP",
            "RAVE-USDT-SWAP", "OP-USDT-SWAP", "BEAT-USDT-SWAP", "ARB-USDT-SWAP",
            "OPN-USDT-SWAP", "APT-USDT-SWAP", "ETC-USDT-SWAP", "SAHARA-USDT-SWAP",
            "LIT-USDT-SWAP", "MMT-USDT-SWAP", "IP-USDT-SWAP", "VIRTUAL-USDT-SWAP",
            "XLM-USDT-SWAP", "ENA-USDT-SWAP", "F-USDT-SWAP", "KAT-USDT-SWAP",
            "BONK-USDT-SWAP", "ORDI-USDT-SWAP", "TRX-USDT-SWAP", "HBAR-USDT-SWAP",
            "CFX-USDT-SWAP", "FARTCOIN-USDT-SWAP", "CHZ-USDT-SWAP", "BERA-USDT-SWAP",
            "DOOD-USDT-SWAP", "SATS-USDT-SWAP", "SIGN-USDT-SWAP", "AR-USDT-SWAP",
            "ZRO-USDT-SWAP", "MEME-USDT-SWAP", "ARKM-USDT-SWAP", "ESP-USDT-SWAP",
            "ONDO-USDT-SWAP", "JELLYJELLY-USDT-SWAP", "AXS-USDT-SWAP", "PNUT-USDT-SWAP",
            "DYDX-USDT-SWAP", "GRT-USDT-SWAP", "ATOM-USDT-SWAP", "GRASS-USDT-SWAP",
            "ALGO-USDT-SWAP", "PEOPLE-USDT-SWAP", "H-USDT-SWAP", "BABY-USDT-SWAP",
            "POL-USDT-SWAP", "SPACE-USDT-SWAP", "CRCL-USDT-SWAP", "ZAMA-USDT-SWAP",
        ]
        self.spot_instruments = ["BTC-USDT", "ETH-USDT"]
        self.all_instruments = self.swap_instruments + self.spot_instruments

        # 数据存储
        self._klines: dict[str, dict[str, pd.DataFrame]] = {}  # {instId: {bar: df}}
        self._tickers: dict[str, dict] = {}
        self._orderbooks: dict[str, dict] = {}
        self._funding_rates: dict[str, float] = {}

        # DeepSeek 策略顾问
        self.advisor = StrategyAdvisor()
        self._deepseek_bias: dict[str, tuple[str, float]] = {}  # {inst: (bias, confidence)}
        self._trailing_state: dict[str, dict] = {}  # 移动止盈跟踪状态
        self._deepseek_action: str = "wait"  # DeepSeek 最新判断: wait/signal/adjust
        self._deepseek_wait_count: int = 0  # DeepSeek 连续观望次数

        # 状态
        self._running = False
        self._equity = 0.0
        self._last_analysis = 0
        self._last_advisor_call = 0
        self._trade_cooldown: dict[str, float] = {}  # 防止过于频繁交易

    async def start(self):
        """启动交易引擎"""
        self._running = True
        await Database.init_pool()

        logger.info("🚀 神州99 交易引擎启动...")
        await SystemLogDAO.log("INFO", "engine", "🚀 交易引擎启动 | 模式: 实盘")
        await ModuleStatusDAO.update_status("trading_engine", "ok", "启动中...")

        # 初始化账户数据
        await self._sync_account()

        # 加载历史K线
        await self._load_history()

        # 启动 WebSocket
        await self._start_ws()

        await ModuleStatusDAO.update_status("trading_engine", "ok", "运行中")
        await ModuleStatusDAO.update_status("strategy_trend", "ok", f"运行中 · {len(self.swap_instruments)}品种")

        logger.info("🟢 交易引擎主循环启动")

        # 主循环 —— 每个 task 独立 try/except 保活
        tasks = [
            self._safe_loop("analysis", self._analysis_loop),
            self._safe_loop("sync", self._sync_loop),
            self._safe_loop("trailing", self._trailing_stop_loop),
            self._keepalive(),
        ]
        # DeepSeek 策略顾问（如果有 API Key）
        if self.advisor.has_key:
            tasks.append(self._safe_loop("advisor", self._advisor_loop))
            logger.info("🧠 DeepSeek 策略顾问已启用")
            await ModuleStatusDAO.update_status("deepseek_advisor", "ok", "已启用")
        else:
            logger.info("⚠️ DeepSeek API Key 未配置，策略顾问未启用")
            await ModuleStatusDAO.update_status("deepseek_advisor", "warn", "未配置 API Key")

        await asyncio.gather(*tasks)

    async def _safe_loop(self, name: str, coro_func):
        """包装循环任务，异常时自动重启"""
        while self._running:
            try:
                await coro_func()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{name}] 循环异常，5秒后重启: {e}")
                await SystemLogDAO.log("ERROR", "engine", f"[{name}] 异常重启: {e}")
                await asyncio.sleep(5)

    async def _keepalive(self):
        """保活 + 熔断状态同步 + 人工解除检查"""
        while self._running:
            await asyncio.sleep(30)
            logger.debug(f"💓 引擎心跳 | 净值=${self._equity:.2f} | 持仓={self.risk.state.open_positions}")

            # 同步熔断状态到 DB（供前端读取）
            try:
                from src.data.dao import ConfigDAO
                await ConfigDAO.set("circuit_breaker_paused",
                    "1" if self.risk.state.is_trading_paused else "0")
                await ConfigDAO.set("circuit_breaker_reason",
                    self.risk.state.pause_reason)
                await ConfigDAO.set("daily_pnl",
                    f"{self.risk.state.daily_pnl:.4f}")

                # 检查人工解除标记
                resume = await ConfigDAO.get("circuit_breaker_resume")
                if resume == "1":
                    msg = self.risk.manual_resume()
                    await ConfigDAO.set("circuit_breaker_resume", "0")
                    await SystemLogDAO.log("WARN", "risk", f"🔓 {msg}")
                    logger.warning(f"🔓 {msg}")
            except Exception:
                pass

    async def stop(self):
        self._running = False
        await self.ws_public.close()
        await self.ws_business.close()
        await self.ws_private.close()
        await self.client.close()
        await Database.close_pool()

    # ═══ 初始化 ═══

    async def _sync_account(self):
        """同步账户数据"""
        bal = await self.client.get_balance()
        if bal.get("code") == "0" and bal.get("data"):
            acct = bal["data"][0]
            self._equity = float(acct.get("totalEq", 0) or 0)
            self.risk.update_equity(self._equity)
            self.risk.state.daily_start_equity = self._equity

            for d in acct.get("details", []):
                ccy = d.get("ccy", "")
                if float(d.get("eq", 0) or d.get("cashBal", 0) or 0) > 0.0001:
                    await AccountDAO.upsert_asset(
                        currency=ccy,
                        balance=float(d.get("cashBal", 0) or 0),
                        frozen=float(d.get("frozenBal", 0) or 0),
                        available=float(d.get("availBal", 0) or 0),
                        usd_value=float(d.get("eqUsd", 0) or 0),
                        equity=float(d.get("eq", 0) or 0),
                    )

            await AccountDAO.save_snapshot(
                total_equity=self._equity,
                available_equity=float(acct.get("availEq", 0) or 0),
                max_equity=self._equity, drawdown=0,
            )
            logger.info(f"💰 账户净值: ${self._equity:.2f}")

        # 同步持仓
        pos = await self.client.get_positions()
        if pos.get("code") == "0":
            count = 0
            for p in pos.get("data", []):
                sz = float(p.get("pos", 0) or 0)
                if sz != 0:
                    count += 1
            self.risk.state.open_positions = count

    async def _load_history(self):
        """加载历史K线数据"""
        logger.info("📊 加载历史K线...")
        bars = ["15m", "1H", "4H"]

        for inst in self.all_instruments:
            self._klines[inst] = {}
            for bar in bars:
                resp = await self.client.get_candles(inst, bar=bar, limit="300")
                if resp.get("code") == "0" and resp.get("data"):
                    rows = []
                    for k in resp["data"]:
                        rows.append({
                            "ts": int(k[0]),
                            "open": float(k[1]),
                            "high": float(k[2]),
                            "low": float(k[3]),
                            "close": float(k[4]),
                            "vol": float(k[5]),
                        })
                    df = pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)
                    self._klines[inst][bar] = df
                    logger.debug(f"  {inst} {bar}: {len(df)} 根K线")

        await SystemLogDAO.log("INFO", "engine", f"📊 历史K线加载完成 | {len(self.all_instruments)}品种 × {len(bars)}周期")

    # ═══ WebSocket ═══

    async def _start_ws(self):
        """启动 WebSocket 连接"""
        # 公共频道: tickers + depth
        self.ws_public.on("tickers", self._on_ticker)
        self.ws_public.on("books5", self._on_depth)

        # Business 频道: K线（OKX K线必须走 business 端点）
        self.ws_business.on("candle15m", self._on_kline_15m)
        self.ws_business.on("candle1H", self._on_kline_1h)
        self.ws_business.on("candle4H", self._on_kline_4h)

        # 私有频道
        self.ws_private.on("account", self._on_account)
        self.ws_private.on("positions", self._on_positions)
        self.ws_private.on("orders", self._on_orders)

        await self.ws_public.connect()
        await self.ws_business.connect()
        await self.ws_private.connect()
        await asyncio.sleep(3)

        # 公共频道订阅
        pub_channels = []
        for inst in self.all_instruments:
            pub_channels.append({"channel": "tickers", "instId": inst})
            pub_channels.append({"channel": "books5", "instId": inst})
        await self.ws_public.subscribe(pub_channels)

        # Business 频道订阅 K线
        biz_channels = []
        for inst in self.swap_instruments:
            biz_channels.append({"channel": "candle15m", "instId": inst})
            biz_channels.append({"channel": "candle1H", "instId": inst})
            biz_channels.append({"channel": "candle4H", "instId": inst})
        await self.ws_business.subscribe(biz_channels)

        # 私有频道订阅
        await self.ws_private.subscribe([
            {"channel": "account"},
            {"channel": "positions", "instType": "ANY"},
            {"channel": "orders", "instType": "ANY"},
        ])

        total = len(pub_channels) + len(biz_channels) + 3
        await ModuleStatusDAO.update_status("okx_websocket", "ok", f"已连接 · {total}频道")
        await SystemLogDAO.log("INFO", "engine", f"📡 WebSocket 已连接 | {len(pub_channels)}公共 + {len(biz_channels)}K线 + 3私有")
        logger.info(f"📡 WebSocket 已连接 ({total} 频道)")

    async def _write_ticker_cache(self):
        """把最新行情写入共享文件，供 web server 读取"""
        try:
            import json as _json
            cache = {}
            for inst, t in self._tickers.items():
                cache[inst] = {
                    "last": float(t.get("last", 0) or 0),
                    "ts": int(time.time() * 1000),
                }
            path = os.path.join(os.path.dirname(__file__), "../../logs/ticker_cache.json")
            with open(path, "w") as f:
                _json.dump(cache, f)
        except Exception:
            pass

    async def _on_ticker(self, msg):
        for d in msg.get("data", []):
            inst = d.get("instId", "")
            self._tickers[inst] = d
            last = float(d.get("last", 0) or 0)
            if last > 0:
                # 闪崩检测
                if self.risk.check_flash_crash(inst, last):
                    await RiskLogDAO.log("flash_crash", f"{inst} 异常波动", inst_id=inst,
                                         action_taken="暂停开仓", equity=self._equity)
                # 更新持仓现价 + 用合约面值正确计算盈亏
                try:
                    ct_val = self._ct_val.get(inst, 1.0)
                    # 真实盈亏 = (现价 - 均价) × 张数 × ctVal × 方向
                    await Database.execute("""
                        UPDATE positions SET
                            current_price=%s,
                            unrealized_pnl = CASE
                                WHEN pos_side='long' THEN (%s - avg_price) * size * %s
                                WHEN pos_side='short' THEN (avg_price - %s) * size * %s
                                ELSE 0
                            END,
                            pnl_ratio = CASE
                                WHEN avg_price > 0 THEN
                                    CASE
                                        WHEN pos_side='long' THEN (%s - avg_price) / avg_price * leverage
                                        WHEN pos_side='short' THEN (avg_price - %s) / avg_price * leverage
                                        ELSE 0
                                    END
                                ELSE 0
                            END
                        WHERE inst_id=%s AND status='open'
                    """, (last, last, ct_val, last, ct_val, last, last, inst))
                except Exception as e:
                    pass
            # 每次 ticker 更新后刷新缓存文件
            await self._write_ticker_cache()

    async def _on_depth(self, msg):
        for d in msg.get("data", []):
            inst = msg.get("arg", {}).get("instId", "")
            self._orderbooks[inst] = d

    async def _on_kline_15m(self, msg):
        await self._process_kline(msg, "15m")

    async def _on_kline_1h(self, msg):
        await self._process_kline(msg, "1H")

    async def _on_kline_4h(self, msg):
        await self._process_kline(msg, "4H")

    async def _process_kline(self, msg, bar):
        """处理K线更新"""
        inst = msg.get("arg", {}).get("instId", "")
        for k in msg.get("data", []):
            new_row = {
                "ts": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                "low": float(k[3]), "close": float(k[4]), "vol": float(k[5]),
            }
            if inst not in self._klines:
                self._klines[inst] = {}
            if bar not in self._klines[inst]:
                self._klines[inst][bar] = pd.DataFrame(columns=["ts","open","high","low","close","vol"])

            df = self._klines[inst][bar]
            # 更新或追加
            if len(df) > 0 and df.iloc[-1]["ts"] == new_row["ts"]:
                for col in ["open","high","low","close","vol"]:
                    df.iloc[-1, df.columns.get_loc(col)] = new_row[col]
            else:
                self._klines[inst][bar] = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True).tail(300)

    async def _on_account(self, msg):
        for d in msg.get("data", []):
            eq = float(d.get("totalEq", 0) or 0)
            if eq > 0:
                self._equity = eq
                self.risk.update_equity(eq)
            for det in d.get("details", []):
                ccy = det.get("ccy", "")
                if ccy:
                    await AccountDAO.upsert_asset(
                        currency=ccy,
                        balance=float(det.get("cashBal", 0) or 0),
                        frozen=float(det.get("frozenBal", 0) or 0),
                        available=float(det.get("availBal", 0) or 0),
                        usd_value=float(det.get("eqUsd", 0) or det.get("cashBal", 0) or 0),
                        equity=float(det.get("eq", 0) or det.get("cashBal", 0) or 0),
                    )

    async def _on_positions(self, msg):
        count = 0
        for d in msg.get("data", []):
            inst_id = d.get("instId", "")
            pos_side = d.get("posSide", "net")
            sz = float(d.get("pos", 0) or 0)
            if sz == 0:
                # 计算真实盈亏：从 OKX 拿 realizedPnl，同时算手续费
                pnl = float(d.get("realizedPnl", 0) or 0)

                # 从 OKX 获取该仓位的累计手续费（通过最近订单估算）
                # OKX 的 realizedPnl 有时不准确，补充计算
                pos_fee = 0.0
                try:
                    # 查询该品种最近已成交订单的手续费
                    orders_resp = await self.client.get_order_history(inst_id=inst_id, limit="10")
                    if orders_resp.get("code") == "0":
                        for od in orders_resp.get("data", []):
                            fee = float(od.get("fee", 0) or 0)
                            fill_pnl = float(od.get("pnl", 0) or 0)
                            pos_fee += fee
                            if fill_pnl != 0 and pnl == 0:
                                pnl += fill_pnl  # 补充 PnL
                except Exception:
                    pass

                net = pnl + pos_fee  # 净利 = 毛利 + 手续费（手续费为负数）
                await PositionDAO.close_position(inst_id, pos_side, net)
                self.risk.record_trade_result(pnl, pos_fee)
                await SystemLogDAO.log("INFO", "engine",
                    f"📤 平仓 {inst_id} {pos_side} | 毛利=${pnl:.2f} 手续费=${pos_fee:.2f} 净利=${net:.2f}")
            else:
                count += 1
                # 用 ticker 里的最新价格，positions WS 的 last 可能是旧值
                ticker_price = 0.0
                if inst_id in self._tickers:
                    ticker_price = float(self._tickers[inst_id].get("last", 0) or 0)
                ws_price = float(d.get("last", 0) or 0)
                best_price = ticker_price if ticker_price > 0 else ws_price

                avg_px = float(d.get("avgPx", 0) or 0)
                ct_val = self._ct_val.get(inst_id, 1.0)
                # 用最新价重算盈亏（比 OKX 推的 upl 更准）
                if best_price > 0 and avg_px > 0:
                    if pos_side == "long":
                        calc_pnl = (best_price - avg_px) * abs(sz) * ct_val
                    elif pos_side == "short":
                        calc_pnl = (avg_px - best_price) * abs(sz) * ct_val
                    else:
                        calc_pnl = float(d.get("upl", 0) or 0)
                else:
                    calc_pnl = float(d.get("upl", 0) or 0)

                await PositionDAO.upsert_position({
                    "inst_id": inst_id, "inst_type": d.get("instType", ""),
                    "pos_side": pos_side, "size": abs(sz),
                    "avg_price": avg_px,
                    "current_price": best_price,
                    "mark_price": float(d.get("markPx", 0) or 0),
                    "liquidation_price": float(d.get("liqPx", 0) or 0) if d.get("liqPx") else None,
                    "margin": float(d.get("margin", 0) or 0),
                    "margin_mode": d.get("mgnMode", "isolated"),
                    "leverage": int(float(d.get("lever", 1) or 1)),
                    "unrealized_pnl": calc_pnl,
                    "realized_pnl": float(d.get("realizedPnl", 0) or 0),
                    "pnl_ratio": float(d.get("uplRatio", 0) or 0),
                    "strategy_name": "trend_multi_tf",
                    "status": "open",
                })
        self.risk.state.open_positions = count

    async def _on_orders(self, msg):
        for d in msg.get("data", []):
            ord_id = d.get("ordId", "")
            status_map = {"live": "pending", "partially_filled": "partially_filled",
                          "filled": "filled", "canceled": "canceled"}
            status = status_map.get(d.get("state", ""), d.get("state", ""))

            order_data = {
                "ord_id": ord_id, "cl_ord_id": d.get("clOrdId") or None,
                "inst_id": d.get("instId"), "inst_type": d.get("instType"),
                "ord_type": d.get("ordType"), "side": d.get("side"),
                "pos_side": d.get("posSide") or None,
                "size": float(d.get("sz", 0) or 0),
                "price": float(d.get("px", 0) or 0) if d.get("px") else None,
                "filled_size": float(d.get("accFillSz", 0) or 0),
                "filled_price": float(d.get("avgPx", 0) or 0) if d.get("avgPx") else None,
                "fee": float(d.get("fee", 0) or 0),
                "pnl": float(d.get("pnl", 0) or 0) if d.get("pnl") else None,
                "status": status,
            }
            existing = await Database.fetch_one("SELECT id FROM orders WHERE ord_id=%s", (ord_id,))
            if existing:
                await OrderDAO.update_order_by_ord_id(ord_id, order_data)
            else:
                await OrderDAO.create_order(order_data)

            if status == "filled":
                emoji = "📈" if d.get("side") == "buy" else "📉"
                await SystemLogDAO.log("INFO", "engine",
                    f"{emoji} 成交 {d.get('instId')} {d.get('side')} {d.get('posSide','')} "
                    f"sz={d.get('accFillSz')} @ {d.get('avgPx')} | fee={d.get('fee')}")
                await TradeDAO.create_trade({
                    "trade_id": d.get("tradeId"), "ord_id": ord_id,
                    "inst_id": d.get("instId"), "inst_type": d.get("instType"),
                    "side": d.get("side"), "pos_side": d.get("posSide") or None,
                    "price": float(d.get("avgPx", 0) or 0),
                    "size": float(d.get("accFillSz", 0) or 0),
                    "fee": float(d.get("fee", 0) or 0),
                    "fee_currency": d.get("feeCcy"),
                    "pnl": float(d.get("pnl", 0) or 0) if d.get("pnl") else None,
                })

    # ═══ 策略分析（核心） ═══

    async def _analysis_loop(self):
        """每 5 分钟分析一次（趋势交易不需要盯盘）"""
        await asyncio.sleep(15)
        while self._running:
            try:
                if self.risk.state.open_positions >= self.risk.config["max_positions"]:
                    await asyncio.sleep(300)
                    continue
                if self.risk.state.is_trading_paused:
                    await asyncio.sleep(300)
                    continue

                for inst in self.swap_instruments:
                    signal = await self._analyze(inst)
                    if signal:
                        await self._execute_signal(signal)
            except Exception as e:
                logger.error(f"分析循环异常: {e}")
                await SystemLogDAO.log("ERROR", "engine", f"分析异常: {e}")
            await asyncio.sleep(300)  # 5分钟一次

    async def _analyze(self, inst_id: str) -> Optional[Signal]:
        """
        纯趋势交易策略

        核心哲学：不预测，只跟随。趋势是朋友，震荡就等待。

        三个问题决定是否交易：
        1. 有没有趋势？（ADX > 25 = 有趋势）
        2. 趋势方向是什么？（4H EMA 排列 + 价格位置）
        3. 有没有好的入场点？（回调到 EMA 附近）

        任何一个不明确 → 不交易。宁可错过，不可做错。
        """
        # 冷却：同品种 2 小时内不重复交易
        cooldown = self._trade_cooldown.get(inst_id, 0)
        if time.time() - cooldown < 7200:
            return None

        if inst_id not in self._klines:
            return None
        klines = self._klines[inst_id]
        for bar in ["1H", "4H"]:
            if bar not in klines or len(klines[bar]) < 60:
                return None

        df_4h = klines["4H"]
        df_1h = klines["1H"]
        close_4h = df_4h["close"]
        close_1h = df_1h["close"]
        current_price = float(close_1h.iloc[-1])

        atr_4h = self._calc_atr(df_4h, 14)
        atr_1h = self._calc_atr(df_1h, 14)
        if atr_4h <= 0 or atr_1h <= 0:
            return None

        # ═══ 第一关：ADX — 有没有趋势？ ═══
        adx = self._calc_adx(df_4h, 14)
        # ETH 波动大、假突破多，需要更强的趋势确认
        adx_min = 30 if "ETH" in inst_id else 26
        if adx < adx_min:
            logger.info(f"⏸️ {inst_id} | ADX={adx:.0f}<{adx_min} 无趋势，等待")
            return None

        # ═══ 第二关：趋势方向 ═══
        ema20_4h = float(close_4h.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50_4h = float(close_4h.ewm(span=53, adjust=False).mean().iloc[-1])
        ema20_1h = float(close_1h.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50_1h = float(close_1h.ewm(span=53, adjust=False).mean().iloc[-1])

        # 4H 趋势（必须明确）
        trend_4h = "neutral"
        if ema20_4h > ema50_4h and current_price > ema20_4h:
            trend_4h = "long"
        elif ema20_4h < ema50_4h and current_price < ema20_4h:
            trend_4h = "short"

        # 1H 确认
        trend_1h = "neutral"
        if ema20_1h > ema50_1h:
            trend_1h = "long"
        elif ema20_1h < ema50_1h:
            trend_1h = "short"

        if trend_4h == "neutral":
            logger.info(f"⏸️ {inst_id} | ADX={adx:.0f} 但4H趋势不明确，等待")
            return None

        # 4H 和 1H 必须同向
        if trend_1h != trend_4h and trend_1h != "neutral":
            logger.info(f"⏸️ {inst_id} | 4H={trend_4h} vs 1H={trend_1h} 矛盾，等待")
            return None

        # ═══ 第三关：入场点 ═══
        distance_to_ema = abs(current_price - ema20_1h) / atr_1h

        rsi_1h = self._calc_rsi(close_1h, 14)
        if np.isnan(rsi_1h):
            return None

        # 价差检查
        if inst_id in self._orderbooks:
            ob = self._orderbooks[inst_id]
            asks = ob.get("asks", [])
            bids = ob.get("bids", [])
            if asks and bids:
                if self.risk.check_spread(float(asks[0][0]), float(bids[0][0])):
                    return None

        # ═══ DeepSeek 否决权 ═══
        if self._deepseek_wait_count >= 3:
            logger.info(f"🧠 {inst_id} | DeepSeek连续{self._deepseek_wait_count}次观望→不交易")
            return None

        # ═══ 趋势信心 ═══
        adx_score = min((adx - 25) / 25, 1.0) * 0.35       # ADX 强度
        align_score = 0.35 if trend_1h == trend_4h else 0.15  # 多级别一致
        entry_score = max(0, 1 - distance_to_ema) * 0.30     # 回调位置

        confidence = adx_score + align_score + entry_score

        logger.info(
            f"🔍 {inst_id} | 趋势={trend_4h} ADX={adx:.0f} | "
            f"4H: EMA20={ema20_4h:.0f}/50={ema50_4h:.0f} | "
            f"1H: {trend_1h} 距EMA={distance_to_ema:.1f}ATR | "
            f"RSI={rsi_1h:.0f} | 信心={confidence:.0%}"
        )

        min_conf = 0.65 if "ETH" in inst_id else 0.60
        if confidence < min_conf:
            return None

        # 不追涨杀跌：离 EMA 太远不入场（收紧到 1.2ATR）
        if distance_to_ema > 1.2:
            logger.info(f"⏸️ {inst_id} | 离EMA太远({distance_to_ema:.1f}ATR)，不追")
            return None

        # ═══ BTC 额外过滤（BTC胜率29%，最大亏损源） ═══
        if "BTC" in inst_id:
            if adx < 30:  # BTC 要更强趋势
                logger.info(f"⏸️ BTC | ADX={adx:.0f}<30 趋势不够强，跳过")
                return None
            if confidence < 0.68:  # BTC 要更高信心
                logger.info(f"⏸️ BTC | 信心{confidence:.0%}<68%，跳过")
                return None

        # ═══ 手续费预估检查：确保预期盈利能覆盖手续费 ═══
        # taker 费率约 0.05%，开+平 = 0.1%，杠杆放大后的名义价值
        est_fee_pct = 0.001  # 0.1% 双向手续费
        # 止损距离必须 > 手续费+滑点损耗的 3 倍（否则盈亏比太差）

        if trend_4h == "long":
            if current_price < ema50_1h:
                return None
            if rsi_1h > 70:  # 从75收紧到70
                return None

            # 止损：固定 1.5 ATR（不再用 EMA50 兜底，避免 SL 太远导致 RR 不足）
            sl = current_price - atr_4h * 1.5
            tp = current_price + atr_4h * 2.35

            # 止盈距离必须 > 0.3%（覆盖手续费+滑点）
            tp_pct = abs(tp - current_price) / current_price
            if tp_pct < 0.003:
                logger.info(f"⏸️ {inst_id} | TP距离{tp_pct:.2%}太小，手续费吃掉利润")
                return None

            rr = abs(tp - current_price) / abs(current_price - sl)
            if rr < 1.5:
                logger.info(f"⏸️ {inst_id} | RR={rr:.1f}<1.5 盈亏比不足")
                return None

            return Signal(
                strategy_name="trend_long",
                inst_id=inst_id, inst_type=InstrumentType.SWAP,
                action=SignalAction.OPEN_LONG, price=current_price,
                stop_loss=sl, take_profit=tp,
                reason=f"趋势做多 | ADX={adx:.0f} 4H+1H多头 | 距EMA={distance_to_ema:.1f} RSI={rsi_1h:.0f}",
                confidence=confidence,
            )

        elif trend_4h == "short":
            if current_price > ema50_1h:
                return None
            if rsi_1h < 30:  # 从25收紧到30
                return None

            sl = current_price + atr_4h * 1.5
            tp = current_price - atr_4h * 2.35

            tp_pct = abs(current_price - tp) / current_price
            if tp_pct < 0.003:
                logger.info(f"⏸️ {inst_id} | TP距离{tp_pct:.2%}太小，手续费吃掉利润")
                return None

            rr = abs(current_price - tp) / abs(sl - current_price)
            if rr < 1.5:
                logger.info(f"⏸️ {inst_id} | RR={rr:.1f}<1.5 盈亏比不足")
                return None

            return Signal(
                strategy_name="trend_short",
                inst_id=inst_id, inst_type=InstrumentType.SWAP,
                action=SignalAction.OPEN_SHORT, price=current_price,
                stop_loss=sl, take_profit=tp,
                reason=f"趋势做空 | ADX={adx:.0f} 4H+1H空头 | 距EMA={distance_to_ema:.1f} RSI={rsi_1h:.0f}",
                confidence=confidence,
            )

        return None

    def _assess_trend(self, df: pd.DataFrame) -> tuple[str, float]:
        """评估趋势方向和强度"""
        close = df["close"]
        if len(close) < 50:
            return "neutral", 0.0

        ema20 = close.ewm(span=20, adjust=False).mean()
        ema50 = close.ewm(span=53, adjust=False).mean()

        # MACD
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        hist = macd - signal

        price = close.iloc[-1]
        score = 0.0

        # EMA 排列 (40%)
        if ema20.iloc[-1] > ema50.iloc[-1]:
            score += 0.4
        else:
            score -= 0.4

        # MACD (30%)
        if macd.iloc[-1] > signal.iloc[-1]:
            score += 0.15
        else:
            score -= 0.15
        if len(hist) >= 2 and hist.iloc[-1] > hist.iloc[-2]:
            score += 0.15
        elif len(hist) >= 2:
            score -= 0.15

        # 价格 vs EMA20 (20%)
        if price > ema20.iloc[-1]:
            score += 0.2
        else:
            score -= 0.2

        # 动量 (10%)
        if len(close) >= 5:
            momentum = (price - close.iloc[-5]) / close.iloc[-5]
            if momentum > 0.01:
                score += 0.1
            elif momentum < -0.01:
                score -= 0.1

        if score > 0:
            return "long", min(abs(score), 1.0)
        elif score < 0:
            return "short", min(abs(score), 1.0)
        return "neutral", 0.0

    def _calc_dynamic_leverage(self, signal: Signal) -> int:
        """
        动态杠杆 2x-8x

        公式：基础(信心) ± 波动率调整 ± 盈亏比加成 ± 连亏惩罚
        """
        conf = signal.confidence
        rr = signal.reward_risk_ratio

        # ATR%
        atr_pct = 0.005
        if signal.inst_id in self._klines and "1H" in self._klines[signal.inst_id]:
            df = self._klines[signal.inst_id]["1H"]
            atr = self._calc_atr(df, 14)
            if atr > 0 and signal.price > 0:
                atr_pct = atr / signal.price

        # 基础杠杆 = 信心映射
        # 降杠杆! 数据证明: ≥5x亏损占93%(-$85), <5x只亏$6
        if conf >= 0.85:
            base = 4
        elif conf >= 0.75:
            base = 3
        elif conf >= 0.65:
            base = 3
        else:
            base = 2

        # 波动率：高波动降杠杆，低波动加杠杆
        if atr_pct > 0.012:
            base -= 2
        elif atr_pct > 0.008:
            base -= 1
        elif atr_pct < 0.003:
            base += 1

        # 盈亏比：RR > 2.5 奖励 +1
        if rr >= 2.5:
            base += 1

        # 连亏惩罚：连亏2次 -1，连亏3次 -2
        losses = self.risk.state.consecutive_losses
        if losses >= 3:
            base -= 2
        elif losses >= 2:
            base -= 1

        leverage = max(2, min(base, 4))  # 上限从6降到4（数据驱动：5x+亏损占93%）

        logger.info(
            f"⚙️ 杠杆={leverage}x | 信心{conf:.0%} ATR{atr_pct:.3%} RR={rr:.1f} 连亏{losses} "
            f"| {signal.inst_id}"
        )
        return leverage

    def _calc_adx(self, df: pd.DataFrame, period: int = 14) -> float:
        """
        ADX（平均趋向指标）— 衡量趋势强度，不管方向

        ADX < 20: 无趋势（震荡）
        ADX 25-50: 有趋势
        ADX > 50: 强趋势
        """
        if len(df) < period * 2:
            return 0.0
        try:
            high = df["high"].astype(float)
            low = df["low"].astype(float)
            close = df["close"].astype(float)

            # True Range
            tr = pd.DataFrame()
            tr["hl"] = high - low
            tr["hc"] = abs(high - close.shift(1))
            tr["lc"] = abs(low - close.shift(1))
            tr["tr"] = tr[["hl", "hc", "lc"]].max(axis=1)

            # +DM / -DM
            up_move = high - high.shift(1)
            down_move = low.shift(1) - low
            plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0), index=df.index)
            minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0), index=df.index)

            # Smoothed averages
            atr = tr["tr"].ewm(span=period, adjust=False).mean()
            plus_di = 100 * (plus_dm.ewm(span=period, adjust=False).mean() / atr)
            minus_di = 100 * (minus_dm.ewm(span=period, adjust=False).mean() / atr)

            # DX → ADX
            dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
            adx = dx.ewm(span=period, adjust=False).mean()

            val = float(adx.iloc[-1])
            return val if not np.isnan(val) else 0.0
        except Exception:
            return 0.0

    def _calc_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        if len(df) < period + 1:
            return 0.0
        h, l, c = df["high"], df["low"], df["close"]
        tr = pd.concat([h - l, abs(h - c.shift(1)), abs(l - c.shift(1))], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])

    def _calc_rsi(self, close: pd.Series, period: int = 14) -> float:
        if len(close) < period + 1:
            return 50.0
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        val = rsi.iloc[-1]
        return float(val) if not np.isnan(val) else 50.0

    # ═══ 执行交易 ═══

    async def _execute_signal(self, signal: Signal):
        """风控审核 → 仓位计算 → 下单"""
        # 0. 同品种同方向不重复开仓
        if signal.action in (SignalAction.OPEN_LONG, SignalAction.OPEN_SHORT):
            pos_side_check = "long" if signal.action == SignalAction.OPEN_LONG else "short"
            try:
                existing = await PositionDAO.get_open_positions()
                for ep in existing:
                    if ep["inst_id"] == signal.inst_id and ep["pos_side"] == pos_side_check:
                        logger.debug(f"跳过: {signal.inst_id} {pos_side_check} 已有持仓")
                        return
            except Exception:
                pass

        # 1. 风控审核
        passed, reason = self.risk.check_signal(signal)

        await SignalDAO.create_signal({
            "strategy_name": signal.strategy_name,
            "inst_id": signal.inst_id,
            "inst_type": signal.inst_type.value,
            "action": signal.action.value,
            "price": signal.price,
            "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit,
            "reward_risk_ratio": signal.reward_risk_ratio,
            "confidence": signal.confidence,
            "reason": signal.reason,
            "risk_check_passed": 1 if passed else 0,
            "risk_check_reason": reason,
            "executed": 0,
        })

        if not passed:
            await SystemLogDAO.log("WARN", "risk", f"🚫 信号被拒: {signal.inst_id} | {reason}")
            await RiskLogDAO.log("signal_check", f"拒绝: {reason}",
                                  inst_id=signal.inst_id, strategy_name=signal.strategy_name,
                                  action_taken="rejected", equity=self._equity)
            logger.info(f"🚫 风控拒绝: {signal.inst_id} {signal.action.value} | {reason}")
            return

        # 2. 仓位计算
        multiplier = self.risk.get_position_multiplier()
        position_size = self.sizer.calculate(
            equity=self._equity,
            entry_price=signal.price,
            stop_loss=signal.stop_loss,
            risk_multiplier=multiplier,
            win_rate=self.risk.win_rate,
        )

        if position_size <= 0:
            logger.info(f"仓位计算为 0，不开仓")
            return

        # ── 动态杠杆 ──
        ct_val = self._ct_val.get(signal.inst_id, 1.0)
        leverage = self._calc_dynamic_leverage(signal)

        # 设置杠杆（OKX 需先撤旧委托才能改杠杆）
        pos_side_for_lever = "long" if signal.action == SignalAction.OPEN_LONG else "short"
        try:
            resp = await self.client.set_leverage(
                inst_id=signal.inst_id,
                lever=str(leverage),
                mgn_mode="isolated",
                pos_side=pos_side_for_lever,
            )
            if resp.get("code") != "0":
                # 杠杆设置失败（可能有旧委托），用当前杠杆继续
                logger.debug(f"杠杆设置跳过（有委托在）: {resp.get('msg','')[:50]}")
        except Exception as e:
            logger.debug(f"杠杆设置跳过: {e}")

        # ── 动态仓位计算 ──
        # 保证金比例跟信心挂钩：趋势确定就上仓位
        # 保证金比例（降仓！数据显示大仓位亏损严重）
        # 100品种分散 → 每笔小仓位 → 靠胜率赚钱不靠单笔大赌
        conf = signal.confidence
        if conf >= 0.85:
            margin_pct = 0.15    # 强趋势 → 15%（旧30%太激进）
        elif conf >= 0.75:
            margin_pct = 0.12    # 明确趋势 → 12%
        elif conf >= 0.65:
            margin_pct = 0.10    # 中等趋势 → 10%
        elif conf >= 0.60:
            margin_pct = 0.08    # 刚过门槛 → 8%
        else:
            margin_pct = 0.05

        # 连亏减仓
        if self.risk.state.consecutive_losses >= 3:
            margin_pct *= 0.5
        elif self.risk.state.consecutive_losses >= 2:
            margin_pct *= 0.7

        # 检查已用保证金，总量不超 70%（15品种分散了风险）
        total_margin_used = 0
        try:
            positions = await PositionDAO.get_open_positions()
            for p in positions:
                total_margin_used += float(p.get("margin", 0) or 0)
        except Exception:
            pass

        max_total_margin = self._equity * 0.70
        remaining = max(max_total_margin - total_margin_used, 0)
        available_margin = min(self._equity * margin_pct, remaining)

        if available_margin < 10:
            logger.info(f"保证金不足: 可用${remaining:.0f}")
            return

        logger.info(f"📊 仓位决策: 信心{conf:.0%}→保证金{margin_pct*100:.0f}%=${available_margin:.0f} 杠杆{leverage}x")

        target_notional = available_margin * leverage
        # 每张名义价值 = ctVal × 当前价格
        value_per_lot = ct_val * signal.price
        # 张数
        lots = target_notional / value_per_lot

        # 取整到 lotSz
        lot_sz = {
            "BTC-USDT-SWAP": 0.01, "ETH-USDT-SWAP": 0.01,
            "SOL-USDT-SWAP": 0.01, "DOGE-USDT-SWAP": 0.01,
            "ATOM-USDT-SWAP": 1, "HMSTR-USDT-SWAP": 1,
            "BCH-USDT-SWAP": 0.1, "W-USDT-SWAP": 1,
            "STRK-USDT-SWAP": 1, "TON-USDT-SWAP": 1,
            "SEI-USDT-SWAP": 1, "MANA-USDT-SWAP": 1,
            "ADA-USDT-SWAP": 0.1, "OP-USDT-SWAP": 1,
            "INJ-USDT-SWAP": 1,
        }.get(signal.inst_id, 0.01)
        if lot_sz >= 1:
            lots = max(int(lots), int(lot_sz))
        else:
            lots = max(round(lots / lot_sz) * lot_sz, lot_sz)
        sz_str = f"{lots:g}"

        # 计算实际保证金占用
        actual_margin = lots * value_per_lot / leverage
        logger.info(f"📐 仓位: {lots}张 | 名义=${lots * value_per_lot:.0f} | 保证金=${actual_margin:.0f} | 占比={actual_margin/self._equity*100:.0f}%")

        # 3. 下单
        side = "buy" if signal.action in (SignalAction.OPEN_LONG, SignalAction.BUY_SPOT) else "sell"
        pos_side = "long" if signal.action == SignalAction.OPEN_LONG else "short"

        logger.info(f"📤 下单: {signal.inst_id} {side} {pos_side} sz={sz_str} @ market")
        await SystemLogDAO.log("INFO", "engine",
            f"📤 下单: {signal.inst_id} {side} {pos_side} sz={sz_str} | {signal.reason}")

        try:
            # 市价下单
            resp = await self.client.place_order(
                inst_id=signal.inst_id,
                td_mode="isolated",
                side=side,
                ord_type="market",
                sz=sz_str,
                pos_side=pos_side,
            )

            if resp.get("code") == "0" and resp.get("data"):
                ord_id = resp["data"][0].get("ordId", "")
                logger.info(f"✅ 下单成功: {ord_id}")

                await OrderDAO.create_order({
                    "ord_id": ord_id, "inst_id": signal.inst_id,
                    "inst_type": signal.inst_type.value,
                    "ord_type": "market", "side": side, "pos_side": pos_side,
                    "size": float(sz_str), "strategy_name": signal.strategy_name,
                    "signal_reason": signal.reason, "status": "pending",
                })

                # 设置止损止盈
                await self._set_sl_tp(signal, sz_str, pos_side)

                # 冷却
                self._trade_cooldown[signal.inst_id] = time.time()

                await SystemLogDAO.log("INFO", "engine",
                    f"✅ 下单成功 {signal.inst_id} {pos_side} | ordId={ord_id}")
                await RiskLogDAO.log("signal_check", f"执行: {signal.reason}",
                                      inst_id=signal.inst_id, strategy_name=signal.strategy_name,
                                      action_taken="executed", equity=self._equity)
            else:
                err = resp.get("data", [{}])[0].get("sMsg", resp.get("msg", "unknown"))
                err_code = resp.get("data", [{}])[0].get("sCode", "")
                logger.error(f"❌ 下单失败: {err}")
                await SystemLogDAO.log("ERROR", "engine", f"❌ 下单失败: {signal.inst_id} | {err}")

                # 余额不足 → 冷却 10 分钟，避免反复下单
                if err_code in ("51008", "51004"):
                    logger.warning(f"💸 余额不足，暂停开仓 10 分钟")
                    for inst in self.swap_instruments:
                        self._trade_cooldown[inst] = time.time() + 300  # 额外 5 分钟冷却
                    await SystemLogDAO.log("WARN", "risk", f"💸 余额不足，暂停开新仓 10 分钟")

        except Exception as e:
            logger.error(f"下单异常: {e}")
            await SystemLogDAO.log("ERROR", "engine", f"下单异常: {e}")

    async def _set_sl_tp(self, signal: Signal, sz: str, pos_side: str):
        """
        智能止盈止损设置

        止损：全仓挂单（铁律不变）
        止盈：不挂固定止盈单！靠移动止盈系统动态管理
              只在极端位置挂一个"安全网"止盈（5×ATR）防止极端行情回撤
        """
        close_side = "sell" if pos_side == "long" else "buy"
        inst_id = signal.inst_id

        try:
            # ── 止损（全仓，铁律） ──
            resp = await self.client.place_algo_order(
                inst_id=inst_id,
                td_mode="isolated",
                side=close_side,
                ord_type="conditional",
                sz=sz,
                sl_trigger_px=str(signal.stop_loss),
                sl_ord_px="-1",
                pos_side=pos_side,
            )
            if resp.get("code") == "0":
                logger.info(f"🛡️ 止损设置: {signal.stop_loss:.2f}")
            else:
                logger.warning(f"止损设置失败: {resp}")

            # ── 安全网止盈（5×ATR，远距离保护） ──
            # 不是主要止盈方式，只是防极端行情回撤的安全网
            atr = 0
            if inst_id in self._klines and "15m" in self._klines[inst_id]:
                atr = self._calc_atr(self._klines[inst_id]["15m"], 14)

            if atr > 0:
                if pos_side == "long":
                    safety_tp = signal.price + atr * 5.0
                else:
                    safety_tp = signal.price - atr * 5.0

                resp = await self.client.place_algo_order(
                    inst_id=inst_id,
                    td_mode="isolated",
                    side=close_side,
                    ord_type="conditional",
                    sz=sz,
                    tp_trigger_px=str(round(safety_tp, 2)),
                    tp_ord_px="-1",
                    pos_side=pos_side,
                )
                if resp.get("code") == "0":
                    logger.info(f"🎯 安全网止盈: {safety_tp:.2f} (5×ATR)")

            # 记录到跟踪表（移动止盈系统用）
            self._trailing_state[f"{inst_id}_{pos_side}"] = {
                "entry_price": signal.price,
                "highest_profit_atr": 0.0,  # 最大浮盈（ATR 倍数）
                "current_sl": signal.stop_loss,
                "phase": "initial",  # initial → breakeven → trailing → accelerating
                "partial_closed": False,  # 是否已部分止盈
                "total_sz": float(sz),
                "entry_time": time.time(),
            }

            # 写入 positions 表（前端显示用）
            await Database.execute(
                "UPDATE positions SET stop_loss=%s, take_profit=%s "
                "WHERE inst_id=%s AND pos_side=%s AND status='open'",
                (signal.stop_loss, signal.take_profit, inst_id, pos_side)
            )

            await SystemLogDAO.log("INFO", "engine",
                f"🛡️ SL={signal.stop_loss:.2f} TP={signal.take_profit:.2f} 安全网={safety_tp:.2f if atr > 0 else 0:.2f} | {inst_id}")

        except Exception as e:
            logger.error(f"止损止盈设置异常: {e}")

    # ═══ 智能移动止盈系统 ═══

    async def _trailing_stop_loop(self):
        """
        智能止盈引擎（每 30 秒检查一次）

        五阶段止盈策略（v2 — 解决"盈利不止盈反亏"问题）：
        Phase 0 - watching:     浮盈 < 0.5ATR → 记录最高盈利，准备保护
        Phase 1 - protecting:   浮盈 ≥ 0.5ATR → 止损移到 -0.3ATR（微亏保护）
        Phase 2 - breakeven:    浮盈 ≥ 1ATR → 止损移到成本+0.1ATR（保本）
        Phase 3 - trailing:     浮盈 ≥ 1.5ATR → 分批平仓 50% + 阶梯移动止损
        Phase 4 - accelerating: 浮盈 ≥ 2.5ATR → 紧跟价格，止损=最高盈利-0.5ATR

        额外规则：
        - 浮盈回撤保护: 峰值 > 0.8ATR 且回撤 > 60% → 立即平仓
        - 时间止盈: 持仓 > 8h 且浮盈 < 0.3ATR → 平仓
        - 趋势逆转: 1H 趋势反向 + 浮盈回撤 > 40% → 平仓
        """
        while self._running:
            await asyncio.sleep(30)
            try:
                positions = await PositionDAO.get_open_positions()
                for p in positions:
                    await self._manage_position_tp(p)
            except Exception as e:
                logger.error(f"移动止盈异常: {e}")

    async def _manage_position_tp(self, p: dict):
        """
        管理单个持仓的止盈（v2 — 更积极锁利）

        核心改进：
        1. 浮盈 0.5ATR 就开始保护（旧版要 1ATR）
        2. 峰值回撤 > 60% 直接平仓（旧版 50%，且要 1ATR 峰值）
        3. 分批止盈从 2ATR 降到 1.5ATR
        4. 加速跟踪从 3ATR 降到 2.5ATR，距离从 0.75 缩到 0.5
        """
        inst_id = p["inst_id"]
        pos_side = p.get("pos_side", "")
        state_key = f"{inst_id}_{pos_side}"

        if inst_id not in self._klines or "15m" not in self._klines[inst_id]:
            return

        atr = self._calc_atr(self._klines[inst_id]["15m"], 14)
        if atr <= 0:
            return

        avg_px = float(p.get("avg_price", 0))
        cur_px = float(p.get("current_price", 0) or self._tickers.get(inst_id, {}).get("last", 0))
        cur_px = float(cur_px)
        size = float(p.get("size", 0))

        if avg_px <= 0 or cur_px <= 0 or size <= 0:
            return

        # 计算浮盈（ATR 倍数）
        if pos_side == "long":
            profit_atr = (cur_px - avg_px) / atr
        else:
            profit_atr = (avg_px - cur_px) / atr

        # 获取/创建跟踪状态
        state = self._trailing_state.get(state_key)
        if not state:
            state = {
                "entry_price": avg_px,
                "highest_profit_atr": max(profit_atr, 0),
                "current_sl": avg_px - atr * 2 if pos_side == "long" else avg_px + atr * 2,
                "phase": "watching",
                "partial_closed": False,
                "total_sz": size,
                "entry_time": time.time() - 3600,
            }
            self._trailing_state[state_key] = state

        # 更新最高浮盈
        if profit_atr > state["highest_profit_atr"]:
            state["highest_profit_atr"] = profit_atr

        old_phase = state["phase"]
        peak = state["highest_profit_atr"]
        close_side = "sell" if pos_side == "long" else "buy"
        hold_hours = (time.time() - state["entry_time"]) / 3600

        # ═══ 紧急规则（优先级最高，任何阶段都检查） ═══

        # ── 浮盈回撤保护：峰值曾到 0.8ATR 以上，回撤超过 60% → 立即平仓锁利 ──
        if peak >= 0.8 and profit_atr < peak * 0.4:
            logger.info(
                f"⚡ 浮盈回撤保护: {inst_id} {pos_side} | "
                f"峰值{peak:.2f}ATR→当前{profit_atr:.2f}ATR 回撤{(1-profit_atr/peak)*100:.0f}% → 平仓")
            await self._close_position_market(inst_id, pos_side, size,
                f"浮盈回撤保护 峰值{peak:.1f}→{profit_atr:.1f}ATR")
            self._trailing_state.pop(state_key, None)
            return

        # ── 盈利变亏损保护：曾经盈利 0.5ATR 以上，现在变亏了 → 立即平仓 ──
        if peak >= 0.5 and profit_atr <= 0:
            logger.info(
                f"🚨 盈转亏保护: {inst_id} {pos_side} | "
                f"峰值{peak:.2f}ATR→当前{profit_atr:.2f}ATR → 平仓止损")
            await self._close_position_market(inst_id, pos_side, size,
                f"盈转亏保护 峰值{peak:.1f}ATR")
            self._trailing_state.pop(state_key, None)
            return

        # ── 时间止盈：持仓超过 8 小时且浮盈很小 → 释放资金 ──
        if hold_hours > 8 and -0.3 < profit_atr < 0.3:
            logger.info(f"⏰ 时间止盈: {inst_id} {pos_side} | 持仓{hold_hours:.1f}h 浮盈{profit_atr:.1f}ATR → 平仓")
            await self._close_position_market(inst_id, pos_side, size, f"时间止盈 {hold_hours:.0f}h")
            self._trailing_state.pop(state_key, None)
            return

        # ── 趋势逆转止盈：1H 趋势反向 + 浮盈回撤 > 40% ──
        if "1H" in self._klines.get(inst_id, {}):
            trend_1h, str_1h = self._assess_trend(self._klines[inst_id]["1H"])
            if peak > 0.6 and profit_atr < peak * 0.6:
                is_reversed = (pos_side == "long" and trend_1h == "short" and str_1h > 0.4) or \
                              (pos_side == "short" and trend_1h == "long" and str_1h > 0.4)
                if is_reversed:
                    logger.info(
                        f"🔄 趋势逆转止盈: {inst_id} {pos_side} | "
                        f"峰值{peak:.1f}ATR→{profit_atr:.1f}ATR 1H={trend_1h}")
                    await self._close_position_market(inst_id, pos_side, size, f"趋势逆转 1H={trend_1h}")
                    self._trailing_state.pop(state_key, None)
                    return

        # ═══ 阶段式止盈（从高到低检查） ═══

        # ── Phase 4: accelerating（浮盈 ≥ 2.5ATR 紧跟模式） ──
        if profit_atr >= 2.5:
            state["phase"] = "accelerating"
            # 紧跟：止损 = 最高盈利 - 0.5ATR（比旧版 0.75 更紧）
            if pos_side == "long":
                new_sl = avg_px + (peak - 0.5) * atr
                if new_sl > state["current_sl"]:
                    await self._update_sl(inst_id, pos_side, new_sl, size, state)
                    logger.info(f"🚀 加速跟踪: {inst_id} {pos_side} SL→{new_sl:.2f} (盈利{profit_atr:.1f}ATR)")
            else:
                new_sl = avg_px - (peak - 0.5) * atr
                if new_sl < state["current_sl"]:
                    await self._update_sl(inst_id, pos_side, new_sl, size, state)
                    logger.info(f"🚀 加速跟踪: {inst_id} {pos_side} SL→{new_sl:.2f} (盈利{profit_atr:.1f}ATR)")

        # ── Phase 3: trailing（浮盈 ≥ 1.5ATR 阶梯模式 + 分批平仓） ──
        elif profit_atr >= 1.5:
            state["phase"] = "trailing"

            # 分批止盈：平掉 50%
            if not state["partial_closed"]:
                close_sz = round(state["total_sz"] * 0.5, 2)
                if close_sz >= 0.01:
                    logger.info(f"💰 分批止盈: {inst_id} {pos_side} 平{close_sz}张 (盈利{profit_atr:.1f}ATR)")
                    await self._close_position_market(inst_id, pos_side, close_sz, f"分批止盈50% 盈利{profit_atr:.1f}ATR")
                    state["partial_closed"] = True

            # 阶梯移动止损
            if pos_side == "long":
                new_sl = avg_px + (profit_atr - 1.0) * atr * 0.82
                if new_sl > state["current_sl"]:
                    await self._update_sl(inst_id, pos_side, new_sl, size, state)
            else:
                new_sl = avg_px - (profit_atr - 1.0) * atr * 0.82
                if new_sl < state["current_sl"]:
                    await self._update_sl(inst_id, pos_side, new_sl, size, state)

        # ── Phase 2: breakeven（浮盈 ≥ 1ATR 移到成本+手续费） ──
        elif profit_atr >= 1.0:
            state["phase"] = "breakeven"
            if pos_side == "long":
                new_sl = avg_px + atr * 0.15  # 覆盖手续费
                if new_sl > state["current_sl"]:
                    await self._update_sl(inst_id, pos_side, new_sl, size, state)
                    logger.info(f"🔒 保本止损: {inst_id} {pos_side} SL→{new_sl:.2f}")
            else:
                new_sl = avg_px - atr * 0.15
                if new_sl < state["current_sl"]:
                    await self._update_sl(inst_id, pos_side, new_sl, size, state)
                    logger.info(f"🔒 保本止损: {inst_id} {pos_side} SL→{new_sl:.2f}")

        # ── Phase 1: protecting（浮盈 ≥ 0.5ATR 微亏保护） ──
        elif profit_atr >= 0.5:
            state["phase"] = "protecting"
            # 止损上移到仅亏 0.3ATR 的位置（而非原始的 1.5-2ATR 远）
            if pos_side == "long":
                new_sl = avg_px - atr * 0.3
                if new_sl > state["current_sl"]:
                    await self._update_sl(inst_id, pos_side, new_sl, size, state)
                    logger.info(f"🛡️ 微亏保护: {inst_id} {pos_side} SL→{new_sl:.2f} (浮盈{profit_atr:.1f}ATR)")
            else:
                new_sl = avg_px + atr * 0.3
                if new_sl < state["current_sl"]:
                    await self._update_sl(inst_id, pos_side, new_sl, size, state)
                    logger.info(f"🛡️ 微亏保护: {inst_id} {pos_side} SL→{new_sl:.2f} (浮盈{profit_atr:.1f}ATR)")

        # ── Phase 0: watching — 什么都不做，等待 ──

        if old_phase != state["phase"]:
            await SystemLogDAO.log("INFO", "engine",
                f"📊 止盈阶段: {inst_id} {pos_side} {old_phase}→{state['phase']} | "
                f"盈利={profit_atr:.1f}ATR 峰值={peak:.1f}ATR")

    async def _update_sl(self, inst_id: str, pos_side: str, new_sl: float, size: float, state: dict):
        """更新止损：撤旧单 + 挂新单"""
        close_side = "sell" if pos_side == "long" else "buy"
        try:
            # 撤销旧的止损委托
            algo_resp = await self.client.get_algo_orders("conditional", inst_id=inst_id)
            if algo_resp.get("code") == "0":
                old_orders = [
                    {"algoId": o["algoId"], "instId": inst_id}
                    for o in algo_resp.get("data", [])
                    if o.get("posSide") == pos_side and o.get("slTriggerPx")
                ]
                if old_orders:
                    await self.client.cancel_algo_orders(old_orders)

            # 挂新止损
            resp = await self.client.place_algo_order(
                inst_id=inst_id,
                td_mode="isolated",
                side=close_side,
                ord_type="conditional",
                sz=str(round(size, 2)),
                sl_trigger_px=str(round(new_sl, 2)),
                sl_ord_px="-1",
                pos_side=pos_side,
            )
            if resp.get("code") == "0":
                state["current_sl"] = new_sl
                # 同步到 DB（前端显示）
                await Database.execute(
                    "UPDATE positions SET stop_loss=%s WHERE inst_id=%s AND pos_side=%s AND status='open'",
                    (new_sl, inst_id, pos_side)
                )
                await SystemLogDAO.log("INFO", "engine",
                    f"🛡️ 止损更新: {inst_id} {pos_side} SL→{new_sl:.2f}")
            else:
                logger.warning(f"更新止损失败: {resp}")

        except Exception as e:
            logger.error(f"更新止损异常: {e}")

    async def _close_position_market(self, inst_id: str, pos_side: str, size: float, reason: str):
        """市价平仓（部分或全部）"""
        close_side = "sell" if pos_side == "long" else "buy"
        try:
            resp = await self.client.place_order(
                inst_id=inst_id,
                td_mode="isolated",
                side=close_side,
                ord_type="market",
                sz=str(round(size, 2)),
                pos_side=pos_side,
                reduce_only=True,
            )
            if resp.get("code") == "0":
                ord_id = resp["data"][0].get("ordId", "?")
                logger.info(f"✅ 平仓成功: {inst_id} {pos_side} sz={size} | {reason} | ordId={ord_id}")
                await SystemLogDAO.log("INFO", "engine",
                    f"💰 平仓: {inst_id} {pos_side} sz={size} | {reason}")
            else:
                logger.warning(f"平仓失败: {resp}")
        except Exception as e:
            logger.error(f"平仓异常: {e}")

    # ═══ DeepSeek 猎手引擎 ═══

    async def _advisor_loop(self):
        """每 5 分钟运行 DeepSeek 猎手，主动发现交易机会"""
        await asyncio.sleep(30)
        while self._running:
            try:
                market_data = await self._build_market_data()
                positions = await self._get_current_positions_summary()
                result = await self.advisor.hunt(market_data, positions)

                # 记录 DeepSeek 的最新判断
                if result:
                    action = result.get("action", "wait")
                    self._deepseek_action = action
                    if action == "wait":
                        self._deepseek_wait_count += 1
                    else:
                        self._deepseek_wait_count = 0

                if result and result.get("action") == "signal":
                    # DeepSeek 发现机会！转换为 Signal 并执行
                    await self._execute_deepseek_signal(result)

                elif result and result.get("action") == "adjust":
                    await self._handle_deepseek_adjust(result)

            except Exception as e:
                logger.error(f"猎手引擎异常: {e}")

            await asyncio.sleep(300)  # 5 分钟

    async def _execute_deepseek_signal(self, ds_signal: dict):
        """
        执行 DeepSeek 产生的交易信号

        v3: 强制趋势一致性检查 — DeepSeek 不允许逆势开仓
        问题：DeepSeek 基于 RSI 超买/超卖做反向交易 = 均值回归 = 逆势 = 亏损
        规则：DeepSeek 信号方向必须与 4H 趋势一致，否则拒绝
        """
        inst_id = ds_signal.get("inst_id", "")
        direction = ds_signal.get("direction", "")
        confidence = float(ds_signal.get("confidence", 0))
        entry = float(ds_signal.get("entry_price", 0))
        sl = float(ds_signal.get("stop_loss", 0))
        tp = float(ds_signal.get("take_profit", 0))
        reason = ds_signal.get("reason", "")
        pattern = ds_signal.get("pattern", "")
        pos_pct = float(ds_signal.get("position_pct", 0.2))

        if confidence < 0.5 or not inst_id or not direction:
            return

        # ═══ 强制趋势一致性：不允许逆势开仓 ═══
        if inst_id in self._klines:
            klines = self._klines[inst_id]

            # 4H 趋势判断
            trend_4h = "neutral"
            if "4H" in klines and len(klines["4H"]) >= 50:
                close_4h = klines["4H"]["close"]
                ema20_4h = close_4h.ewm(span=20, adjust=False).mean().iloc[-1]
                ema50_4h = close_4h.ewm(span=53, adjust=False).mean().iloc[-1]
                cur_px = close_4h.iloc[-1]
                if ema20_4h > ema50_4h and cur_px > ema20_4h:
                    trend_4h = "long"
                elif ema20_4h < ema50_4h and cur_px < ema20_4h:
                    trend_4h = "short"

            # 逆势检查
            if trend_4h != "neutral":
                if direction == "short" and trend_4h == "long":
                    logger.warning(
                        f"🚫 DeepSeek 逆势信号被拒: {inst_id} 想做空但 4H 趋势=多头 | {reason[:50]}")
                    await SystemLogDAO.log("WARN", "deepseek",
                        f"🚫 逆势做空被拒 {inst_id}: 4H多头趋势中不做空 | {reason[:60]}")
                    return
                if direction == "long" and trend_4h == "short":
                    logger.warning(
                        f"🚫 DeepSeek 逆势信号被拒: {inst_id} 想做多但 4H 趋势=空头 | {reason[:50]}")
                    await SystemLogDAO.log("WARN", "deepseek",
                        f"🚫 逆势做多被拒 {inst_id}: 4H空头趋势中不做多 | {reason[:60]}")
                    return

            # ADX 趋势强度检查（无趋势不开仓）
            if "4H" in klines and len(klines["4H"]) >= 30:
                adx = self._calc_adx(klines["4H"])
                min_adx = 30 if "BTC" in inst_id else 25
                if adx < min_adx:
                    logger.info(f"🚫 DeepSeek 信号被拒: {inst_id} ADX={adx:.0f}<{min_adx} 无趋势")
                    return

        # 检查盈亏比
        if sl and tp and entry:
            risk = abs(entry - sl)
            reward = abs(tp - entry)
            if risk > 0 and reward / risk < 1.5:
                logger.info(f"🧠 DeepSeek 信号盈亏比不足: {reward/risk:.1f}:1")
                return

        # 手续费预检
        if entry > 0 and tp > 0:
            tp_pct = abs(tp - entry) / entry
            if tp_pct < 0.003:
                logger.info(f"🚫 DeepSeek 信号被拒: {inst_id} TP距离{tp_pct:.2%}<0.3% 手续费吃利润")
                return

        action = SignalAction.OPEN_LONG if direction == "long" else SignalAction.OPEN_SHORT

        signal = Signal(
            strategy_name=f"deepseek_{pattern or 'hunter'}",
            inst_id=inst_id,
            inst_type=InstrumentType.SWAP,
            action=action,
            price=entry,
            stop_loss=sl,
            take_profit=tp,
            reason=f"🧠 DS: {reason}",
            confidence=confidence,
        )

        logger.info(f"🧠 执行 DeepSeek 信号: {inst_id} {direction} @ {entry} | 趋势一致✅ | {reason[:50]}")
        await self._execute_signal(signal)

    async def _handle_deepseek_adjust(self, ds_adjust: dict):
        """处理 DeepSeek 持仓调整建议"""
        inst_id = ds_adjust.get("inst_id", "")
        adjustment = ds_adjust.get("adjustment", "")
        reason = ds_adjust.get("reason", "")

        if adjustment == "close":
            logger.info(f"🧠 DeepSeek 建议平仓: {inst_id} | {reason}")
            await SystemLogDAO.log("WARN", "deepseek", f"🧠 建议平仓 {inst_id}: {reason}")
            # TODO: 自动平仓

        elif adjustment == "tighten_sl":
            new_sl = ds_adjust.get("new_sl")
            if new_sl:
                logger.info(f"🧠 DeepSeek 建议收紧止损: {inst_id} → {new_sl} | {reason}")
                await SystemLogDAO.log("INFO", "deepseek", f"🛡️ 收紧止损 {inst_id} → {new_sl}: {reason}")

    async def _build_market_data(self) -> dict:
        """构建丰富的市场数据包给 DeepSeek"""
        instruments = {}
        for inst in self.swap_instruments:
            ticker = self._tickers.get(inst, {})
            kline_1h = self._klines.get(inst, {}).get("1H")
            kline_15m = self._klines.get(inst, {}).get("15m")

            price = float(ticker.get("last", 0) or 0)
            data = {
                "price": price,
                "high_24h": float(ticker.get("high24h", 0) or 0),
                "low_24h": float(ticker.get("low24h", 0) or 0),
                "volume_24h": float(ticker.get("vol24h", 0) or 0),
            }

            if kline_1h is not None and len(kline_1h) > 50:
                close = kline_1h["close"]
                ema20 = close.ewm(span=20, adjust=False).mean()
                ema50 = close.ewm(span=53, adjust=False).mean()
                ema12 = close.ewm(span=12, adjust=False).mean()
                ema26 = close.ewm(span=26, adjust=False).mean()
                macd = ema12 - ema26
                macd_sig = macd.ewm(span=9, adjust=False).mean()
                macd_hist = macd - macd_sig

                # 布林带
                sma20 = close.rolling(20).mean()
                std20 = close.rolling(20).std()

                data.update({
                    "ema20": round(float(ema20.iloc[-1]), 2),
                    "ema50": round(float(ema50.iloc[-1]), 2),
                    "rsi": round(self._calc_rsi(close, 14), 1),
                    "macd": round(float(macd.iloc[-1]), 4),
                    "macd_signal": round(float(macd_sig.iloc[-1]), 4),
                    "macd_hist": round(float(macd_hist.iloc[-1]), 4),
                    "macd_hist_prev": round(float(macd_hist.iloc[-2]), 4) if len(macd_hist) > 1 else 0,
                    "atr": round(self._calc_atr(kline_1h, 14), 2),
                    "bb_upper": round(float((sma20 + 2 * std20).iloc[-1]), 2),
                    "bb_lower": round(float((sma20 - 2 * std20).iloc[-1]), 2),
                    "bb_middle": round(float(sma20.iloc[-1]), 2),
                })

                # 最近 5 根 1H K线（给 DeepSeek 看形态）
                recent = kline_1h.tail(5)
                data["kline_1h_5"] = [
                    {"o": round(float(r["open"]),2), "h": round(float(r["high"]),2),
                     "l": round(float(r["low"]),2), "c": round(float(r["close"]),2)}
                    for _, r in recent.iterrows()
                ]

            # 15m RSI（更灵敏）
            if kline_15m is not None and len(kline_15m) > 20:
                data["rsi_15m"] = round(self._calc_rsi(kline_15m["close"], 14), 1)

            data["funding_rate"] = self._funding_rates.get(inst, 0)
            instruments[inst] = data

        # 可用保证金比例
        avail_pct = 1.0 - (self.risk.state.open_positions * 0.35)

        return {
            "instruments": instruments,
            "account": {
                "equity": round(self._equity, 2),
                "open_positions": self.risk.state.open_positions,
                "max_positions": self.risk.config["max_positions"],
                "daily_pnl": round(self.risk.state.daily_pnl, 4),
                "available_margin_pct": round(max(avail_pct, 0), 2),
            },
        }

    async def _get_current_positions_summary(self) -> list[dict]:
        """获取当前持仓摘要给 DeepSeek"""
        try:
            from src.data.dao import PositionDAO
            positions = await PositionDAO.get_open_positions()
            return [
                {
                    "inst_id": p["inst_id"],
                    "pos_side": p["pos_side"],
                    "avg_price": float(p.get("avg_price", 0)),
                    "current_price": float(p.get("current_price", 0)),
                    "unrealized_pnl": float(p.get("unrealized_pnl", 0)),
                    "pnl_pct": round(float(p.get("pnl_ratio", 0)) * 100, 2),
                    "size": float(p.get("size", 0)),
                    "leverage": int(p.get("leverage", 5)),
                }
                for p in positions
            ]
        except Exception:
            return []

    async def _review_closed_trade(self, inst_id: str, pos_side: str,
                                    entry_price: float, exit_price: float,
                                    pnl: float, duration_min: int, reason: str):
        """平仓后请求 DeepSeek 复盘"""
        if not self.advisor.has_key:
            return
        trade_data = {
            "inst_id": inst_id, "pos_side": pos_side,
            "entry_price": entry_price, "exit_price": exit_price,
            "pnl": pnl, "duration_minutes": duration_min,
            "entry_reason": reason,
        }
        asyncio.create_task(self.advisor.review_trade(trade_data))

    # ═══ 定时同步 ═══

    async def _sync_loop(self):
        """每 60 秒同步一次"""
        while self._running:
            await asyncio.sleep(60)
            try:
                await self._sync_account()
                # 净值快照每 5 分钟
                if time.time() - self._last_analysis > 300:
                    await AccountDAO.save_snapshot(
                        total_equity=self._equity,
                        max_equity=self.risk.state.max_equity,
                        drawdown=self.risk.state.current_drawdown,
                        unrealized_pnl=await PositionDAO.get_unrealized_pnl(),
                    )
                    self._last_analysis = time.time()

                    await SystemLogDAO.log("INFO", "engine",
                        f"📊 净值: ${self._equity:.2f} | 回撤: {self.risk.state.current_drawdown:.2%} | "
                        f"日PnL: ${self.risk.state.daily_pnl:.2f}")
            except Exception as e:
                logger.error(f"同步异常: {e}")


async def main():
    engine = TradingEngine()
    try:
        await engine.start()
    except KeyboardInterrupt:
        pass
    finally:
        await engine.stop()


if __name__ == "__main__":
    asyncio.run(main())
