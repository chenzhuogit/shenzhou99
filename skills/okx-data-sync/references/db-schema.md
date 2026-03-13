# 数据库表结构

## 数据流向

```
OKX API → sync.py → MySQL → web/server.py → 前端
```

## 核心表

### account_assets
| 列 | 类型 | 说明 |
|---|---|---|
| currency | varchar | 币种 (USDT) |
| balance | decimal | 余额 |
| frozen | decimal | 冻结 |
| available | decimal | 可用 |
| usd_value | decimal | USD估值 |
| equity | decimal | 权益 |

### positions
| 列 | 类型 | 说明 |
|---|---|---|
| inst_id | varchar | 合约ID |
| pos_side | varchar | long/short |
| size | decimal | 张数 |
| avg_price | decimal | 均价 |
| current_price | decimal | 现价 |
| mark_price | decimal | 标记价 |
| margin | decimal | 保证金 |
| leverage | int | 杠杆 |
| unrealized_pnl | decimal | 浮盈 |
| realized_pnl | decimal | 已实现盈亏(净) |
| stop_loss | decimal | 止损价 |
| take_profit | decimal | 止盈价 |
| status | varchar | open/closed |

### trades
| 列 | 类型 | 说明 |
|---|---|---|
| trade_id | varchar | 成交ID |
| ord_id | varchar | 订单ID |
| inst_id | varchar | 合约ID |
| side | varchar | buy/sell |
| pos_side | varchar | long/short |
| price | decimal | 成交价 |
| size | decimal | 数量 |
| fee | decimal | 手续费 |
| pnl | decimal | 盈亏 |

## OKX API 对应

| 数据 | API 端点 | 频率 |
|---|---|---|
| 账户余额 | GET /api/v5/account/balance | 每次同步 |
| 持仓 | GET /api/v5/account/positions | 每次同步 |
| 历史订单 | GET /api/v5/trade/orders-history-archive | 每次同步 |
| K线 | GET /api/v5/market/candles | 引擎自动 |
| Ticker | WS tickers | 实时推送 |
