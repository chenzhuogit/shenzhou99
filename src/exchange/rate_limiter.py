"""限速控制器 - 令牌桶算法"""
import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class RateLimitRule:
    """限速规则"""
    name: str
    max_requests: int     # 最大请求数
    window_seconds: float  # 时间窗口（秒）
    tokens: float = field(init=False)
    last_refill: float = field(init=False)

    def __post_init__(self):
        self.tokens = float(self.max_requests)
        self.last_refill = time.monotonic()


class RateLimiter:
    """
    令牌桶限速器
    
    遵守 OKX 限速规则：
    - 下单: 60次/2s (单产品)
    - 撤单: 60次/2s (单产品)
    - 批量下单: 300次/2s
    - 查询: 20次/2s
    """

    def __init__(self):
        self._rules: dict[str, RateLimitRule] = {}
        self._lock = asyncio.Lock()
        self._init_default_rules()

    def _init_default_rules(self):
        """初始化 OKX 默认限速规则"""
        self.add_rule("place_order", max_requests=60, window_seconds=2)
        self.add_rule("cancel_order", max_requests=60, window_seconds=2)
        self.add_rule("batch_orders", max_requests=300, window_seconds=2)
        self.add_rule("amend_order", max_requests=60, window_seconds=2)
        self.add_rule("query", max_requests=20, window_seconds=2)
        self.add_rule("ws_order", max_requests=60, window_seconds=2)

    def add_rule(self, name: str, max_requests: int, window_seconds: float):
        """添加限速规则"""
        self._rules[name] = RateLimitRule(name, max_requests, window_seconds)

    def _refill(self, rule: RateLimitRule):
        """补充令牌"""
        now = time.monotonic()
        elapsed = now - rule.last_refill
        refill_amount = elapsed * (rule.max_requests / rule.window_seconds)
        rule.tokens = min(rule.max_requests, rule.tokens + refill_amount)
        rule.last_refill = now

    async def acquire(self, rule_name: str) -> bool:
        """
        获取一个令牌
        
        Args:
            rule_name: 限速规则名称
            
        Returns:
            是否成功获取
        """
        if rule_name not in self._rules:
            return True

        async with self._lock:
            rule = self._rules[rule_name]
            self._refill(rule)

            if rule.tokens >= 1:
                rule.tokens -= 1
                return True

            # 等待令牌恢复
            wait_time = (1 - rule.tokens) * (rule.window_seconds / rule.max_requests)
            await asyncio.sleep(wait_time)
            self._refill(rule)
            rule.tokens -= 1
            return True

    async def wait_and_acquire(self, rule_name: str):
        """阻塞等待直到获取令牌"""
        while not await self.acquire(rule_name):
            await asyncio.sleep(0.01)
