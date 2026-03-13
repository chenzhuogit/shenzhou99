#!/usr/bin/env python3
"""
OKX 数据校正同步脚本
从 OKX API 拉取真实数据 → 计算指标 → 更新 MySQL → 前端实时显示

用法:
    python3 scripts/sync.py                    # 全量同步
    python3 scripts/sync.py --module account   # 仅同步账户
    python3 scripts/sync.py --module positions # 仅同步持仓
    python3 scripts/sync.py --module trades    # 仅同步成交
    python3 scripts/sync.py --module all       # 全量同步
"""
import os
import sys
import asyncio
import argparse
from datetime import datetime, timezone, timedelta
from decimal import Decimal

# 项目根目录
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from loguru import logger
from src.exchange.okx_client import OKXClient
from src.exchange.auth import OKXAuth
from src.data.database import Database
from src.data.dao import AccountDAO, PositionDAO, TradeDAO, OrderDAO

# ctVal 映射
CT_VAL = {
    "ETH-USDT-SWAP":0.1,"BTC-USDT-SWAP":0.01,"SOL-USDT-SWAP":1,"TRUMP-USDT-SWAP":0.1,
    "DOGE-USDT-SWAP":1000,"RIVER-USDT-SWAP":0.1,"XRP-USDT-SWAP":100,"XAU-USDT-SWAP":0.001,
    "PEPE-USDT-SWAP":10000000,"HYPE-USDT-SWAP":0.1,"TURBO-USDT-SWAP":10000,"PI-USDT-SWAP":1,
    "SUI-USDT-SWAP":1,"ADA-USDT-SWAP":100,"BNB-USDT-SWAP":0.01,"LINK-USDT-SWAP":1,
    "BCH-USDT-SWAP":0.1,"AVAX-USDT-SWAP":1,"DOT-USDT-SWAP":1,"LTC-USDT-SWAP":1,
    "NEAR-USDT-SWAP":10,"UNI-USDT-SWAP":1,"OP-USDT-SWAP":1,"ARB-USDT-SWAP":10,
    "APT-USDT-SWAP":1,"ETC-USDT-SWAP":10,"FIL-USDT-SWAP":0.1,"TRX-USDT-SWAP":1000,
    "ATOM-USDT-SWAP":1,"DYDX-USDT-SWAP":1,"GRT-USDT-SWAP":10,"ALGO-USDT-SWAP":10,
    "XLM-USDT-SWAP":100,"HBAR-USDT-SWAP":100,"SHIB-USDT-SWAP":1000000,
    "BONK-USDT-SWAP":100000,"SATS-USDT-SWAP":10000000,"ORDI-USDT-SWAP":0.1,
    "WIF-USDT-SWAP":1,"RENDER-USDT-SWAP":1,"AR-USDT-SWAP":0.1,"BERA-USDT-SWAP":0.1,
    "TAO-USDT-SWAP":0.01,"AAVE-USDT-SWAP":0.1,"ICP-USDT-SWAP":0.01,"OKB-USDT-SWAP":0.01,
    "CRV-USDT-SWAP":1,"INJ-USDT-SWAP":0.1,"HMSTR-USDT-SWAP":100,"TON-USDT-SWAP":1,
    "SEI-USDT-SWAP":10,"MANA-USDT-SWAP":10,"W-USDT-SWAP":1,"STRK-USDT-SWAP":1,
}


async def sync_account(client: OKXClient) -> dict:
    """同步账户资产: OKX → MySQL"""
    print("\n═══ 1. 同步账户资产 ═══")

    resp = await client.get_balance()
    if resp.get("code") != "0":
        print(f"  ❌ 获取余额失败: {resp}")
        return {}

    acct = resp["data"][0]
    total_eq = float(acct.get("totalEq", 0))
    adj_eq = float(acct.get("adjEq", 0) or total_eq)
    avail_eq = float(acct.get("availEq", 0) or 0)

    print(f"  总权益: ${total_eq:.2f}")
    print(f"  调整权益: ${adj_eq:.2f}")

    details = acct.get("details", [])
    for d in details:
        ccy = d.get("ccy", "")
        eq = float(d.get("eq", 0) or 0)
        cash_bal = float(d.get("cashBal", 0) or 0)
        avail_bal = float(d.get("availBal", 0) or 0)
        frozen_bal = float(d.get("frozenBal", 0) or 0)
        upl = float(d.get("upl", 0) or 0)

        await AccountDAO.upsert_asset(
            currency=ccy,
            balance=cash_bal,
            frozen=frozen_bal,
            available=avail_bal,
            usd_value=eq,
            equity=cash_bal,
        )
        print(f"  {ccy}: 余额=${cash_bal:.4f} 可用=${avail_bal:.4f} 权益=${eq:.4f} 冻结=${frozen_bal:.4f} 浮盈=${upl:.4f}")

    # 保存快照
    await AccountDAO.save_snapshot(total_equity=total_eq, available_equity=avail_eq)
    print(f"  ✅ 账户快照已保存")

    return {"total_equity": total_eq, "details": details}


async def sync_positions(client: OKXClient) -> list:
    """同步持仓: OKX → MySQL"""
    print("\n═══ 2. 同步持仓数据 ═══")

    resp = await client.get_positions(inst_type="SWAP")
    if resp.get("code") != "0":
        print(f"  ❌ 获取持仓失败: {resp}")
        return []

    okx_positions = resp.get("data", [])
    print(f"  OKX 实际持仓: {len(okx_positions)} 个")

    # 获取 DB 中的 open 持仓
    db_positions = await PositionDAO.get_open_positions()
    db_map = {(p["inst_id"], p["pos_side"]): p for p in db_positions}
    okx_map = {}

    for pos in okx_positions:
        inst_id = pos.get("instId", "")
        pos_side = pos.get("posSide", "net")
        size = float(pos.get("pos", 0) or 0)
        avg_px = float(pos.get("avgPx", 0) or 0)
        mark_px = float(pos.get("markPx", 0) or 0)
        liq_px = float(pos.get("liqPx", 0) or 0) if pos.get("liqPx") else 0
        upl = float(pos.get("upl", 0) or 0)
        margin = float(pos.get("margin", 0) or 0)
        lever = int(float(pos.get("lever", 1) or 1))
        mgn_mode = pos.get("mgnMode", "isolated")
        ct_val = float(pos.get("ctVal", 1) or 1)
        notional = abs(size) * ct_val * mark_px

        okx_map[(inst_id, pos_side)] = pos

        if abs(size) < 0.0001:
            continue

        # 计算盈亏比例
        pnl_ratio = (upl / margin * 100) if margin > 0 else 0

        print(f"  {'📈' if pos_side == 'long' else '📉'} {inst_id} {pos_side} | "
              f"sz={abs(size)} avg=${avg_px:.4f} mark=${mark_px:.4f} | "
              f"浮盈=${upl:+.2f} ({pnl_ratio:+.1f}%) | "
              f"保证金=${margin:.2f} {lever}x | 名义=${notional:.0f}")

        # 更新或创建 DB 记录
        key = (inst_id, pos_side)
        if key in db_map:
            # 更新现有持仓
            await Database.execute("""
                UPDATE positions SET
                    size=%s, avg_price=%s, current_price=%s, mark_price=%s,
                    liquidation_price=%s, margin=%s, leverage=%s,
                    unrealized_pnl=%s, pnl_ratio=%s, updated_at=NOW()
                WHERE inst_id=%s AND pos_side=%s AND status='open'
            """, (abs(size), avg_px, mark_px, mark_px, liq_px, margin, lever,
                  upl, pnl_ratio, inst_id, pos_side))
        else:
            # 新建持仓记录
            await Database.execute("""
                INSERT INTO positions (inst_id, inst_type, pos_side, size, avg_price,
                    current_price, mark_price, liquidation_price, margin, margin_mode,
                    leverage, unrealized_pnl, pnl_ratio, status, opened_at)
                VALUES (%s, 'SWAP', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open', NOW())
            """, (inst_id, pos_side, abs(size), avg_px, mark_px, mark_px, liq_px,
                  margin, mgn_mode, lever, upl, pnl_ratio))
            print(f"    → 新增 DB 记录")

    # 关闭 DB 中有但 OKX 没有的持仓
    for key, db_pos in db_map.items():
        if key not in okx_map:
            print(f"  ⚠️ {key[0]} {key[1]} DB有但OKX无 → 标记已平仓")
            await Database.execute("""
                UPDATE positions SET status='closed', closed_at=NOW(), unrealized_pnl=0
                WHERE inst_id=%s AND pos_side=%s AND status='open'
            """, (key[0], key[1]))

    print(f"  ✅ 持仓同步完成")
    return okx_positions


async def sync_trades(client: OKXClient, days: int = 3) -> list:
    """同步最近成交: OKX → MySQL"""
    print(f"\n═══ 3. 同步近{days}天成交记录 ═══")

    all_trades = []
    new_count = 0
    updated_count = 0

    # 拉取最近的成交（分多个品种）
    resp = await client.get_order_history(inst_type="SWAP", limit="100")
    if resp.get("code") != "0":
        print(f"  ❌ 获取订单历史失败: {resp}")
        return []

    orders = resp.get("data", [])
    print(f"  OKX 最近订单: {len(orders)} 个")

    total_pnl = 0
    total_fee = 0

    for o in orders:
        state = o.get("state", "")
        if state != "filled":
            continue

        ord_id = o.get("ordId", "")
        inst_id = o.get("instId", "")
        side = o.get("side", "")
        pos_side = o.get("posSide", "")
        fill_px = float(o.get("avgPx", 0) or 0)
        fill_sz = float(o.get("accFillSz", 0) or 0)
        fee = float(o.get("fee", 0) or 0)
        pnl = float(o.get("pnl", 0) or 0)
        ts = int(o.get("fillTime", 0) or o.get("uTime", 0))

        total_pnl += pnl
        total_fee += fee

        if ts > 0:
            traded_at = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        else:
            traded_at = datetime.now(timezone.utc)

        # 检查是否已存在
        existing = await Database.fetch_one(
            "SELECT id FROM orders WHERE ord_id=%s", (ord_id,))

        if existing:
            # 更新 pnl 和 fee
            await Database.execute("""
                UPDATE orders SET pnl=%s, fee=%s, status='filled',
                    filled_price=%s, filled_size=%s WHERE ord_id=%s
            """, (pnl, fee, fill_px, fill_sz, ord_id))
            updated_count += 1
        else:
            await OrderDAO.create_order({
                "ord_id": ord_id, "inst_id": inst_id, "inst_type": "SWAP",
                "ord_type": o.get("ordType", "market"), "side": side,
                "pos_side": pos_side, "size": fill_sz, "price": fill_px,
                "filled_size": fill_sz, "filled_price": fill_px,
                "fee": fee, "pnl": pnl, "status": "filled",
            })
            new_count += 1

        emoji = "📈" if side == "buy" else "📉"
        pnl_emoji = "✅" if pnl >= 0 else "❌" if pnl < 0 else "  "
        print(f"  {emoji} {inst_id:18} {side:4} {pos_side:5} sz={fill_sz} @ ${fill_px:.4f} "
              f"| fee=${fee:.4f} {pnl_emoji} pnl=${pnl:+.4f}")

        all_trades.append(o)

    print(f"\n  总盈亏: ${total_pnl:+.2f} | 总手续费: ${total_fee:+.2f} | 净: ${total_pnl+total_fee:+.2f}")
    print(f"  新增: {new_count} | 更新: {updated_count}")
    print(f"  ✅ 成交同步完成")
    return all_trades


async def sync_daily_pnl() -> None:
    """重新计算今日盈亏并更新 system_config"""
    print("\n═══ 4. 重算今日盈亏 ═══")

    today = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d')

    # 从 trades 表统计
    row = await Database.fetch_one(f"""
        SELECT COALESCE(SUM(pnl), 0) as total_pnl,
               COALESCE(SUM(fee), 0) as total_fee,
               COUNT(*) as trade_count
        FROM trades WHERE traded_at >= '{today}'
    """)

    # 从 positions(closed) 统计
    pos_row = await Database.fetch_one(f"""
        SELECT COALESCE(SUM(realized_pnl), 0) as pos_pnl,
               COUNT(*) as closed_count
        FROM positions WHERE status='closed' AND closed_at >= '{today}'
    """)

    trade_pnl = float(row["total_pnl"]) if row else 0
    trade_fee = float(row["total_fee"]) if row else 0
    pos_pnl = float(pos_row["pos_pnl"]) if pos_row else 0

    print(f"  trades 表: 毛利=${trade_pnl:+.2f} 手续费=${trade_fee:+.2f} 净=${trade_pnl+trade_fee:+.2f}")
    print(f"  positions 表: 已平仓净盈亏=${pos_pnl:+.2f}")

    # 更新 system_config
    daily_pnl = trade_pnl + trade_fee
    from src.data.dao import ConfigDAO
    await ConfigDAO.set("daily_pnl", f"{daily_pnl:.4f}")
    print(f"  ✅ daily_pnl 已更新: ${daily_pnl:+.4f}")


async def generate_report(acct_info: dict, positions: list) -> None:
    """生成校正报告"""
    print("\n" + "=" * 60)
    print("  📊 数据校正报告")
    print("=" * 60)

    total_eq = acct_info.get("total_equity", 0)
    print(f"  账户总权益: ${total_eq:.2f}")
    print(f"  活跃持仓: {len([p for p in positions if abs(float(p.get('pos',0) or 0)) > 0])} 个")

    total_margin = sum(float(p.get("margin", 0) or 0) for p in positions if abs(float(p.get('pos',0) or 0)) > 0)
    total_upl = sum(float(p.get("upl", 0) or 0) for p in positions if abs(float(p.get('pos',0) or 0)) > 0)
    margin_pct = (total_margin / total_eq * 100) if total_eq > 0 else 0

    print(f"  总保证金: ${total_margin:.2f} ({margin_pct:.1f}%)")
    print(f"  总浮盈: ${total_upl:+.2f}")
    print(f"  可用余额: ${total_eq - total_margin:+.2f}")
    print(f"\n  ⏰ 校正时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)


async def main():
    parser = argparse.ArgumentParser(description="OKX 数据校正同步")
    parser.add_argument("--module", choices=["account", "positions", "trades", "all"],
                        default="all", help="同步模块")
    parser.add_argument("--days", type=int, default=3, help="成交记录天数")
    args = parser.parse_args()

    print("🔄 OKX 数据校正同步开始...")
    print(f"   模块: {args.module} | 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    await Database.init_pool()
    auth = OKXAuth(
        api_key=os.getenv("OKX_API_KEY", ""),
        secret_key=os.getenv("OKX_SECRET_KEY", ""),
        passphrase=os.getenv("OKX_PASSPHRASE", ""),
        simulated=False,
    )
    client = OKXClient(auth, simulated=False)

    try:
        acct_info = {}
        positions = []

        if args.module in ("account", "all"):
            acct_info = await sync_account(client)

        if args.module in ("positions", "all"):
            positions = await sync_positions(client)

        if args.module in ("trades", "all"):
            await sync_trades(client, args.days)

        if args.module == "all":
            await sync_daily_pnl()
            await generate_report(acct_info, positions)

        print("\n✅ 数据校正完成！前端刷新即可看到最新数据。")

    except Exception as e:
        print(f"\n❌ 同步失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await client.close()
        await Database.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
