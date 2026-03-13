"""
神州99 DeepSeek 策略引擎

DeepSeek 不是顾问，是合伙人——它是第二个交易员。

职能：
1. 🔍 猎手模式 — 每5分钟扫描，发现技术指标抓不到的机会
2. 🎯 精准狙击 — 识别K线形态、背离、关键位突破，直接发信号
3. 📊 资金费率套利 — 极端费率时反向开仓吃费率
4. 🧠 动态风控 — 根据市场状态实时调整止损止盈距离
5. 📝 复盘进化 — 每笔平仓后分析，持续优化
"""
import os
import sys
import json
import asyncio
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from loguru import logger

from src.advisor.deepseek_client import DeepSeekClient
from src.data.database import Database
from src.data.dao import SystemLogDAO


# ═══ DeepSeek 猎手 Prompt ═══

HUNTER_SYSTEM = """你是「神州99」量化交易系统的核心策略引擎，代号 DeepSeek-Hunter。
你的血统来自幻方量化——中国最强量化基金。你的使命：抓住每一个交易机会赚钱。

你是第二个交易员，不是顾问。你要主动发现机会。

## 你擅长的（技术指标系统做不到的）

1. **K线形态识别** — 头肩、双底/顶、旗形、楔形、吞没、锤子线
2. **背离发现** — 价格新高但 RSI/MACD 没跟上 = 反转机会
3. **关键位分析** — 整数关口、前高前低、布林带边界
4. **市场情绪** — 资金费率极值 = 情绪过热 = 反转机会
5. **跨品种联动** — BTC 领先 ETH，BTC 突破后 ETH 跟随

## 你的输出

严格 JSON，三种可能：

### 1. 发现交易机会
```json
{
  "action": "signal",
  "inst_id": "BTC-USDT-SWAP 或 ETH-USDT-SWAP",
  "direction": "long 或 short",
  "entry_price": 当前价格,
  "stop_loss": 止损价,
  "take_profit": 止盈价,
  "confidence": 0.0-1.0,
  "position_pct": 0.1-0.4,
  "reason": "具体理由（中文，包含你看到的形态/背离/关键位）",
  "pattern": "识别到的形态名称",
  "urgency": "high/medium/low"
}
```

### 2. 建议调整现有持仓
```json
{
  "action": "adjust",
  "inst_id": "BTC-USDT-SWAP",
  "adjustment": "tighten_sl 或 widen_tp 或 close",
  "new_sl": 新止损价（如适用）,
  "new_tp": 新止盈价（如适用）,
  "reason": "理由"
}
```

### 3. 没有明确机会
```json
{
  "action": "wait",
  "market_state": "trending/ranging/volatile/quiet",
  "next_opportunity": "描述下一个可能的机会在哪里",
  "watch_levels": {"BTC": {"support": 价格, "resistance": 价格}, "ETH": {...}}
}
```

## 决策规则
- 信心 < 0.5 → 不发 signal，发 wait
- 盈亏比 < 1.5:1 → 不发 signal
- 资金费率 > 0.1% 或 < -0.1% → 考虑反向套利
- 发现明确形态 + 指标确认 → 信心可以给 0.7+
- 不确定就说 wait，宁可错过不可做错"""


REVIEW_SYSTEM = """你是「神州99」交易复盘师。分析已完成的交易，找出赚钱/亏钱的原因。

输出 JSON：
{
  "score": 1-10,
  "profit_or_loss": "profit/loss",
  "entry_timing": "early/good/late/bad",
  "exit_timing": "early/good/late/bad",
  "what_worked": "做对了什么",
  "what_failed": "做错了什么",
  "lesson": "一句话教训",
  "next_time": "下次遇到类似情况应该怎么做"
}"""


class StrategyAdvisor:
    """DeepSeek 策略引擎 — 第二个交易员"""

    def __init__(self):
        self.client = DeepSeekClient()
        self._last_result: Optional[dict] = None
        self._pending_signals: list[dict] = []  # 待执行的信号队列
        self._call_count = 0
        self._signal_count = 0

    @property
    def has_key(self) -> bool:
        return bool(self.client.api_key)

    def get_pending_signal(self) -> Optional[dict]:
        """引擎每15秒调用，获取 DeepSeek 产生的信号"""
        if self._pending_signals:
            return self._pending_signals.pop(0)
        return None

    # ═══ 猎手模式：主动发现机会 ═══

    async def hunt(self, market_data: dict, positions: list[dict]) -> Optional[dict]:
        """
        猎手模式：分析市场，主动发现交易机会

        market_data: {
            "instruments": {
                "BTC-USDT-SWAP": {
                    "price", "high_24h", "low_24h", "volume_24h",
                    "ema20", "ema50", "rsi", "macd", "macd_signal", "macd_hist",
                    "atr", "funding_rate",
                    "kline_5": [最近5根1H K线 {o,h,l,c}],
                    "bb_upper", "bb_lower", "bb_middle",
                }, ...
            },
            "account": {"equity", "open_positions", "daily_pnl", "available_margin_pct"},
            "open_positions": [{"inst_id","pos_side","avg_price","unrealized_pnl","pnl_pct"}]
        }
        """
        if not self.has_key:
            return None

        self._call_count += 1

        prompt = f"""当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}

## 市场数据
{json.dumps(market_data, indent=2, ensure_ascii=False)}

## 当前持仓
{json.dumps(positions, indent=2, ensure_ascii=False) if positions else '无持仓'}

任务：扫描所有品种，发现交易机会。记住使命——抓住每一个机会赚钱。
如果有明确机会就发 signal，没有就发 wait 并告诉我关注什么价位。"""

        try:
            response = await self.client.chat(HUNTER_SYSTEM, prompt, max_tokens=4000)
            if not response:
                return None

            result = self._parse_json(response)
            if not result:
                return None

            self._last_result = result
            action = result.get("action", "wait")

            if action == "signal":
                self._signal_count += 1
                inst = result.get("inst_id", "")
                direction = result.get("direction", "")
                conf = result.get("confidence", 0)
                reason = result.get("reason", "")
                pattern = result.get("pattern", "")

                # 加入信号队列
                self._pending_signals.append(result)

                await SystemLogDAO.log("INFO", "deepseek",
                    f"🎯 猎手信号 #{self._signal_count}: {inst} {direction} "
                    f"({conf*100:.0f}%) | {pattern} | {reason[:60]}")
                logger.info(f"🎯 DeepSeek 猎手: {inst} {direction} conf={conf} | {reason[:80]}")

            elif action == "adjust":
                inst = result.get("inst_id", "")
                adj = result.get("adjustment", "")
                reason = result.get("reason", "")
                self._pending_signals.append(result)
                await SystemLogDAO.log("INFO", "deepseek",
                    f"🔧 持仓调整: {inst} {adj} | {reason[:60]}")

            elif action == "wait":
                state = result.get("market_state", "?")
                next_opp = result.get("next_opportunity", "")
                levels = result.get("watch_levels", {})
                await SystemLogDAO.log("INFO", "deepseek",
                    f"⏳ 观望 | {state} | {next_opp[:60]}")

                # 更新关注价位到模块状态
                level_str = ""
                for sym, lvls in levels.items():
                    s = lvls.get("support", 0)
                    r = lvls.get("resistance", 0)
                    if s and r:
                        level_str += f"{sym}:{s}-{r} "
                from src.data.dao import ModuleStatusDAO
                await ModuleStatusDAO.update_status("deepseek_advisor", "ok",
                    f"{state} | {next_opp[:30]} | {level_str}")

            return result

        except Exception as e:
            logger.error(f"猎手异常: {e}")
            return None

    # ═══ 复盘 ═══

    async def review_trade(self, trade_data: dict) -> Optional[dict]:
        """平仓后复盘"""
        if not self.has_key:
            return None

        prompt = f"""复盘这笔交易：
{json.dumps(trade_data, indent=2, ensure_ascii=False)}"""

        try:
            response = await self.client.chat(REVIEW_SYSTEM, prompt, max_tokens=3000)
            if not response:
                return None

            result = self._parse_json(response)
            if result:
                score = result.get("score", 0)
                lesson = result.get("lesson", "")
                await SystemLogDAO.log("INFO", "deepseek",
                    f"📝 复盘 {trade_data.get('inst_id','')} | "
                    f"{score}/10 | {lesson}")
                return result

        except Exception as e:
            logger.error(f"复盘异常: {e}")
            return None

    # ═══ 错误诊断 ═══

    async def diagnose_error(self, error_context: dict) -> Optional[dict]:
        if not self.has_key:
            return None
        prompt = f"系统错误：\n{json.dumps(error_context, indent=2, ensure_ascii=False)}"
        try:
            resp = await self.client.chat(
                "你是交易系统运维专家。诊断错误，输出JSON: {\"root_cause\":\"\",\"severity\":\"critical/high/medium/low\",\"fix_suggestion\":\"\"}",
                prompt, max_tokens=2000)
            return self._parse_json(resp) if resp else None
        except Exception:
            return None

    # ═══ 统计 ═══

    def get_stats(self) -> dict:
        return {
            "total_calls": self._call_count,
            "signals_generated": self._signal_count,
            "pending_signals": len(self._pending_signals),
            "last_result": self._last_result,
        }

    # ═══ JSON 解析 ═══

    def _parse_json(self, text: str) -> Optional[dict]:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        import re
        matches = re.findall(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        for m in matches:
            try:
                return json.loads(m.strip())
            except json.JSONDecodeError:
                continue
        brace_start = text.find('{')
        brace_end = text.rfind('}')
        if brace_start >= 0 and brace_end > brace_start:
            try:
                return json.loads(text[brace_start:brace_end+1])
            except json.JSONDecodeError:
                pass
        logger.warning(f"JSON 解析失败: {text[:100]}")
        return None

    async def close(self):
        await self.client.close()
