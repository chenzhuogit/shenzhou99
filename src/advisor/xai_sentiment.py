"""
神州99 — xAI Grok 社交媒体情绪分析模块

利用 Grok 的 Twitter/X 实时数据访问能力，分析加密货币社区舆情。
作为独立模块运行，不影响现有交易逻辑，仅提供参考信号。

用法:
    # 单独测试
    PYTHONPATH=. python3 src/advisor/xai_sentiment.py

    # 在引擎中调用
    from src.advisor.xai_sentiment import XAISentimentAnalyzer
    analyzer = XAISentimentAnalyzer()
    result = await analyzer.analyze("BTC")
"""

import os
import json
import asyncio
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field, asdict

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None

from loguru import logger


@dataclass
class SentimentResult:
    """情绪分析结果"""
    coin: str = ""
    timestamp: str = ""
    # 情绪
    sentiment: str = "neutral"       # bullish / bearish / neutral
    score: float = 0.0               # -1.0 到 1.0
    confidence: float = 0.0          # 0-1
    # 恐惧贪婪
    fear_greed: str = "neutral"      # extreme fear / fear / neutral / greed / extreme greed
    fear_greed_score: int = 50       # 0-100
    # 指标
    bullish_pct: int = 50
    bearish_pct: int = 50
    mention_volume: str = "medium"   # high / medium / low
    trend: str = "stable"            # increasing / stable / decreasing
    # 鲸鱼
    whale_accumulating: bool = False
    whale_distributing: bool = False
    whale_notes: list = field(default_factory=list)
    # 叙事
    narratives: list = field(default_factory=list)
    fud_alerts: list = field(default_factory=list)
    fomo_level: str = "none"         # high / medium / low / none
    # 交易建议
    trading_signal: str = "hold"     # strong_buy / buy / hold / sell / strong_sell
    signal_reason: str = ""
    # 原始响应
    raw_response: str = ""
    error: str = ""


# ═══ 配置 ═══
XAI_API_KEY = os.getenv("XAI_API_KEY", "")
XAI_BASE_URL = os.getenv("XAI_BASE_URL", "https://api.x.ai/v1")
XAI_PROXY = os.getenv("XAI_PROXY", "")  # 如被 Cloudflare 拦截，设置 HTTP 代理
XAI_MODEL = "grok-4-1-fast-non-reasoning"  # 便宜快速：$0.20/M in, $0.50/M out

# 分析的品种列表
INSTRUMENTS = ["BTC", "ETH", "SOL", "DOGE", "ATOM"]

# 分析 prompt（结构化输出）
SENTIMENT_PROMPT = """Analyze real-time Crypto Twitter (CT) sentiment for {coin}. 
Search for the latest tweets, discussions, and trending topics about {coin} in the last few hours.

Return ONLY valid JSON (no markdown, no code blocks):
{{
  "coin": "{coin}",
  "sentiment": {{
    "overall": "bullish" or "bearish" or "neutral",
    "score": -1.0 to 1.0,
    "confidence": 0.0 to 1.0
  }},
  "fear_greed": {{
    "label": "extreme fear" or "fear" or "neutral" or "greed" or "extreme greed",
    "score": 0 to 100
  }},
  "metrics": {{
    "bullish_percent": 0-100,
    "bearish_percent": 0-100,
    "mention_volume": "high" or "medium" or "low",
    "trend": "increasing" or "stable" or "decreasing"
  }},
  "whale_activity": {{
    "accumulating": true/false,
    "distributing": true/false,
    "notes": ["note1", "note2"]
  }},
  "narratives": ["narrative1", "narrative2"],
  "fud_alerts": ["alert1"],
  "fomo_level": "high" or "medium" or "low" or "none",
  "trading_signal": {{
    "action": "strong_buy" or "buy" or "hold" or "sell" or "strong_sell",
    "reason": "brief reason"
  }}
}}"""


class XAISentimentAnalyzer:
    """xAI Grok 情绪分析器"""

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or XAI_API_KEY
        self.client: Optional[AsyncOpenAI] = None
        self._cache: dict[str, SentimentResult] = {}
        self._cache_ttl = 300  # 5分钟缓存

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

    async def analyze(self, coin: str) -> SentimentResult:
        """分析单个币种的 Twitter 情绪"""
        result = SentimentResult(
            coin=coin,
            timestamp=datetime.utcnow().isoformat(),
        )

        if not self.available:
            result.error = "xAI 未配置（缺少 API Key）"
            return result

        # 缓存检查
        cache_key = coin.upper()
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            cached_time = datetime.fromisoformat(cached.timestamp)
            if (datetime.utcnow() - cached_time).total_seconds() < self._cache_ttl:
                logger.debug(f"📱 {coin} 情绪使用缓存")
                return cached

        try:
            prompt = SENTIMENT_PROMPT.format(coin=coin)

            response = await self.client.chat.completions.create(
                model=XAI_MODEL,
                messages=[
                    {"role": "system", "content": "You are a crypto market analyst. Always respond with valid JSON only. No markdown formatting."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=800,
                temperature=0.3,
            )

            raw = response.choices[0].message.content.strip()
            result.raw_response = raw

            # 解析 JSON
            # 清理可能的 markdown 包裹
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                if raw.endswith("```"):
                    raw = raw[:-3]
                raw = raw.strip()

            data = json.loads(raw)
            result = self._parse_response(data, result)
            self._cache[cache_key] = result

            logger.info(
                f"📱 {coin} 情绪: {result.sentiment} "
                f"(score={result.score:.2f}, 恐惧贪婪={result.fear_greed_score}) "
                f"信号={result.trading_signal}"
            )

        except json.JSONDecodeError as e:
            result.error = f"JSON 解析失败: {e}"
            logger.warning(f"📱 {coin} 情绪分析 JSON 错误: {e}")
        except Exception as e:
            result.error = str(e)
            logger.error(f"📱 {coin} 情绪分析失败: {e}")

        return result

    def _parse_response(self, data: dict, result: SentimentResult) -> SentimentResult:
        """解析 Grok 返回的 JSON"""
        # 情绪
        s = data.get("sentiment", {})
        result.sentiment = s.get("overall", "neutral")
        result.score = float(s.get("score", 0))
        result.confidence = float(s.get("confidence", 0))

        # 恐惧贪婪
        fg = data.get("fear_greed", {})
        result.fear_greed = fg.get("label", "neutral")
        result.fear_greed_score = int(fg.get("score", 50))

        # 指标
        m = data.get("metrics", {})
        result.bullish_pct = int(m.get("bullish_percent", 50))
        result.bearish_pct = int(m.get("bearish_percent", 50))
        result.mention_volume = m.get("mention_volume", "medium")
        result.trend = m.get("trend", "stable")

        # 鲸鱼
        w = data.get("whale_activity", {})
        result.whale_accumulating = bool(w.get("accumulating", False))
        result.whale_distributing = bool(w.get("distributing", False))
        result.whale_notes = w.get("notes", [])

        # 叙事
        result.narratives = data.get("narratives", [])
        result.fud_alerts = data.get("fud_alerts", [])
        result.fomo_level = data.get("fomo_level", "none")

        # 交易信号
        ts = data.get("trading_signal", {})
        result.trading_signal = ts.get("action", "hold")
        result.signal_reason = ts.get("reason", "")

        return result

    async def analyze_all(self, coins: list[str] = None) -> dict[str, SentimentResult]:
        """分析所有品种"""
        coins = coins or INSTRUMENTS
        results = {}
        for coin in coins:
            results[coin] = await self.analyze(coin)
            await asyncio.sleep(0.5)  # 避免限流
        return results

    def get_trading_adjustment(self, result: SentimentResult) -> dict:
        """
        根据情绪分析结果，返回交易调整建议

        返回:
            {
                "confidence_bonus": -0.1 到 0.1,   # 信心加减分
                "position_mult": 0.5 到 1.5,       # 仓位倍数
                "should_pause": False,              # 是否暂停交易
                "reason": "...",
            }
        """
        adj = {
            "confidence_bonus": 0.0,
            "position_mult": 1.0,
            "should_pause": False,
            "reason": "",
        }

        if result.error:
            return adj  # 出错不调整

        # 极端恐惧 → 可能反弹，谨慎做空
        if result.fear_greed_score < 20:
            adj["confidence_bonus"] = -0.05  # 降低信心
            adj["reason"] = f"极度恐惧({result.fear_greed_score})，市场可能反弹"

        # 极端贪婪 → 可能回调，谨慎做多
        elif result.fear_greed_score > 80:
            adj["confidence_bonus"] = -0.05
            adj["reason"] = f"极度贪婪({result.fear_greed_score})，注意回调风险"

        # 强看涨 + 鲸鱼吸筹 → 加仓
        if result.score > 0.6 and result.whale_accumulating:
            adj["confidence_bonus"] = 0.08
            adj["position_mult"] = 1.2
            adj["reason"] = f"强看涨+鲸鱼吸筹 (score={result.score:.2f})"

        # 强看跌 + 鲸鱼出货 → 减仓
        elif result.score < -0.6 and result.whale_distributing:
            adj["confidence_bonus"] = -0.08
            adj["position_mult"] = 0.7
            adj["reason"] = f"强看跌+鲸鱼出货 (score={result.score:.2f})"

        # FUD 爆发 → 暂停
        if len(result.fud_alerts) >= 3:
            adj["should_pause"] = True
            adj["reason"] = f"FUD 爆发({len(result.fud_alerts)}条): {result.fud_alerts[0]}"

        # FOMO 极高 → 不追涨
        if result.fomo_level == "high":
            adj["position_mult"] = min(adj["position_mult"], 0.8)
            adj["reason"] = adj["reason"] or "FOMO 过高，控制仓位"

        return adj

    async def get_summary(self) -> dict:
        """获取所有品种的情绪摘要（给前端用）"""
        results = await self.analyze_all()
        summary = {}
        for coin, r in results.items():
            summary[coin] = {
                "sentiment": r.sentiment,
                "score": r.score,
                "confidence": r.confidence,
                "fear_greed": r.fear_greed,
                "fear_greed_score": r.fear_greed_score,
                "bullish_pct": r.bullish_pct,
                "bearish_pct": r.bearish_pct,
                "mention_volume": r.mention_volume,
                "trend": r.trend,
                "whale": "吸筹" if r.whale_accumulating else ("出货" if r.whale_distributing else "中性"),
                "signal": r.trading_signal,
                "signal_reason": r.signal_reason,
                "fomo": r.fomo_level,
                "narratives": r.narratives[:3],
                "fud_alerts": r.fud_alerts[:2],
                "error": r.error,
            }
        return summary


# ═══ 独立测试 ═══
async def _test():
    analyzer = XAISentimentAnalyzer()
    if not analyzer.available:
        print("❌ xAI 未配置，请设置 XAI_API_KEY 环境变量")
        return

    print("═" * 60)
    print("  xAI Grok 加密货币情绪分析")
    print("═" * 60)

    for coin in ["BTC", "ETH", "SOL"]:
        print(f"\n  🔍 分析 {coin}...")
        result = await analyzer.analyze(coin)
        if result.error:
            print(f"  ❌ {result.error}")
            continue

        print(f"  情绪: {result.sentiment} (score={result.score:.2f})")
        print(f"  恐惧贪婪: {result.fear_greed} ({result.fear_greed_score})")
        print(f"  看涨{result.bullish_pct}% / 看跌{result.bearish_pct}%")
        print(f"  提及量: {result.mention_volume} 趋势: {result.trend}")
        print(f"  鲸鱼: {'吸筹' if result.whale_accumulating else '出货' if result.whale_distributing else '中性'}")
        print(f"  信号: {result.trading_signal} — {result.signal_reason}")
        if result.narratives:
            print(f"  叙事: {', '.join(result.narratives[:3])}")

        adj = analyzer.get_trading_adjustment(result)
        if adj["reason"]:
            print(f"  ⚙️ 调整: {adj['reason']}")
            print(f"     信心{adj['confidence_bonus']:+.2f} 仓位×{adj['position_mult']:.1f}")


if __name__ == "__main__":
    asyncio.run(_test())
