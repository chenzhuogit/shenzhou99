"""神州99 Web API 服务器 — 数据全部来自 MySQL"""
import os
import sys
import json
import asyncio
import hashlib
import secrets
import time
from datetime import datetime, date
from decimal import Decimal

from aiohttp import web

# 加项目根目录到 path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.data.database import Database
from src.data.dao import (
    AccountDAO, PositionDAO, OrderDAO, TradeDAO,
    SignalDAO, RiskLogDAO, SystemLogDAO, ModuleStatusDAO,
    ConfigDAO, StrategyPerfDAO, FundingRateDAO,
)

PORT = 8899
WEB_DIR = os.path.dirname(os.path.abspath(__file__))

# ═══ 认证 ═══
AUTH_USERS = {
    "admin": hashlib.sha256("Zhuo198919@".encode()).hexdigest(),
}
# token -> {user, expires}  持久化到文件，重启不丢失
_tokens: dict[str, dict] = {}
TOKEN_EXPIRE = 7 * 24 * 3600  # 7天有效
_TOKEN_FILE = os.path.join(os.path.dirname(__file__), "..", "logs", "tokens.json")

def _load_tokens():
    global _tokens
    try:
        if os.path.exists(_TOKEN_FILE):
            with open(_TOKEN_FILE, "r") as f:
                _tokens = json.loads(f.read())
            # 清理过期 token
            now = time.time()
            _tokens = {k: v for k, v in _tokens.items() if v.get("expires", 0) > now}
    except Exception:
        _tokens = {}

def _save_tokens():
    try:
        os.makedirs(os.path.dirname(_TOKEN_FILE), exist_ok=True)
        with open(_TOKEN_FILE, "w") as f:
            f.write(json.dumps(_tokens))
    except Exception:
        pass

_load_tokens()

# 不需要认证的路径（页面本身公开，API 需要认证）
PUBLIC_PATHS = {"/", "/login", "/health", "/api/login"}


def json_serial(obj):
    """JSON 序列化辅助"""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Type {type(obj)} not serializable")


def json_response(data, status=200):
    return web.Response(
        text=json.dumps(data, default=json_serial, ensure_ascii=False),
        content_type="application/json", status=status,
    )


# ═══ 页面 ═══
async def index(request):
    return web.FileResponse(os.path.join(WEB_DIR, "index.html"))


# ═══ 仪表盘聚合接口（一次拉全部） ═══
def _read_ticker_cache() -> dict:
    """读取引擎写的实时行情缓存"""
    try:
        import json as _json
        cache_path = os.path.join(os.path.dirname(__file__), "../logs/ticker_cache.json")
        with open(cache_path, "r") as f:
            return _json.load(f)
    except Exception:
        return {}


def _enrich_positions_with_ticker(positions: list, ticker_cache: dict) -> list:
    """用最新 ticker 价格覆盖持仓的 current_price 并重算盈亏"""
    ct_val_map = {
        "BTC-USDT-SWAP": 0.01, "ETH-USDT-SWAP": 0.1, "SOL-USDT-SWAP": 1.0,
        "DOGE-USDT-SWAP": 1000.0, "ATOM-USDT-SWAP": 1.0, "HMSTR-USDT-SWAP": 100.0,
        "BCH-USDT-SWAP": 0.1, "W-USDT-SWAP": 1.0, "STRK-USDT-SWAP": 1.0,
        "TON-USDT-SWAP": 1.0, "SEI-USDT-SWAP": 10.0, "MANA-USDT-SWAP": 10.0,
        "ADA-USDT-SWAP": 100.0, "OP-USDT-SWAP": 1.0, "INJ-USDT-SWAP": 0.1,
    }
    for p in positions:
        inst_id = p.get("inst_id", "")
        if inst_id in ticker_cache:
            ticker_px = ticker_cache[inst_id].get("last", 0)
            if ticker_px > 0:
                p["current_price"] = ticker_px
                avg_px = float(p.get("avg_price", 0) or 0)
                sz = float(p.get("size", 0) or 0)
                ct_val = ct_val_map.get(inst_id, 1.0)
                lever = float(p.get("leverage", 1) or 1)
                if avg_px > 0 and sz > 0:
                    if p.get("pos_side") == "long":
                        p["unrealized_pnl"] = (ticker_px - avg_px) * sz * ct_val
                        p["pnl_ratio"] = (ticker_px - avg_px) / avg_px * lever
                    elif p.get("pos_side") == "short":
                        p["unrealized_pnl"] = (avg_px - ticker_px) * sz * ct_val
                        p["pnl_ratio"] = (avg_px - ticker_px) / avg_px * lever
    return positions


async def api_dashboard(request):
    """前端仪表盘一次拉取所有数据（3秒缓存）"""
    cached = cache_get("dashboard")
    if cached:
        return json_response(cached)

    (
        assets, positions, today_stats, win_loss, cum_pnl,
        snapshot, modules, recent_orders, recent_logs, risk_logs,
    ) = await asyncio.gather(
        AccountDAO.get_all_assets(),
        PositionDAO.get_open_positions(),
        OrderDAO.get_today_stats(),
        TradeDAO.get_win_loss_stats(),
        TradeDAO.get_cumulative_pnl(),
        AccountDAO.get_latest_snapshot(),
        ModuleStatusDAO.get_all(),
        OrderDAO.get_recent_orders(30),
        SystemLogDAO.get_recent(60),
        RiskLogDAO.get_recent(20),
    )

    # 用最新 ticker 价格覆盖 DB 中的旧价格
    ticker_cache = _read_ticker_cache()
    positions = _enrich_positions_with_ticker(positions, ticker_cache)

    unrealized_pnl = sum(float(p.get("unrealized_pnl", 0) or 0) for p in positions)
    total_asset = sum(float(a.get("usd_value", 0) or 0) for a in assets)

    def safe_int(v): return int(v) if v is not None else 0
    def safe_float(v): return float(v) if v is not None else 0.0

    today_pnl = safe_float(today_stats.get("total_pnl")) if today_stats else 0  # 净利润（含手续费）
    today_gross = safe_float(today_stats.get("gross_pnl")) if today_stats else 0
    today_fee = safe_float(today_stats.get("total_fee")) if today_stats else 0
    wins = safe_int(win_loss.get("wins")) if win_loss else 0
    losses = safe_int(win_loss.get("losses")) if win_loss else 0
    total_trades_all = wins + losses
    win_rate = (wins / total_trades_all * 100) if total_trades_all > 0 else 0

    result = {
        "stats": {
            "total_asset": total_asset,
            "position_count": len(positions),
            "max_positions": 5,
            "today_trades": safe_int(today_stats.get("total")) if today_stats else 0,
            "today_buys": safe_int(today_stats.get("buys")) if today_stats else 0,
            "today_sells": safe_int(today_stats.get("sells")) if today_stats else 0,
            "win_rate": round(win_rate, 1),
            "wins": wins,
            "losses": losses,
            "unrealized_pnl": unrealized_pnl,
            "today_pnl": today_pnl,
            "today_gross_pnl": today_gross,
            "today_fee": today_fee,
            "cumulative_pnl": float(cum_pnl),
            "max_drawdown": float(snapshot.get("drawdown", 0)) * 100 if snapshot else 0,
            "equity": float(snapshot.get("total_equity", 0)) if snapshot else total_asset,
            "max_equity": float(snapshot.get("max_equity", 0)) if snapshot else total_asset,
        },
        "positions": positions,
        "assets": assets,
        "orders": recent_orders,
        "modules": modules,
        "logs": recent_logs,
        "risk_logs": risk_logs,
    }
    cache_set("dashboard", result, ttl=3)
    return json_response(result)


# ═══ 资产 ═══
async def api_assets(request):
    assets = await AccountDAO.get_all_assets()
    return json_response(assets)


# ═══ 持仓 ═══
async def api_positions(request):
    positions = await PositionDAO.get_open_positions()
    ticker_cache = _read_ticker_cache()
    positions = _enrich_positions_with_ticker(positions, ticker_cache)
    return json_response(positions)


async def api_close_position(request):
    data = await request.json()
    inst_id = data.get("inst_id")
    pos_side = data.get("pos_side", "long")
    await PositionDAO.close_position(inst_id, pos_side)
    await SystemLogDAO.log("INFO", "web", f"手动平仓: {inst_id} {pos_side}")
    return json_response({"ok": True})


async def api_close_all(request):
    await PositionDAO.close_all()
    await SystemLogDAO.log("WARN", "web", "手动全部平仓")
    return json_response({"ok": True})


# ═══ 订单 ═══
async def api_orders(request):
    limit = int(request.query.get("limit", 50))
    orders = await OrderDAO.get_recent_orders(limit)
    return json_response(orders)


async def api_today_orders(request):
    orders = await OrderDAO.get_today_orders()
    return json_response(orders)


# ═══ 成交 ═══
async def api_trades(request):
    limit = int(request.query.get("limit", 50))
    trades = await TradeDAO.get_recent_trades(limit)
    return json_response(trades)


# ═══ 交易记录详情（历史+当前，以持仓为主表） ═══
async def api_trade_records(request):
    """交易记录详情：所有持仓（开仓中+已平仓），关联订单和信号"""
    limit = int(request.query.get("limit", 50))
    status_filter = request.query.get("status", "")  # open / closed / 空=全部

    cache_key = f"trade_records:{limit}:{status_filter}"
    cached = cache_get(cache_key)
    if cached:
        return json_response(cached)

    where = ""
    params = []
    if status_filter:
        where = "WHERE p.status = %s"
        params.append(status_filter)

    rows = await Database.fetch_all(f"""
        SELECT
            p.id, p.inst_id, p.inst_type, p.pos_side,
            p.size, p.avg_price, p.current_price, p.mark_price,
            p.liquidation_price, p.margin, p.margin_mode, p.leverage,
            p.unrealized_pnl, p.realized_pnl, p.pnl_ratio,
            p.stop_loss AS pos_sl, p.take_profit AS pos_tp,
            p.strategy_name AS pos_strategy, p.status AS pos_status,
            p.opened_at, p.closed_at, p.updated_at,
            o.ord_id, o.side, o.filled_size, o.filled_price,
            o.fee, o.pnl AS ord_pnl,
            o.strategy_name, o.signal_reason, o.status AS ord_status,
            o.created_at AS order_time,
            s.stop_loss, s.take_profit, s.reward_risk_ratio,
            s.confidence, s.reason AS signal_detail,
            s.risk_check_passed, s.risk_check_reason
        FROM positions p
        LEFT JOIN orders o ON o.inst_id = p.inst_id
            AND o.pos_side = p.pos_side
            AND o.side = CASE WHEN p.pos_side='long' THEN 'buy' ELSE 'sell' END
            AND ABS(TIMESTAMPDIFF(SECOND, o.created_at, p.opened_at)) < 30
        LEFT JOIN signals s ON s.inst_id = p.inst_id
            AND ABS(TIMESTAMPDIFF(SECOND, s.created_at, p.opened_at)) < 30
        {where}
        ORDER BY p.opened_at DESC
        LIMIT %s
    """, (*params, limit))

    # ── 已平仓盈亏：直接用 positions.realized_pnl（已修正为净盈亏含手续费） ──
    # 不再逐条子查询 trades 表（15次×343ms = 5秒延迟的根因）
    closed_pnl_map = {}

    records = []
    seen_ids = set()
    for r in rows:
        pid = r["id"]
        if pid in seen_ids:
            continue
        seen_ids.add(pid)

        avg_px = float(r.get("avg_price", 0) or 0)
        cur_px = float(r.get("current_price", 0) or 0)
        pnl_ratio = float(r.get("pnl_ratio", 0) or 0) * 100
        unr_pnl = float(r.get("unrealized_pnl", 0) or 0)
        real_pnl = float(r.get("realized_pnl", 0) or 0)
        pos_status = r.get("pos_status", "")
        total_fee = float(r.get("fee", 0) or 0)

        # 已平仓：用 trades 表的真实数据（含手续费）
        if pos_status == "closed":
            key = (r["inst_id"], r.get("pos_side", ""), str(r.get("opened_at", "")))
            if key in closed_pnl_map:
                td = closed_pnl_map[key]
                real_pnl = td["net"]  # pnl + fee = 真实净盈亏
                total_fee = td["fee"]
                # 重算收益率
                margin = float(r.get("margin", 0) or 0)
                if margin > 0:
                    pnl_ratio = real_pnl / margin * 100

        # 持仓时长
        opened = r.get("opened_at")
        closed = r.get("closed_at")
        duration = ""
        if opened:
            end = closed or datetime.now()
            delta = end - opened
            mins = int(delta.total_seconds() / 60)
            if mins < 60:
                duration = f"{mins}分钟"
            elif mins < 1440:
                duration = f"{mins//60}小时{mins%60}分"
            else:
                duration = f"{mins//1440}天{(mins%1440)//60}小时"

        record = {
            "id": pid,
            "inst_id": r["inst_id"],
            "inst_type": r.get("inst_type", ""),
            "pos_side": r.get("pos_side", ""),
            "side": r.get("side", ""),
            "size": float(r.get("size", 0) or 0),
            "filled_size": float(r.get("filled_size", 0) or 0),
            "avg_price": avg_px,
            "filled_price": float(r.get("filled_price", 0) or 0),
            "current_price": cur_px,
            "mark_price": float(r.get("mark_price", 0) or 0),
            "fee": total_fee,
            "strategy": r.get("strategy_name") or r.get("pos_strategy") or "",
            "signal_reason": r.get("signal_reason", "") or "",
            "signal_detail": r.get("signal_detail", "") or "",
            "stop_loss": float(r.get("stop_loss") or r.get("pos_sl") or 0),
            "take_profit": float(r.get("take_profit") or r.get("pos_tp") or 0),
            "reward_risk_ratio": float(r.get("reward_risk_ratio", 0) or 0),
            "confidence": float(r.get("confidence", 0) or 0),
            "risk_passed": bool(r.get("risk_check_passed")),
            "risk_reason": r.get("risk_check_reason") or "",
            "unrealized_pnl": unr_pnl,
            "realized_pnl": real_pnl,
            "pnl_pct": round(pnl_ratio, 2),
            "margin": float(r.get("margin", 0) or 0),
            "leverage": int(float(r.get("leverage", 1) or 1)),
            "liquidation_price": float(r.get("liquidation_price", 0) or 0),
            "pos_status": pos_status,
            "ord_status": r.get("ord_status", ""),
            "opened_at": r.get("opened_at"),
            "closed_at": r.get("closed_at"),
            "duration": duration,
        }
        records.append(record)

    cache_set(cache_key, records, ttl=5)
    return json_response(records)


# ═══ 熔断状态 ═══
async def api_circuit_breaker_status(request):
    """获取熔断状态（5秒缓存）"""
    cached = cache_get("circuit_breaker")
    if cached:
        return json_response(cached)
    from src.data.dao import ConfigDAO
    is_paused = (await ConfigDAO.get("circuit_breaker_paused")) == "1"
    reason = (await ConfigDAO.get("circuit_breaker_reason")) or ""
    daily_pnl_str = (await ConfigDAO.get("daily_pnl")) or "0"
    daily_pnl = float(daily_pnl_str)

    result = {
        "is_paused": is_paused,
        "reason": reason,
        "daily_pnl": daily_pnl,
        "circuit_limit": 50.0,
    }
    cache_set("circuit_breaker", result, ttl=5)
    return json_response(result)


async def api_circuit_breaker_resume(request):
    """人工解除熔断"""
    from src.data.dao import ConfigDAO
    await ConfigDAO.set("circuit_breaker_resume", "1")
    await SystemLogDAO.log("WARN", "risk", "⚠️ 人工解除熔断（前端操作）")
    return json_response({"ok": True, "message": "熔断已解除，引擎将在下个周期恢复交易"})


# ═══ DeepSeek 日志 ═══
async def api_deepseek_logs(request):
    """DeepSeek 策略引擎专属日志（5秒缓存）"""
    limit = int(request.query.get("limit", 30))
    cache_key = f"deepseek:{limit}"
    cached = cache_get(cache_key)
    if cached:
        return json_response(cached)
    logs = await Database.fetch_all(
        "SELECT * FROM system_logs WHERE module='deepseek' ORDER BY id DESC LIMIT %s",
        (limit,)
    )
    # 同时获取 DeepSeek 模块状态
    ds_module = await Database.fetch_one(
        "SELECT * FROM module_status WHERE module_name='deepseek_advisor'"
    )
    result = {"logs": logs, "status": ds_module}
    cache_set(cache_key, result, ttl=5)
    return json_response(result)


# ═══ 日志 ═══
async def api_logs(request):
    limit = int(request.query.get("limit", 100))
    level = request.query.get("level")
    module = request.query.get("module")
    logs = await SystemLogDAO.get_recent(limit, level, module)
    return json_response(logs)


async def api_risk_logs(request):
    limit = int(request.query.get("limit", 50))
    logs = await RiskLogDAO.get_recent(limit)
    return json_response(logs)


# ═══ 模块 ═══
async def api_modules(request):
    modules = await ModuleStatusDAO.get_all()
    return json_response(modules)


# ═══ 配置 ═══
async def api_config(request):
    configs = await ConfigDAO.get_all()
    return json_response(configs)


async def api_config_set(request):
    data = await request.json()
    key = data.get("key")
    value = data.get("value")
    if not key or value is None:
        return json_response({"error": "key and value required"}, 400)
    await ConfigDAO.set(key, str(value))
    return json_response({"ok": True})


# ═══ 策略绩效 ═══
async def api_strategy_perf(request):
    strategy = request.query.get("strategy", "trend_following")
    limit = int(request.query.get("limit", 30))
    perf = await StrategyPerfDAO.get_by_strategy(strategy, limit)
    return json_response(perf)


# ═══ 快照 ═══
async def api_snapshots(request):
    limit = int(request.query.get("limit", 100))
    snapshots = await AccountDAO.get_snapshots(limit)
    return json_response(snapshots)


# ═══ 自动优化日志 ═══
async def api_optimize_logs(request):
    """返回优化实验日志 + 当前最优参数"""
    result = {"best_params": {}, "experiments": [], "log_tail": ""}

    # 最优参数
    params_file = os.path.join(os.path.dirname(__file__), "../src/backtest/best_params.json")
    try:
        with open(params_file, "r") as f:
            result["best_params"] = json.load(f)
    except Exception:
        pass

    # 实验记录 (results.tsv)
    tsv_file = os.path.join(os.path.dirname(__file__), "../src/backtest/results.tsv")
    try:
        with open(tsv_file, "r") as f:
            lines = f.readlines()
        if len(lines) > 1:
            headers = lines[0].strip().split("\t")
            for line in lines[-30:]:  # 最近30条
                cols = line.strip().split("\t")
                if len(cols) >= len(headers):
                    result["experiments"].append(dict(zip(headers, cols)))
    except Exception:
        pass

    # 优化日志尾部
    log_file = os.path.join(os.path.dirname(__file__), "../logs/optimize.log")
    try:
        with open(log_file, "r") as f:
            all_lines = f.readlines()
            result["log_tail"] = "".join(all_lines[-50:])
    except Exception:
        pass

    return json_response(result)


# ═══ xAI 情绪分析 ═══
async def api_xai_sentiment(request):
    """返回 xAI 情绪分析结果（使用缓存）"""
    try:
        from src.advisor.xai_sentiment import get_analyzer
        analyzer = get_analyzer()
        if not analyzer.available:
            return json_response({"error": "xAI 未配置", "data": {}})

        # ?refresh=1 强制刷新（消耗 token）
        if request.query.get("refresh") == "1":
            summary = await analyzer.force_refresh()
        else:
            summary = await analyzer.get_summary()

        return json_response(summary)
    except Exception as e:
        return json_response({"error": str(e), "data": {}})


# ═══ 登录 ═══
async def api_login(request):
    try:
        body = await request.json()
    except Exception:
        return json_response({"ok": False, "error": "请求格式错误"}, status=400)

    username = body.get("username", "").strip()
    password = body.get("password", "")
    pwd_hash = hashlib.sha256(password.encode()).hexdigest()

    if username in AUTH_USERS and AUTH_USERS[username] == pwd_hash:
        token = secrets.token_hex(32)
        _tokens[token] = {"user": username, "expires": time.time() + TOKEN_EXPIRE}
        _save_tokens()
        return json_response({"ok": True, "token": token, "user": username})

    return json_response({"ok": False, "error": "用户名或密码错误"}, status=401)


async def login_page(request):
    return web.FileResponse(os.path.join(WEB_DIR, "login.html"))


# ═══ 健康检查 ═══
async def api_health(request):
    return json_response({"status": "ok", "system": "shenzhou99", "db": "mysql"})


# ═══ 应用生命周期 ═══
# ═══ API 缓存层（减少 DB 往返，每次往返 ~160ms） ═══
_api_cache: dict[str, tuple] = {}  # key → (data, expire_time)

def cache_get(key: str):
    """获取缓存，过期返回 None"""
    if key in _api_cache:
        data, expires = _api_cache[key]
        if time.time() < expires:
            return data
        del _api_cache[key]
    return None

def cache_set(key: str, data, ttl: float = 3.0):
    """设置缓存，默认 3 秒 TTL"""
    _api_cache[key] = (data, time.time() + ttl)


async def on_startup(app):
    await Database.init_pool(min_size=3, max_size=15)

async def on_cleanup(app):
    await Database.close_pool()


# ═══ 认证检查 ═══
def check_token(request) -> bool:
    """检查请求是否带有效 token"""
    # Header: Authorization: Bearer <token>
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
    else:
        # Query: ?token=xxx
        token = request.query.get("token", "")

    if not token:
        return False

    info = _tokens.get(token)
    if not info:
        return False

    if time.time() > info["expires"]:
        del _tokens[token]
        return False

    return True


# ═══ CORS + Auth 中间件 ═══
@web.middleware
async def cors_middleware(request, handler):
    if request.method == "OPTIONS":
        resp = web.Response()
    else:
        resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return resp


@web.middleware
async def auth_middleware(request, handler):
    path = request.path

    # 公开路径不需要认证
    if path in PUBLIC_PATHS:
        return await handler(request)

    # 静态资源不需要认证（login.html 的 CSS/JS）
    if path.startswith("/static/"):
        return await handler(request)

    # 检查 token
    if not check_token(request):
        # API 请求返回 401
        if path.startswith("/api/"):
            return web.json_response({"error": "未登录"}, status=401)
        # 页面请求重定向到登录
        raise web.HTTPFound("/login")

    return await handler(request)


# ═══ 路由 ═══
app = web.Application(middlewares=[cors_middleware, auth_middleware])

app.router.add_get("/", index)
app.router.add_get("/login", login_page)
app.router.add_post("/api/login", api_login)
app.router.add_get("/health", api_health)

# 聚合接口
app.router.add_get("/api/dashboard", api_dashboard)

# 分项接口
app.router.add_get("/api/assets", api_assets)
app.router.add_get("/api/positions", api_positions)
app.router.add_post("/api/positions/close", api_close_position)
app.router.add_post("/api/positions/close-all", api_close_all)
app.router.add_get("/api/orders", api_orders)
app.router.add_get("/api/orders/today", api_today_orders)
app.router.add_get("/api/trades", api_trades)
app.router.add_get("/api/trade-records", api_trade_records)
app.router.add_get("/api/deepseek", api_deepseek_logs)
app.router.add_get("/api/circuit-breaker", api_circuit_breaker_status)
app.router.add_post("/api/circuit-breaker/resume", api_circuit_breaker_resume)
app.router.add_get("/api/logs", api_logs)
app.router.add_get("/api/logs/risk", api_risk_logs)
app.router.add_get("/api/modules", api_modules)
app.router.add_get("/api/config", api_config)
app.router.add_post("/api/config", api_config_set)
app.router.add_get("/api/performance", api_strategy_perf)
app.router.add_get("/api/optimize", api_optimize_logs)
app.router.add_get("/api/sentiment", api_xai_sentiment)

# ═══ 交易决策权限规范 ═══
# 🚫 严禁 Claude（AI助手）自主执行任何交易操作，包括但不限于：
#    - 手动调用 place_order / close_position
#    - 通过脚本或代码直接下单/平仓
#    - 修改持仓/止损/止盈
# ✅ 只有以下两个主体可以执行交易：
#    1. 用户（hu la）— 通过前端手动操作
#    2. 策略引擎 — 通过 _check_signal / _execute_deepseek_signal 自动执行
# Claude 的职责：写代码、优化策略、分析数据、修 Bug，不碰交易按钮。
app.router.add_get("/api/snapshots", api_snapshots)

app.router.add_static("/static/", WEB_DIR)

app.on_startup.append(on_startup)
app.on_cleanup.append(on_cleanup)

if __name__ == "__main__":
    print(f"\n🚀 神州99 控制台启动: http://0.0.0.0:{PORT}")
    print(f"   数据库: MySQL shenzhou99\n")
    web.run_app(app, host="0.0.0.0", port=PORT)
