"""限速器测试"""
import asyncio
import time
import pytest
from src.exchange.rate_limiter import RateLimiter


@pytest.mark.asyncio
class TestRateLimiter:

    async def test_acquire_within_limit(self):
        """限速内应立即通过"""
        limiter = RateLimiter()
        limiter.add_rule("test", max_requests=10, window_seconds=1)
        for _ in range(10):
            result = await limiter.acquire("test")
            assert result

    async def test_unknown_rule_passes(self):
        """未知规则应直接通过"""
        limiter = RateLimiter()
        result = await limiter.acquire("nonexistent")
        assert result

    async def test_default_rules_exist(self):
        """默认规则应存在"""
        limiter = RateLimiter()
        assert "place_order" in limiter._rules
        assert "cancel_order" in limiter._rules
        assert "query" in limiter._rules
