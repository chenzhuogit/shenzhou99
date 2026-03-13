"""DeepSeek API 客户端"""
import os
import json
import aiohttp
from loguru import logger


class DeepSeekClient:
    """DeepSeek API 调用封装"""

    BASE_URL = "https://api.deepseek.com/v1"

    def __init__(self, api_key: str = None, model: str = "deepseek-reasoner"):
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        self.model = model
        self._session: aiohttp.ClientSession = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=120),  # reasoner 需要更长思考时间
            )
        return self._session

    async def chat(self, system_prompt: str, user_prompt: str,
                   temperature: float = 0.3, max_tokens: int = 2000) -> str:
        """调用 DeepSeek Chat API"""
        if not self.api_key:
            logger.warning("DeepSeek API Key 未配置")
            return ""

        session = await self._get_session()
        # reasoner 模型不支持 system role，合并到 user
        if "reasoner" in self.model:
            messages = [
                {"role": "user", "content": f"{system_prompt}\n\n---\n\n{user_prompt}"},
            ]
        else:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": False,
        }
        # reasoner 不支持 temperature
        if "reasoner" not in self.model:
            payload["temperature"] = temperature

        try:
            async with session.post(f"{self.BASE_URL}/chat/completions", json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"DeepSeek API 错误 {resp.status}: {text[:200]}")
                    return ""
                data = await resp.json()
                msg = data.get("choices", [{}])[0].get("message", {})
                content = msg.get("content", "")
                # reasoner 模型有思考过程
                reasoning = msg.get("reasoning_content", "")
                usage = data.get("usage", {})
                thinking_tokens = usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0)
                logger.debug(
                    f"DeepSeek V3.2 调用: {usage.get('prompt_tokens',0)}+{usage.get('completion_tokens',0)} tokens"
                    f" (思考: {thinking_tokens})"
                )
                if reasoning:
                    logger.debug(f"DeepSeek 思考过程: {reasoning[:200]}...")
                return content

        except Exception as e:
            logger.error(f"DeepSeek 调用失败: {e}")
            return ""

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
