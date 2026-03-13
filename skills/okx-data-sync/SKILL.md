---
name: okx-data-sync
description: OKX 数据校正同步工具。从 OKX API 拉取真实账户/持仓/成交数据，校正 MySQL 中的记录，确保前端显示的数据与交易所一致。用于：数据不一致修复、持仓对账、盈亏重算、账户资产校准。触发词："数据校正"、"同步数据"、"对账"、"数据不一致"、"持仓不对"、"盈亏不对"。
---

# OKX 数据校正同步

## 快速使用

```bash
cd /root/.openclaw/workspace/shenzhou99
python3 skills/okx-data-sync/scripts/sync.py              # 全量同步
python3 skills/okx-data-sync/scripts/sync.py --module account   # 仅账户
python3 skills/okx-data-sync/scripts/sync.py --module positions # 仅持仓
python3 skills/okx-data-sync/scripts/sync.py --module trades    # 仅成交
```

## 同步流程

1. **账户资产** — `GET /api/v5/account/balance` → `account_assets` 表 + 快照
2. **持仓数据** — `GET /api/v5/account/positions` → `positions` 表 (open/closed 状态校正)
3. **成交记录** — `GET /api/v5/trade/orders-history-archive` → `orders` + `trades` 表
4. **日盈亏重算** — 从 trades 表 SUM(pnl+fee) → `system_config.daily_pnl`

## 校正逻辑

- DB 有但 OKX 无的持仓 → 自动标记 `closed`
- OKX 有但 DB 无的持仓 → 自动新建记录
- 价格/保证金/浮盈 → 以 OKX markPx 为准覆盖
- 成交记录 → 已存在的更新 pnl/fee，不存在的新建

## 数据库表结构

详见 [references/db-schema.md](references/db-schema.md)

## 注意事项

- 脚本依赖项目 `.env` 中的 OKX API 密钥和 MySQL 配置
- 不会修改止损/止盈设置（这些由引擎管理）
- 不会执行任何交易操作（只读 + 写DB）
- 前端刷新即可看到更新后的数据
