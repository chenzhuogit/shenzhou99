"""认证模块测试"""
import pytest
from src.exchange.auth import OKXAuth


class TestOKXAuth:

    @pytest.fixture
    def auth(self):
        return OKXAuth(
            api_key="test_key",
            secret_key="test_secret",
            passphrase="test_pass",
            simulated=True,
        )

    def test_headers_contain_required_fields(self, auth):
        """请求头应包含必要字段"""
        headers = auth.get_headers("GET", "/api/v5/account/balance")
        assert "OK-ACCESS-KEY" in headers
        assert "OK-ACCESS-SIGN" in headers
        assert "OK-ACCESS-TIMESTAMP" in headers
        assert "OK-ACCESS-PASSPHRASE" in headers
        assert headers["OK-ACCESS-KEY"] == "test_key"
        assert headers["OK-ACCESS-PASSPHRASE"] == "test_pass"

    def test_simulated_header(self, auth):
        """模拟盘应包含 x-simulated-trading 头"""
        headers = auth.get_headers("GET", "/api/v5/account/balance")
        assert headers.get("x-simulated-trading") == "1"

    def test_live_no_simulated_header(self):
        """实盘不应包含模拟头"""
        auth = OKXAuth("key", "secret", "pass", simulated=False)
        headers = auth.get_headers("GET", "/test")
        assert "x-simulated-trading" not in headers

    def test_ws_login_params(self, auth):
        """WebSocket 登录参数格式"""
        params = auth.get_ws_login_params()
        assert params["op"] == "login"
        assert len(params["args"]) == 1
        arg = params["args"][0]
        assert "apiKey" in arg
        assert "passphrase" in arg
        assert "timestamp" in arg
        assert "sign" in arg

    def test_signature_changes_with_path(self, auth):
        """不同路径应产生不同签名"""
        h1 = auth.get_headers("GET", "/api/v5/account/balance")
        h2 = auth.get_headers("GET", "/api/v5/trade/order")
        assert h1["OK-ACCESS-SIGN"] != h2["OK-ACCESS-SIGN"]

    def test_signature_changes_with_body(self, auth):
        """不同请求体应产生不同签名"""
        h1 = auth.get_headers("POST", "/api/v5/trade/order", '{"instId":"BTC"}')
        h2 = auth.get_headers("POST", "/api/v5/trade/order", '{"instId":"ETH"}')
        assert h1["OK-ACCESS-SIGN"] != h2["OK-ACCESS-SIGN"]
