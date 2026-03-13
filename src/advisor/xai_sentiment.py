"""
神州99 — xAI Grok 社交媒体情绪分析模块（优化版）

优化策略：
  1. 一次 API 调用分析所有币种（而非每币一次）
  2. 极简 prompt，压缩 token 用量
  3. 30 分钟缓存（情绪不会分钟级变化）
  4. 前端手动刷新才调用（不自动轮询）

Token 消耗估算：
  旧版：5币 × (~500 in + 800 out) = ~6500 tokens/次
  新版：1次 × (~300 in + 600 out) = ~900 tokens/次  ↓ 86%
"""

import os
import json
import asyncio
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None

from loguru import logger

# ═══ 配置 ═══
XAI_API_KEY = os.getenv("XAI_API_KEY", "")
XAI_BASE_URL = os.getenv("XAI_BASE_URL", "https://api.x.ai/v1")
XAI_PROXY = os.getenv("XAI_PROXY", "")
XAI_MODEL = "grok-4-1-fast-non-reasoning"

# 默认分析品种
DEFAULT_COINS = ["BTC", "ETH", "SOL", "DOGE", "ATOM"]

# 缓存 30 分钟（情绪是慢变量，不需要频繁更新）
CACHE_TTL = 1800


@dataclass
class CoinSentiment:
    """单币种情绪"""
    sentiment: str = "neutral"
    score: float = 0.0
    fear_greed: int = 50
    whale: str = "neutral"       # accumulating / distributing / neutral
    signal: str = "hold"
    reason: str = ""
    narratives: list = field(default_factory=list)
    fud: list = field(default_factory=list)
    fomo: str = "none"
    error: str = ""


@dataclass
class SentimentSnapshot:
    """全市场情绪快照"""
    timestamp: str = ""
    coins: dict = field(default_factory=dict)  # coin -> CoinSentiment
    market_mood: str = "neutral"
    tokens_used: int = 0
    error: str = ""


# ═══ 极简 Prompt — 一次分析所有币种 ═══
BATCH_PROMPT = """Crypto Twitter sentiment NOW for: {coins}.
JSON only, no markdown:
{{"market":"{{"mood":"bullish/bearish/neutral"}}","coins":{{{coin_template}}}}}

Per coin: {{"s":"bullish/bearish/neutral","sc":-1to1,"fg":0to100,"w":"acc/dist/n","sig":"buy/sell/hold","r":"10words max","n":["narrative"],"fomo":"h/m/l/n"}}"""


class XAISentimentAnalyzer:

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or XAI_API_KEY
        self.client: Optional[AsyncOpenAI] = None
        self._snapshot: Optional[SentimentSnapshot] = None
        self._snapshot_time: float = 0

        if AsyncOpenAI and self.api_key:
            import httpx
            http_client = None
            if XAI_PROXY:
                http_client = httpx.AsyncClient(proxy=XAI_PROXY)
            self.client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=XAI_BASE_URL,
                http_client=http_client,
            )

    @property
    def available(self) -> bool:
        return self.client is not None and bool(self.api_key)

    async def analyze_market(self, coins: list[str] = None, force: bool = False) -> SentimentSnapshot:
        """
        一次性分析所有币种（核心方法）

        1 次 API 调用 ≈ 900 tokens（而非旧版 5 次 × 1300）
        """
        coins = coins or DEFAULT_COINS

        # 缓存检查（30 分钟）
        import time
        now = time.time()
        if not force and self._snapshot and (now - self._snapshot_time) < CACHE_TTL:
            logger.debug("📱 情绪使用缓存")
            return self._snapshot

        snap = SentimentSnapshot(timestamp=datetime.utcnow().isoformat())

        if not self.available:
            snap.error = "xAI 未配置"
            return snap

        try:
            # 构建极简 prompt
            coin_template = ",".join(f'"{c}":{{}}'  for c in coins)
            prompt = BATCH_PROMPT.format(
                coins=",".join(coins),
                coin_template=coin_template
            )

            response = await self.client.chat.completions.create(
                model=XAI_MODEL,
                messages=[
                    {"role": "system", "content": "Crypto analyst. JSON only. Be concise."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=500,
                temperature=0.2,
            )

            raw = response.choices[0].message.content.strip()

            # 记录 token 用量
            if response.usage:
                snap.tokens_used = response.usage.total_tokens
                logger.info(f"📱 情绪分析消耗 {snap.tokens_used} tokens")

            # 清理 markdown
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                if raw.endswith("```"):
                    raw = raw[:-3]
                raw = raw.strip()

            data = json.loads(raw)

            # 解析市场情绪
            market = data.get("market", {})
            snap.market_mood = market.get("mood", "neutral")

            # 解析各币种
            coins_data = data.get("coins", {})
            for coin in coins:
                cs = CoinSentiment()
                cd = coins_data.get(coin, {})
                if not cd:
                    cs.error = "无数据"
                    snap.coins[coin] = cs
                    continue

                cs.sentiment = cd.get("s", "neutral")
                cs.score = float(cd.get("sc", 0))
                cs.fear_greed = int(cd.get("fg", 50))

                w = cd.get("w", "n")
                cs.whale = {"acc": "accumulating", "dist": "distributing"}.get(w, "neutral")

                cs.signal = cd.get("sig", "hold")
                cs.reason = cd.get("r", "")
                cs.narratives = cd.get("n", [])

                fomo = cd.get("fomo", "n")
                cs.fomo = {"h": "high", "m": "medium", "l": "low"}.get(fomo, "none")

                snap.coins[coin] = cs

                logger.info(
                    f"📱 {coin}: {cs.sentiment}({cs.score:+.1f}) "
                    f"FG={cs.fear_greed} 鲸鱼={cs.whale} → {cs.signal}"
                )

            # 更新缓存
            self._snapshot = snap
            self._snapshot_time = now

        except json.JSONDecodeError as e:
            snap.error = f"JSON解析失败: {e}"
            logger.warning(f"📱 情绪JSON错误: {e}\n原文: {raw[:200]}")
        except Exception as e:
            snap.error = str(e)
            logger.error(f"📱 情绪分析失败: {e}")

        return snap

    def get_trading_adjustment(self, coin: str) -> dict:
        """
        根据缓存的情绪结果，返回交易调整建议（零API调用）

        引擎在开仓前调用此方法，完全使用缓存数据
        """
        adj = {
            "confidence_bonus": 0.0,
            "position_mult": 1.0,
            "should_pause": False,
            "reason": "",
        }

        if not self._snapshot or coin not in self._snapshot.coins:
            return adj

        cs = self._snapshot.coins[coin]
        if cs.error:
            return adj

        # 极端恐惧 → 谨慎
        if cs.fear_greed < 20:
            adj["confidence_bonus"] = -0.05
            adj["reason"] = f"极度恐惧(FG={cs.fear_greed})"

        # 极端贪婪 → 谨慎
        elif cs.fear_greed > 80:
            adj["confidence_bonus"] = -0.05
            adj["reason"] = f"极度贪婪(FG={cs.fear_greed})"

        # 强看涨 + 鲸鱼吸筹
        if cs.score > 0.6 and cs.whale == "accumulating":
            adj["confidence_bonus"] = 0.08
            adj["position_mult"] = 1.2
            adj["reason"] = f"看涨+鲸鱼吸筹(sc={cs.score:.1f})"

        # 强看跌 + 鲸鱼出货
        elif cs.score < -0.6 and cs.whale == "distributing":
            adj["confidence_bonus"] = -0.08
            adj["position_mult"] = 0.7
            adj["reason"] = f"看跌+鲸鱼出货(sc={cs.score:.1f})"

        # FUD 爆发
        if cs.fud and len(cs.fud) >= 2:
            adj["should_pause"] = True
            adj["reason"] = f"FUD爆发"

        # FOMO 极高
        if cs.fomo == "high":
            adj["position_mult"] = min(adj["position_mult"], 0.8)
            if not adj["reason"]:
                adj["reason"] = "FOMO过高"

        return adj

    async def get_summary(self) -> dict:
        """给前端的摘要（使用缓存，不触发新API调用）"""
        snap = self._snapshot

        # 没有缓存则拉一次
        if not snap:
            snap = await self.analyze_market()

        result = {"market_mood": snap.market_mood, "tokens_used": snap.tokens_used}
        data = {}

        for coin, cs in snap.coins.items():
            whale_label = {"accumulating": "吸筹", "distributing": "出货"}.get(cs.whale, "中性")
            signal_label = {"strong_buy": "强力买入", "buy": "买入", "hold": "观望",
                           "sell": "卖出", "strong_sell": "强力卖出"}.get(cs.signal, cs.signal)
            data[coin] = {
                "sentiment": cs.sentiment,
                "score": cs.score,
                "fear_greed_score": cs.fear_greed,
                "whale": whale_label,
                "signal": cs.signal,
                "signal_label": signal_label,
                "signal_reason": cs.reason,
                "fomo": cs.fomo,
                "narratives": cs.narratives[:3],
                "error": cs.error,
            }

        result["data"] = data
        return result

    async def force_refresh(self) -> dict:
        """前端手动刷新（强制新API调用）"""
        await self.analyze_market(force=True)
        return await self.get_summary()


# ═══ Web API 用的全局实例 ═══
_global_analyzer: Optional[XAISentimentAnalyzer] = None

def get_analyzer() -> XAISentimentAnalyzer:
    global _global_analyzer
    if _global_analyzer is None:
        _global_analyzer = XAISentimentAnalyzer()
    return _global_analyzer


# ═══ 独立测试 ═══
async def _test():
    analyzer = XAISentimentAnalyzer()
    if not analyzer.available:
        print("❌ xAI 未配置")
        return

    print("=" * 50)
    print("  xAI 情绪分析（优化版 · 单次批量调用）")
    print("=" * 50)

    snap = await analyzer.analyze_market(force=True)

    if snap.error:
        print(f"❌ {snap.error}")
        return

    print(f"\n  市场情绪: {snap.market_mood}")
    print(f"  Token 消耗: {snap.tokens_used}")
    print()

    for coin, cs in snap.coins.items():
        whale = {"accumulating": "🐋吸筹", "distributing": "🐋出货"}.get(cs.whale, "🐋中性")
        print(f"  {coin:5s} | {cs.sentiment:8s} sc={cs.score:+.2f} | "
              f"FG={cs.fear_greed:2d} | {whale} | → {cs.signal} | {cs.reason}")
        if cs.narratives:
            print(f"         叙事: {', '.join(cs.narratives[:2])}")

    # 测试调整建议
    print(f"\n  交易调整建议:")
    for coin in snap.coins:
        adj = analyzer.get_trading_adjustment(coin)
        if adj["reason"]:
            print(f"    {coin}: {adj['reason']} | 信心{adj['confidence_bonus']:+.2f} 仓位×{adj['position_mult']:.1f}")


if __name__ == "__main__":
    asyncio.run(_test())
