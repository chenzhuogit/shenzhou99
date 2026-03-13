# 神州99 (Shenzhou99)

🚀 加密货币量化交易平台 — 纯趋势交易策略

## 架构

```
Strategy Engine → Risk Engine → Core Engine → Exchange Gateway → OKX V5 API
                                                ↕
                                          Data Pipeline (WebSocket)
```

## 核心策略

**纯趋势交易** — 不预测，只跟随

1. **有没有趋势？** ADX > 25 = 有趋势
2. **方向是什么？** 4H + 1H EMA 排列同向
3. **好入场点？** 回调到 EMA 附近（< 1.5 ATR）

任何一个不明确 → 不交易

## 交易品种

BTC, ETH, SOL, DOGE, ATOM, HMSTR, BCH, W, STRK, TON, SEI, MANA, ADA, OP, INJ

全部经过 90 天历史回测验证

## 风控

- 止损：4H EMA50 下方（趋势线不破不出）
- 熔断：日亏 ≥$50 自动停止
- 动态杠杆：3x-10x（信心+波动率+盈亏比联动）
- 动态仓位：12%-30%（信心驱动）
- 连亏减仓：连亏2次×0.7，连亏3次×0.5
- DeepSeek AI 否决权：连续3次观望 = 禁止开仓

## 技术栈

- Python 3.11+ / asyncio
- OKX V5 REST + WebSocket API
- MySQL (阿里云 RDS)
- aiohttp Web Dashboard (port 8899)
- DeepSeek V3.2 策略顾问

## 前端

实时控制台：`http://server:8899`

## 运行

```bash
# 启动
bash scripts/start.sh

# 重启
bash scripts/restart.sh

# 回测
PYTHONPATH=. python3 src/backtest/backtest_engine.py --all --days 90
```

## License

Private — 神州99 团队内部使用
