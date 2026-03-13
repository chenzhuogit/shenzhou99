"""OKX REST API 客户端"""
import json
import asyncio
from typing import Any, Optional

import aiohttp
from loguru import logger

from .auth import OKXAuth
from .rate_limiter import RateLimiter


class OKXClient:
    """OKX REST API 客户端"""

    def __init__(self, auth: OKXAuth, simulated: bool = True):
        self.auth = auth
        self.simulated = simulated
        self.base_url = "https://www.okx.com"
        self.rate_limiter = RateLimiter()
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(
        self,
        method: str,
        path: str,
        body: Optional[dict] = None,
        rate_limit_key: str = "query"
    ) -> dict:
        """
        发送 API 请求
        
        Args:
            method: HTTP 方法
            path: API 路径
            body: 请求体
            rate_limit_key: 限速规则名
            
        Returns:
            API 响应
        """
        await self.rate_limiter.acquire(rate_limit_key)

        body_str = json.dumps(body) if body else ""
        headers = self.auth.get_headers(method, path, body_str)
        url = self.base_url + path

        session = await self._get_session()
        try:
            async with session.request(method, url, headers=headers, data=body_str or None) as resp:
                data = await resp.json()
                if data.get("code") != "0":
                    logger.error(f"API Error: {path} → {data}")
                return data
        except Exception as e:
            logger.error(f"Request failed: {method} {path} → {e}")
            raise

    # ─── 账户相关 ───

    async def get_balance(self, ccy: str = "") -> dict:
        """查看账户余额"""
        path = f"/api/v5/account/balance"
        if ccy:
            path += f"?ccy={ccy}"
        return await self._request("GET", path)

    async def get_positions(self, inst_type: str = "", inst_id: str = "") -> dict:
        """查看持仓信息"""
        params = []
        if inst_type:
            params.append(f"instType={inst_type}")
        if inst_id:
            params.append(f"instId={inst_id}")
        path = "/api/v5/account/positions"
        if params:
            path += "?" + "&".join(params)
        return await self._request("GET", path)

    async def set_leverage(self, inst_id: str, lever: str, mgn_mode: str, pos_side: str = "") -> dict:
        """设置杠杆倍数"""
        body = {
            "instId": inst_id,
            "lever": lever,
            "mgnMode": mgn_mode,  # cross | isolated
        }
        if pos_side:
            body["posSide"] = pos_side
        return await self._request("POST", "/api/v5/account/set-leverage", body, "query")

    async def get_account_config(self) -> dict:
        """查看账户配置"""
        return await self._request("GET", "/api/v5/account/config")

    async def set_position_mode(self, pos_mode: str) -> dict:
        """
        设置持仓模式
        pos_mode: long_short_mode (双向持仓) | net_mode (单向持仓)
        """
        return await self._request(
            "POST", "/api/v5/account/set-position-mode",
            {"posMode": pos_mode}, "query"
        )

    # ─── 交易相关 ───

    async def place_order(
        self,
        inst_id: str,
        td_mode: str,
        side: str,
        ord_type: str,
        sz: str,
        px: str = "",
        pos_side: str = "",
        cl_ord_id: str = "",
        reduce_only: bool = False,
    ) -> dict:
        """
        下单
        
        Args:
            inst_id: 产品ID (如 BTC-USDT-SWAP)
            td_mode: 交易模式 (cross/isolated/cash)
            side: 买卖方向 (buy/sell)
            ord_type: 订单类型 (market/limit/post_only/fok/ioc)
            sz: 数量
            px: 价格（限价单必填）
            pos_side: 持仓方向 (long/short，双向持仓必填)
            cl_ord_id: 客户自定义订单ID
            reduce_only: 只减仓
        """
        body: dict[str, Any] = {
            "instId": inst_id,
            "tdMode": td_mode,
            "side": side,
            "ordType": ord_type,
            "sz": sz,
        }
        if px:
            body["px"] = px
        if pos_side:
            body["posSide"] = pos_side
        if cl_ord_id:
            body["clOrdId"] = cl_ord_id
        if reduce_only:
            body["reduceOnly"] = True

        return await self._request("POST", "/api/v5/trade/order", body, "place_order")

    async def cancel_order(self, inst_id: str, ord_id: str = "", cl_ord_id: str = "") -> dict:
        """撤单"""
        body: dict[str, str] = {"instId": inst_id}
        if ord_id:
            body["ordId"] = ord_id
        if cl_ord_id:
            body["clOrdId"] = cl_ord_id
        return await self._request("POST", "/api/v5/trade/cancel-order", body, "cancel_order")

    async def amend_order(
        self, inst_id: str, ord_id: str = "", cl_ord_id: str = "",
        new_sz: str = "", new_px: str = ""
    ) -> dict:
        """改单"""
        body: dict[str, str] = {"instId": inst_id}
        if ord_id:
            body["ordId"] = ord_id
        if cl_ord_id:
            body["clOrdId"] = cl_ord_id
        if new_sz:
            body["newSz"] = new_sz
        if new_px:
            body["newPx"] = new_px
        return await self._request("POST", "/api/v5/trade/amend-order", body, "amend_order")

    async def close_position(self, inst_id: str, mgn_mode: str, pos_side: str = "") -> dict:
        """市价全平"""
        body: dict[str, str] = {
            "instId": inst_id,
            "mgnMode": mgn_mode,
        }
        if pos_side:
            body["posSide"] = pos_side
        return await self._request("POST", "/api/v5/trade/close-position", body, "place_order")

    async def place_algo_order(
        self,
        inst_id: str,
        td_mode: str,
        side: str,
        ord_type: str,
        sz: str,
        tp_trigger_px: str = "",
        tp_ord_px: str = "",
        sl_trigger_px: str = "",
        sl_ord_px: str = "",
        pos_side: str = "",
    ) -> dict:
        """
        策略委托下单（止盈止损）
        
        ord_type: conditional (止盈止损) | oco | trigger (计划委托)
        """
        body: dict[str, Any] = {
            "instId": inst_id,
            "tdMode": td_mode,
            "side": side,
            "ordType": ord_type,
            "sz": sz,
        }
        if tp_trigger_px:
            body["tpTriggerPx"] = tp_trigger_px
        if tp_ord_px:
            body["tpOrdPx"] = tp_ord_px
        if sl_trigger_px:
            body["slTriggerPx"] = sl_trigger_px
        if sl_ord_px:
            body["slOrdPx"] = sl_ord_px
        if pos_side:
            body["posSide"] = pos_side
        return await self._request("POST", "/api/v5/trade/order-algo", body, "place_order")

    async def cancel_algo_orders(self, orders: list[dict]) -> dict:
        """
        撤销策略委托
        orders: [{"algoId":"xxx","instId":"xxx"}, ...]
        """
        return await self._request("POST", "/api/v5/trade/cancel-algos", orders, "place_order")

    async def get_algo_orders(self, ord_type: str = "conditional", inst_id: str = "", 
                               inst_type: str = "SWAP") -> dict:
        """获取未触发策略委托"""
        params = [f"ordType={ord_type}", f"instType={inst_type}"]
        if inst_id:
            params.append(f"instId={inst_id}")
        return await self._request("GET", "/api/v5/trade/orders-algo-pending?" + "&".join(params))

    async def get_order_history(self, inst_id: str = "", inst_type: str = "SWAP",
                                limit: str = "10") -> dict:
        """获取最近成交订单历史"""
        params = [f"instType={inst_type}", f"limit={limit}"]
        if inst_id:
            params.append(f"instId={inst_id}")
        return await self._request("GET", "/api/v5/trade/orders-history-archive?" + "&".join(params))

    async def get_order(self, inst_id: str, ord_id: str = "", cl_ord_id: str = "") -> dict:
        """获取订单信息"""
        params = [f"instId={inst_id}"]
        if ord_id:
            params.append(f"ordId={ord_id}")
        if cl_ord_id:
            params.append(f"clOrdId={cl_ord_id}")
        path = "/api/v5/trade/order?" + "&".join(params)
        return await self._request("GET", path)

    async def get_pending_orders(self, inst_type: str = "", inst_id: str = "") -> dict:
        """获取未成交订单列表"""
        params = []
        if inst_type:
            params.append(f"instType={inst_type}")
        if inst_id:
            params.append(f"instId={inst_id}")
        path = "/api/v5/trade/orders-pending"
        if params:
            path += "?" + "&".join(params)
        return await self._request("GET", path)

    # ─── 行情相关 ───

    async def get_tickers(self, inst_type: str = "SPOT") -> dict:
        """获取所有 Ticker"""
        return await self._request("GET", f"/api/v5/market/tickers?instType={inst_type}")

    async def get_ticker(self, inst_id: str) -> dict:
        """获取单个 Ticker"""
        return await self._request("GET", f"/api/v5/market/ticker?instId={inst_id}")

    async def get_candles(
        self, inst_id: str, bar: str = "1H",
        after: str = "", before: str = "", limit: str = "100"
    ) -> dict:
        """获取K线数据"""
        params = [f"instId={inst_id}", f"bar={bar}", f"limit={limit}"]
        if after:
            params.append(f"after={after}")
        if before:
            params.append(f"before={before}")
        path = "/api/v5/market/candles?" + "&".join(params)
        return await self._request("GET", path)

    async def get_orderbook(self, inst_id: str, sz: str = "5") -> dict:
        """获取深度数据"""
        return await self._request("GET", f"/api/v5/market/books?instId={inst_id}&sz={sz}")

    async def get_instruments(self, inst_type: str = "SPOT") -> dict:
        """获取交易产品信息"""
        return await self._request("GET", f"/api/v5/public/instruments?instType={inst_type}")

    async def get_funding_rate(self, inst_id: str) -> dict:
        """获取资金费率"""
        return await self._request("GET", f"/api/v5/public/funding-rate?instId={inst_id}")

    async def get_mark_price(self, inst_id: str) -> dict:
        """获取标记价格"""
        return await self._request("GET", f"/api/v5/public/mark-price?instId={inst_id}&instType=SWAP")
