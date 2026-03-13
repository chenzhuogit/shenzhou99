"""OKX WebSocket 客户端"""
import asyncio
import json
import time
from typing import Any, Callable, Optional

import websockets
from loguru import logger

from .auth import OKXAuth


class OKXWebSocket:
    """
    OKX WebSocket 客户端
    
    支持公共频道（行情）和私有频道（账户/订单）
    自动重连 + 心跳保活 + 消息分发
    """

    def __init__(
        self,
        url: str,
        auth: Optional[OKXAuth] = None,
        name: str = "ws",
        ping_interval: float = 25.0,
        reconnect_delay: float = 1.0,
        max_reconnect_delay: float = 60.0,
    ):
        self.url = url
        self.auth = auth
        self.name = name
        self.ping_interval = ping_interval
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False
        self._logged_in = False
        self._subscriptions: list[dict] = []
        self._callbacks: dict[str, list[Callable]] = {}
        self._order_callbacks: dict[str, asyncio.Future] = {}
        self._tasks: list[asyncio.Task] = []

    def on(self, channel: str, callback: Callable):
        """注册频道回调"""
        if channel not in self._callbacks:
            self._callbacks[channel] = []
        self._callbacks[channel].append(callback)

    async def connect(self):
        """建立连接"""
        self._running = True
        self._tasks.append(asyncio.create_task(self._run()))

    async def close(self):
        """关闭连接"""
        self._running = False
        for task in self._tasks:
            task.cancel()
        if self._ws:
            await self._ws.close()

    async def _run(self):
        """主循环：连接 + 重连"""
        delay = self.reconnect_delay
        while self._running:
            try:
                ws = await websockets.connect(self.url, ping_interval=None)
                self._ws = ws
                delay = self.reconnect_delay
                logger.info(f"[{self.name}] Connected to {self.url}")

                if self.auth:
                    await self._login()

                if self._subscriptions:
                    await self._resubscribe()

                ping_task = asyncio.create_task(self._ping_loop())

                try:
                    async for message in ws:
                        if isinstance(message, bytes):
                            message = message.decode('utf-8')
                        await self._handle_message(message)
                finally:
                    ping_task.cancel()
                    await ws.close()

            except (ConnectionError, OSError) as e:
                logger.warning(f"[{self.name}] Connection lost: {e}")
            except Exception as e:
                logger.error(f"[{self.name}] Unexpected error: {e}")

            if self._running:
                logger.info(f"[{self.name}] Reconnecting in {delay}s...")
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.max_reconnect_delay)

    async def _login(self):
        """WebSocket 登录"""
        if not self.auth:
            return
        login_msg = self.auth.get_ws_login_params()
        await self._send(login_msg)

        # 等待登录响应
        try:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
            msg = raw.decode('utf-8') if isinstance(raw, bytes) else raw
            data = json.loads(msg)
            if data.get("event") == "login" and data.get("code") == "0":
                self._logged_in = True
                logger.info(f"[{self.name}] Login successful")
            else:
                logger.error(f"[{self.name}] Login failed: {data}")
        except asyncio.TimeoutError:
            logger.error(f"[{self.name}] Login timeout")

    async def _ping_loop(self):
        """心跳保活"""
        while self._running and self._ws:
            try:
                await asyncio.sleep(self.ping_interval)
                if self._ws_is_open():
                    await self._ws.send("ping")
            except Exception:
                break

    def _ws_is_open(self) -> bool:
        """检查 WS 是否打开（兼容 websockets v12-v16）"""
        if not self._ws:
            return False
        # v16+ uses .state, older uses .closed
        if hasattr(self._ws, 'closed'):
            return not self._ws.closed
        if hasattr(self._ws, 'state'):
            try:
                from websockets.protocol import State
                return self._ws.state == State.OPEN
            except ImportError:
                pass
        return True

    async def _send(self, data: dict):
        """发送消息"""
        if self._ws_is_open():
            await self._ws.send(json.dumps(data))

    async def subscribe(self, channels: list[dict]):
        """
        订阅频道
        
        Args:
            channels: 频道列表，如 [{"channel": "tickers", "instId": "BTC-USDT"}]
        """
        # 保存订阅列表用于重连恢复
        for ch in channels:
            if ch not in self._subscriptions:
                self._subscriptions.append(ch)

        msg = {"op": "subscribe", "args": channels}
        await self._send(msg)
        logger.info(f"[{self.name}] Subscribed: {channels}")

    async def unsubscribe(self, channels: list[dict]):
        """取消订阅"""
        for ch in channels:
            if ch in self._subscriptions:
                self._subscriptions.remove(ch)

        msg = {"op": "unsubscribe", "args": channels}
        await self._send(msg)

    async def _resubscribe(self):
        """重连后恢复订阅"""
        if self._subscriptions:
            msg = {"op": "subscribe", "args": self._subscriptions}
            await self._send(msg)
            logger.info(f"[{self.name}] Resubscribed {len(self._subscriptions)} channels")

    async def _handle_message(self, raw: str):
        """处理接收到的消息"""
        if raw == "pong":
            return

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"[{self.name}] Invalid JSON: {raw[:100]}")
            return

        # 事件消息（订阅确认/错误）
        if "event" in data:
            event = data["event"]
            if event == "error":
                logger.error(f"[{self.name}] WS Error: {data}")
            elif event == "subscribe":
                logger.debug(f"[{self.name}] Subscribe confirmed: {data.get('arg')}")
            return

        # 下单响应
        if "id" in data and "op" in data:
            order_id = data["id"]
            if order_id in self._order_callbacks:
                self._order_callbacks[order_id].set_result(data)
            return

        # 数据推送
        if "arg" in data and "data" in data:
            channel = data["arg"].get("channel", "")
            callbacks = self._callbacks.get(channel, [])
            for cb in callbacks:
                try:
                    if asyncio.iscoroutinefunction(cb):
                        await cb(data)
                    else:
                        cb(data)
                except Exception as e:
                    logger.error(f"[{self.name}] Callback error on {channel}: {e}")

    # ─── WebSocket 下单（低延迟） ───

    async def place_order(self, order_params: dict, timeout: float = 5.0) -> dict:
        """
        通过 WebSocket 下单（比 REST 低延迟）
        
        Args:
            order_params: 订单参数
            timeout: 等待响应超时
            
        Returns:
            下单响应
        """
        order_id = f"sz99_{int(time.time() * 1000)}"
        msg = {
            "id": order_id,
            "op": "order",
            "args": [order_params]
        }

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._order_callbacks[order_id] = future

        try:
            await self._send(msg)
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            logger.error(f"[{self.name}] Order timeout: {order_id}")
            return {"code": "-1", "msg": "timeout"}
        finally:
            self._order_callbacks.pop(order_id, None)

    async def cancel_order_ws(self, inst_id: str, ord_id: str, timeout: float = 5.0) -> dict:
        """通过 WebSocket 撤单"""
        cancel_id = f"sz99c_{int(time.time() * 1000)}"
        msg = {
            "id": cancel_id,
            "op": "cancel-order",
            "args": [{"instId": inst_id, "ordId": ord_id}]
        }

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._order_callbacks[cancel_id] = future

        try:
            await self._send(msg)
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            return {"code": "-1", "msg": "timeout"}
        finally:
            self._order_callbacks.pop(cancel_id, None)
