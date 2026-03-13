"""神州99 Web API 服务器 — 数据全部来自 MySQL"""
import os
import sys
import json
import asyncio
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
    """前端仪表盘一次拉取所有数据"""
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

    return json_response({
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
    })


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

        # 已平仓时，计算最终盈亏
        if pos_status == "closed" and cur_px > 0 and avg_px > 0:
            ct_val = 0.01 if "BTC" in r["inst_id"] else 0.1 if "ETH" in r["inst_id"] else 1
            sz = float(r.get("size", 0) or 0)
            if r.get("pos_side") == "long":
                real_pnl = (cur_px - avg_px) * sz * ct_val
            else:
                real_pnl = (avg_px - cur_px) * sz * ct_val

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
            "fee": float(r.get("fee", 0) or 0),
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

    return json_response(records)


# ═══ 熔断状态 ═══
async def api_circuit_breaker_status(request):
    """获取熔断状态"""
    from src.data.dao import ConfigDAO
    is_paused = (await ConfigDAO.get("circuit_breaker_paused")) == "1"
    reason = (await ConfigDAO.get("circuit_breaker_reason")) or ""
    daily_pnl_str = (await ConfigDAO.get("daily_pnl")) or "0"
    daily_pnl = float(daily_pnl_str)

    return json_response({
        "is_paused": is_paused,
        "reason": reason,
        "daily_pnl": daily_pnl,
        "circuit_limit": 50.0,
    })


async def api_circuit_breaker_resume(request):
    """人工解除熔断"""
    from src.data.dao import ConfigDAO
    await ConfigDAO.set("circuit_breaker_resume", "1")
    await SystemLogDAO.log("WARN", "risk", "⚠️ 人工解除熔断（前端操作）")
    return json_response({"ok": True, "message": "熔断已解除，引擎将在下个周期恢复交易"})


# ═══ DeepSeek 日志 ═══
async def api_deepseek_logs(request):
    """DeepSeek 策略引擎专属日志"""
    limit = int(request.query.get("limit", 30))
    logs = await Database.fetch_all(
        "SELECT * FROM system_logs WHERE module='deepseek' ORDER BY id DESC LIMIT %s",
        (limit,)
    )
    # 同时获取 DeepSeek 模块状态
    ds_module = await Database.fetch_one(
        "SELECT * FROM module_status WHERE module_name='deepseek_advisor'"
    )
    return json_response({
        "logs": logs,
        "status": ds_module,
    })


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


# ═══ 健康检查 ═══
async def api_health(request):
    return json_response({"status": "ok", "system": "shenzhou99", "db": "mysql"})


# ═══ 应用生命周期 ═══
async def on_startup(app):
    await Database.init_pool()

async def on_cleanup(app):
    await Database.close_pool()


# ═══ CORS 中间件 ═══
@web.middleware
async def cors_middleware(request, handler):
    if request.method == "OPTIONS":
        resp = web.Response()
    else:
        resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


# ═══ 路由 ═══
app = web.Application(middlewares=[cors_middleware])

app.router.add_get("/", index)
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
app.router.add_get("/api/snapshots", api_snapshots)

app.router.add_static("/static/", WEB_DIR)

app.on_startup.append(on_startup)
app.on_cleanup.append(on_cleanup)

if __name__ == "__main__":
    print(f"\n🚀 神州99 控制台启动: http://0.0.0.0:{PORT}")
    print(f"   数据库: MySQL shenzhou99\n")
    web.run_app(app, host="0.0.0.0", port=PORT)
