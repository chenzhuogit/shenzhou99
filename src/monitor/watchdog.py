"""
神州99 自我监控看门狗 🐕

功能：
1. 实时监控引擎日志
2. 检测异常模式（错误、进程崩溃、连接断开）
3. 自动修复已知问题
4. 未知问题通知到 Telegram
5. 监控交易表现，检测异常

自我进化：每次修复问题后，将修复方案记录到 knowledge base
"""
import os
import sys
import asyncio
import time
import json
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

from loguru import logger

PROJECT_ROOT = Path(__file__).parent.parent.parent
LOG_FILE = PROJECT_ROOT / "logs" / "engine.log"
WATCHDOG_LOG = PROJECT_ROOT / "logs" / "watchdog.log"
KNOWLEDGE_FILE = PROJECT_ROOT / "logs" / "watchdog_knowledge.json"
ENGINE_PID_FILE = PROJECT_ROOT / "logs" / "engine.pid"
WEB_PID_FILE = PROJECT_ROOT / "logs" / "web.pid"

# 配置 watchdog 自己的日志
logger.add(str(WATCHDOG_LOG), rotation="5 MB", retention=3, level="INFO")


class KnowledgeBase:
    """已知问题和修复方案"""

    def __init__(self):
        self.path = KNOWLEDGE_FILE
        self.knowledge = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text())
        return {"fixes": [], "patterns": {}, "stats": {"total_errors": 0, "auto_fixed": 0, "escalated": 0}}

    def save(self):
        self.path.write_text(json.dumps(self.knowledge, ensure_ascii=False, indent=2, default=str))

    def record_fix(self, error_pattern: str, fix_action: str, success: bool):
        self.knowledge["fixes"].append({
            "time": datetime.now().isoformat(),
            "pattern": error_pattern,
            "action": fix_action,
            "success": success,
        })
        if success:
            self.knowledge["stats"]["auto_fixed"] += 1
        self.save()

    def record_escalation(self, error: str, reason: str):
        self.knowledge["stats"]["escalated"] += 1
        self.save()

    def get_stats(self) -> dict:
        return self.knowledge["stats"]


class AutoFixer:
    """自动修复器：已知问题自动处理"""

    def __init__(self, kb: KnowledgeBase):
        self.kb = kb
        self._last_restart = 0
        self._error_counts = defaultdict(int)
        self._suppressed_until = defaultdict(float)  # 错误抑制

    async def try_fix(self, error_type: str, error_msg: str, log_line: str) -> tuple[bool, str]:
        """尝试自动修复，返回 (是否修复, 描述)"""

        # ── 余额不足 ──
        if "51008" in error_msg or "insufficient" in error_msg.lower():
            return True, "已知问题：余额不足。引擎已内置冷却机制，无需额外处理"

        # ── WebSocket 断连 ──
        if "ConnectionClosed" in error_msg or "Connection refused" in error_msg:
            return True, "WebSocket 断连，引擎有自动重连机制，等待重连"

        # ── MySQL 连接池满 ──
        if "pool" in error_msg.lower() and ("full" in error_msg.lower() or "timeout" in error_msg.lower()):
            return True, "连接池临时满载，会自动恢复"

        # ── Data truncated 警告（不是真错误） ──
        if "Data truncated" in error_msg:
            return True, "MySQL 精度截断警告，不影响业务"

        # ── 引擎进程崩溃 ──
        if error_type == "process_dead":
            if time.time() - self._last_restart < 120:
                return False, "2分钟内已重启过，不再自动重启，需要人工介入"
            self._last_restart = time.time()
            success = await self._restart_engine()
            self.kb.record_fix("process_dead", "restart_engine", success)
            return success, "引擎崩溃，已自动重启" if success else "引擎重启失败"

        # ── Web 进程崩溃 ──
        if error_type == "web_dead":
            if time.time() - self._last_restart < 120:
                return False, "2分钟内已重启过"
            success = await self._restart_web()
            self.kb.record_fix("web_dead", "restart_web", success)
            return success, "Web 服务已重启" if success else "Web 重启失败"

        # ── 未知错误 → 求助 DeepSeek ──
        try:
            from src.advisor.strategy_advisor import StrategyAdvisor
            advisor = StrategyAdvisor()
            if advisor.has_key:
                result = await advisor.diagnose_error({
                    "error_message": error_msg[:500],
                    "module": error_type,
                    "timestamp": datetime.now().isoformat(),
                })
                if result:
                    severity = result.get("severity", "unknown")
                    fix = result.get("fix_suggestion", "")
                    self.kb.record_fix(error_msg[:100], f"deepseek_diagnosis: {fix[:100]}", False)
                    return False, f"DeepSeek 诊断 [{severity}]: {fix[:200]}"
                await advisor.close()
        except Exception:
            pass

        return False, "未知错误，需要人工分析"

    async def _restart_engine(self) -> bool:
        try:
            subprocess.run(["pkill", "-f", "src.core.engine"], timeout=5)
            await asyncio.sleep(2)
            proc = subprocess.Popen(
                ["python3", "-m", "src.core.engine"],
                cwd=str(PROJECT_ROOT),
                stdout=open(str(LOG_FILE), "a"),
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            ENGINE_PID_FILE.write_text(str(proc.pid))
            logger.info(f"🔄 引擎已重启 PID={proc.pid}")
            return True
        except Exception as e:
            logger.error(f"重启引擎失败: {e}")
            return False

    async def _restart_web(self) -> bool:
        try:
            subprocess.run(["bash", "-c", "kill $(lsof -ti:8899) 2>/dev/null"], timeout=5)
            await asyncio.sleep(1)
            proc = subprocess.Popen(
                ["python3", "web/server.py"],
                cwd=str(PROJECT_ROOT),
                stdout=open(str(PROJECT_ROOT / "logs" / "web.log"), "a"),
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            WEB_PID_FILE.write_text(str(proc.pid))
            logger.info(f"🔄 Web 已重启 PID={proc.pid}")
            return True
        except Exception as e:
            logger.error(f"重启 Web 失败: {e}")
            return False

    def should_suppress(self, error_key: str) -> bool:
        """相同错误 5 分钟内只报一次"""
        now = time.time()
        if now < self._suppressed_until.get(error_key, 0):
            return True
        self._suppressed_until[error_key] = now + 300
        return False


class Watchdog:
    """主看门狗"""

    def __init__(self):
        self.kb = KnowledgeBase()
        self.fixer = AutoFixer(self.kb)
        self._last_pos = 0
        self._running = True
        self._notification_queue: list[str] = []

    async def run(self):
        logger.info("🐕 看门狗启动")
        print("🐕 神州99 看门狗启动")
        print(f"   监控日志: {LOG_FILE}")
        print(f"   知识库: {KNOWLEDGE_FILE}")

        await asyncio.gather(
            self._watch_logs(),
            self._health_check(),
            self._performance_check(),
            self._flush_notifications(),
        )

    async def _watch_logs(self):
        """实时监控引擎日志文件"""
        # 跳到文件末尾
        if LOG_FILE.exists():
            self._last_pos = LOG_FILE.stat().st_size

        while self._running:
            await asyncio.sleep(2)
            try:
                if not LOG_FILE.exists():
                    continue

                size = LOG_FILE.stat().st_size
                if size < self._last_pos:
                    self._last_pos = 0  # 日志被截断/轮转

                if size <= self._last_pos:
                    continue

                with open(LOG_FILE, "r") as f:
                    f.seek(self._last_pos)
                    new_lines = f.readlines()
                    self._last_pos = f.tell()

                for line in new_lines:
                    line = line.strip()
                    if not line:
                        continue
                    await self._analyze_log_line(line)

            except Exception as e:
                logger.error(f"日志监控异常: {e}")

    async def _analyze_log_line(self, line: str):
        """分析单条日志"""
        # 跳过 Warning（MySQL truncation 等）
        if "Warning:" in line and "Data truncated" in line:
            return

        # 检测 ERROR 级别
        if "| ERROR" in line or "Traceback" in line:
            self.kb.knowledge["stats"]["total_errors"] += 1

            # 提取错误信息
            error_msg = line.split("- ", 1)[-1] if "- " in line else line

            # 生成错误 key（去重用）
            error_key = re.sub(r'\d+', 'N', error_msg)[:80]

            if self.fixer.should_suppress(error_key):
                return

            # 尝试自动修复
            fixed, desc = await self.fixer.try_fix("log_error", error_msg, line)

            if fixed:
                logger.info(f"🔧 自动处理: {desc}")
                await self._write_to_db("INFO", "watchdog", f"🔧 自动处理: {desc}")
            else:
                logger.warning(f"⚠️ 需要关注: {desc} | {error_msg[:100]}")
                await self._write_to_db("WARN", "watchdog", f"⚠️ 未知错误: {error_msg[:200]}")
                self._notification_queue.append(
                    f"⚠️ 神州99 错误\n{error_msg[:300]}\n\n看门狗无法自动修复: {desc}"
                )
                self.kb.record_escalation(error_msg[:200], desc)

    async def _health_check(self):
        """每 30 秒检查进程健康"""
        while self._running:
            await asyncio.sleep(30)
            try:
                # 检查引擎进程
                engine_alive = self._check_process("src.core.engine")
                if not engine_alive:
                    logger.warning("💀 引擎进程不存在！")
                    fixed, desc = await self.fixer.try_fix("process_dead", "", "")
                    if not fixed:
                        self._notification_queue.append(f"🚨 神州99 引擎崩溃！\n自动重启失败: {desc}")
                    else:
                        await self._write_to_db("WARN", "watchdog", f"🔄 引擎崩溃已自动重启")

                # 检查 Web 进程
                web_alive = self._check_process("web/server.py") or self._check_port(8899)
                if not web_alive:
                    logger.warning("💀 Web 服务不存在！")
                    fixed, desc = await self.fixer.try_fix("web_dead", "", "")
                    if not fixed:
                        self._notification_queue.append(f"🚨 Web 控制台崩溃！\n{desc}")
                    else:
                        await self._write_to_db("INFO", "watchdog", f"🔄 Web 服务已自动重启")

            except Exception as e:
                logger.error(f"健康检查异常: {e}")

    async def _performance_check(self):
        """每 5 分钟检查交易表现"""
        while self._running:
            await asyncio.sleep(300)
            try:
                # 从 MySQL 拉交易数据分析
                from src.data.database import Database
                if not Database._pool:
                    await Database.init_pool()

                # 连续亏损检查
                recent = await Database.fetch_all(
                    "SELECT realized_pnl FROM positions WHERE status='closed' ORDER BY closed_at DESC LIMIT 10"
                )
                consecutive_losses = 0
                for r in recent:
                    if float(r.get("realized_pnl", 0) or 0) < 0:
                        consecutive_losses += 1
                    else:
                        break

                if consecutive_losses >= 3:
                    msg = f"⚠️ 连续 {consecutive_losses} 笔亏损！建议检查策略"
                    logger.warning(msg)
                    await self._write_to_db("WARN", "watchdog", msg)
                    if consecutive_losses >= 5:
                        self._notification_queue.append(f"🚨 神州99 连续 {consecutive_losses} 笔亏损！需要人工干预")

                # 检查净值是否大幅下降
                snap = await Database.fetch_one(
                    "SELECT total_equity FROM account_snapshots ORDER BY id DESC LIMIT 1"
                )
                if snap:
                    equity = float(snap.get("total_equity", 0) or 0)
                    if equity < 450:  # 初始500，亏损>10%
                        self._notification_queue.append(
                            f"🚨 净值警告！当前 ${equity:.2f}，亏损超过 10%"
                        )

                stats = self.kb.get_stats()
                logger.info(
                    f"📊 看门狗统计: 总错误={stats['total_errors']} "
                    f"自动修复={stats['auto_fixed']} 上报={stats['escalated']}"
                )

            except Exception as e:
                logger.error(f"绩效检查异常: {e}")

    async def _flush_notifications(self):
        """发送通知到 DB（前端可见），严重问题标记为 CRITICAL"""
        while self._running:
            await asyncio.sleep(10)
            while self._notification_queue:
                msg = self._notification_queue.pop(0)
                level = "CRITICAL" if "🚨" in msg else "WARN"
                await self._write_to_db(level, "watchdog", msg)
                logger.info(f"📤 通知: {msg[:80]}")

    async def _write_to_db(self, level: str, module: str, message: str):
        try:
            from src.data.database import Database
            if not Database._pool:
                await Database.init_pool()
            from src.data.dao import SystemLogDAO
            await SystemLogDAO.log(level, module, message)
        except Exception:
            pass  # DB 不可用时静默

    def _check_process(self, name: str) -> bool:
        try:
            result = subprocess.run(
                ["pgrep", "-f", name], capture_output=True, timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False

    def _check_port(self, port: int) -> bool:
        try:
            result = subprocess.run(
                ["bash", "-c", f"lsof -i:{port} | grep LISTEN"],
                capture_output=True, timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False


async def main():
    dog = Watchdog()
    try:
        await dog.run()
    except KeyboardInterrupt:
        dog._running = False


if __name__ == "__main__":
    asyncio.run(main())
