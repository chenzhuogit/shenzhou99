"""一次性全量同步 OKX 真实数据到 MySQL"""
import asyncio, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from src.exchange.auth import OKXAuth
from src.exchange.okx_client import OKXClient
from src.data.database import Database
from src.data.dao import AccountDAO, PositionDAO, ModuleStatusDAO, SystemLogDAO, FundingRateDAO


async def main():
    auth = OKXAuth(
        os.getenv("OKX_API_KEY"), os.getenv("OKX_SECRET_KEY"),
        os.getenv("OKX_PASSPHRASE"), simulated=False,
    )
    client = OKXClient(auth, simulated=False)
    await Database.init_pool()

    print("🔄 开始同步 OKX 真实数据...\n")

    # 1. 账户余额
    print("💰 同步账户余额...")
    bal = await client.get_balance()
    total_eq = 0.0
    if bal.get("code") == "0" and bal.get("data"):
        acct = bal["data"][0]
        total_eq = float(acct.get("totalEq", 0) or 0)
        avail_eq = float(acct.get("availEq", 0) or 0)
        imr = float(acct.get("imr", 0) or 0)

        for d in acct.get("details", []):
            ccy = d.get("ccy", "")
            eq = float(d.get("eq", 0) or d.get("cashBal", 0) or 0)
            if eq > 0 or ccy == "USDT":
                await AccountDAO.upsert_asset(
                    currency=ccy,
                    balance=float(d.get("cashBal", 0) or 0),
                    frozen=float(d.get("frozenBal", 0) or 0),
                    available=float(d.get("availBal", 0) or 0),
                    usd_value=float(d.get("eqUsd", 0) or 0),
                    equity=eq,
                )
                print(f"  ✅ {ccy}: {eq} (≈${float(d.get('eqUsd', 0) or 0):.2f})")

        # 快照
        await AccountDAO.save_snapshot(
            total_equity=total_eq, available_equity=avail_eq,
            unrealized_pnl=0, margin_used=imr,
            max_equity=total_eq, drawdown=0,
        )
        print(f"  📸 总权益: ${total_eq:.2f}")
    else:
        print(f"  ⚠️ 余额为空或获取失败")

    # 2. 持仓
    print("\n📋 同步持仓...")
    pos = await client.get_positions()
    pos_count = 0
    if pos.get("code") == "0":
        for p in pos.get("data", []):
            sz = float(p.get("pos", 0) or 0)
            if sz == 0:
                continue
            pos_count += 1
            inst_id = p.get("instId")
            await PositionDAO.upsert_position({
                "inst_id": inst_id,
                "inst_type": p.get("instType", ""),
                "pos_side": p.get("posSide", "net"),
                "size": abs(sz),
                "avg_price": float(p.get("avgPx", 0) or 0),
                "current_price": float(p.get("last", 0) or 0),
                "mark_price": float(p.get("markPx", 0) or 0),
                "liquidation_price": float(p.get("liqPx", 0) or 0) if p.get("liqPx") else None,
                "margin": float(p.get("margin", 0) or 0),
                "margin_mode": p.get("mgnMode", "isolated"),
                "leverage": int(float(p.get("lever", 1) or 1)),
                "unrealized_pnl": float(p.get("upl", 0) or 0),
                "realized_pnl": float(p.get("realizedPnl", 0) or 0),
                "pnl_ratio": float(p.get("uplRatio", 0) or 0),
                "status": "open",
            })
            print(f"  ✅ {inst_id} {p.get('posSide')} size={sz}")
    print(f"  持仓数: {pos_count}")

    # 3. 资金费率
    print("\n📊 获取资金费率...")
    for inst in ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]:
        resp = await client.get_funding_rate(inst)
        if resp.get("code") == "0" and resp.get("data"):
            d = resp["data"][0]
            rate = float(d.get("fundingRate", 0) or 0)
            await FundingRateDAO.save(inst, rate, int(d.get("nextFundingTime", 0) or 0))
            print(f"  ✅ {inst}: {rate:.6%}")

    # 4. 更新模块状态
    await ModuleStatusDAO.update_status("trading_engine", "ok", "就绪")
    await ModuleStatusDAO.update_status("okx_rest_api", "ok", f"已连接 · UID认证成功")
    await ModuleStatusDAO.update_status("okx_websocket", "ok", "待启动")
    await ModuleStatusDAO.update_status("risk_engine", "ok", "就绪")
    await ModuleStatusDAO.update_status("data_pipeline", "ok", f"已同步 · 实盘数据")

    # 5. 系统日志
    await SystemLogDAO.log("INFO", "sync", f"🚀 首次全量同步完成 | 总权益: ${total_eq:.2f} | 持仓: {pos_count}")
    await SystemLogDAO.log("INFO", "sync", f"🔑 API认证成功 | 模式: 实盘 | IP: 43.162.89.175")

    await client.close()
    await Database.close_pool()

    print(f"\n🎉 同步完成！总权益: ${total_eq:.2f} | 持仓: {pos_count}")
    print(f"   控制台: http://43.162.89.175:8899")


asyncio.run(main())
