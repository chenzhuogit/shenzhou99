"""OKX API 签名认证模块"""
import base64
import hashlib
import hmac
import time
from typing import Optional


class OKXAuth:
    """OKX API 认证"""

    def __init__(self, api_key: str, secret_key: str, passphrase: str, simulated: bool = True):
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.simulated = simulated

    def _get_timestamp(self) -> str:
        """获取 ISO 格式时间戳"""
        return time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())

    def _sign(self, timestamp: str, method: str, request_path: str, body: str = "") -> str:
        """
        生成签名
        签名 = Base64(HMAC-SHA256(timestamp + method + requestPath + body, secretKey))
        """
        message = timestamp + method.upper() + request_path + body
        mac = hmac.new(
            self.secret_key.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        )
        return base64.b64encode(mac.digest()).decode('utf-8')

    def get_headers(self, method: str, request_path: str, body: str = "") -> dict:
        """
        获取带签名的请求头
        
        Args:
            method: HTTP 方法 (GET/POST)
            request_path: 请求路径 (如 /api/v5/trade/order)
            body: 请求体 JSON 字符串
            
        Returns:
            签名后的请求头字典
        """
        timestamp = self._get_timestamp()
        sign = self._sign(timestamp, method, request_path, body)

        headers = {
            'OK-ACCESS-KEY': self.api_key,
            'OK-ACCESS-SIGN': sign,
            'OK-ACCESS-TIMESTAMP': timestamp,
            'OK-ACCESS-PASSPHRASE': self.passphrase,
            'Content-Type': 'application/json',
        }

        if self.simulated:
            headers['x-simulated-trading'] = '1'

        return headers

    def get_ws_login_params(self) -> dict:
        """
        获取 WebSocket 登录参数
        
        Returns:
            WebSocket 登录请求体
        """
        timestamp = str(int(time.time()))
        sign = self._sign(timestamp, 'GET', '/users/self/verify')

        return {
            "op": "login",
            "args": [{
                "apiKey": self.api_key,
                "passphrase": self.passphrase,
                "timestamp": timestamp,
                "sign": sign
            }]
        }
