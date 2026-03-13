"""神州99 主启动脚本 — Web服务 + 数据同步"""
import os
import sys
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from aiohttp import web
from loguru import logger

from src.data.database import Database
from src.data.sync_service import OKXSyncService


async def main():
    passphrase = os.getenv("OKX_PASSPHRASE", "")
    if not passphrase or passphrase == "NEED_TO_SET":
        logger.error("❌ 请在 .env 中设置 OKX_PASSPHRASE")
        print("\n❌ 错误：OKX_PASSPHRASE 未设置！")
        print("   请编辑 .env 文件，填入创建 API Key 时设置的密码短语\n")
        sys.exit(1)

    # 导入 web app（需要在 dotenv 加载之后）
    # 这里直接启动 web server 的 app
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "web"))
    from server import app, PORT

    # 启动同步服务
    sync = OKXSyncService()

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logger.info(f"🚀 神州99 已启动")
    logger.info(f"   控制台: http://0.0.0.0:{PORT}")
    logger.info(f"   模式: {'模拟盘' if sync.simulated else '🔴 实盘'}")

    try:
        await sync.start()
    except KeyboardInterrupt:
        pass
    finally:
        await sync.stop()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
